#!/usr/bin/env python3
"""
General-purpose WEDA deployment tool: deploy any Docker Compose stack to any device and/or
get a live, verified tunnel to a port on it. Talks to the real WEDA Container Management
and Tunnel Management APIs — container-agnostic, not specific to NeuVector or any product.

Three ways to run it:
  python3 run_deploy.py deploy \\
      --domain <domain> --tenant <tenant> --token-file /tmp/weda.jwt \\
      --device-id <deviceId> --compose-file <path> --stack-name <name>
      # just deploy the stack and confirm it's running, nothing else

  python3 run_deploy.py tunnel \\
      --domain <domain> --tenant <tenant> --token-file /tmp/weda.jwt \\
      --device-id <deviceId> --port <port> --application-host <ip>
      # just open a tunnel and hand back the live link, nothing else

  python3 run_deploy.py \\
      --domain <domain> --tenant <tenant> --token-file /tmp/weda.jwt \\
      --device-id <deviceId> --compose-file <path> --stack-name <name> \\
      --port <port> --application-host <ip> [--org-id <orgId>] [--service-type http]
      # no subcommand: the full deploy + tunnel + verify flow, with a PASS/FAIL
      # report — this is what maps one-for-one to
      # WEDA-Deployment-E2E-Test-Scenario.md (doc-id E2E-WEDA-DEPLOY-001) if you need
      # a formal sign-off record

See SKILL.md and REFERENCE.md in this skill for the real-hardware gotchas this script encodes
(stale-revision loops, applicationHost pitfalls, tunnel lifetime limits) before hand-writing any
of these calls yourself.
"""
import argparse
import base64
import http.client
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parent / "report"
UNVERIFIED_SSL = ssl.create_default_context()
UNVERIFIED_SSL.check_hostname = False
UNVERIFIED_SSL.verify_mode = ssl.CERT_NONE

# Confirmed against the official weda-tunnel-mgmt skill (verified live, 2026-09-04):
# sessionStatus can come back as an int OR a string. Comparing only against string
# literals ("ESTABLISHED" etc.) silently breaks polling on any environment/version that
# returns the numeric form — normalize before comparing, always.
SESSION_STATUS_BY_CODE = {0: "ESTABLISHING", 1: "ESTABLISHED", 2: "TERMINATING", 3: "TERMINATED"}


def normalize_session_status(value):
    if isinstance(value, int):
        return SESSION_STATUS_BY_CODE.get(value, str(value))
    return value


class Step:
    def __init__(self, step_id, description):
        self.id = step_id
        self.description = description
        self.passed = None
        self.expected = None
        self.actual = None
        self.notes = []

    def ok(self, actual="", *notes):
        self.passed = True
        self.actual = actual
        self.notes.extend(notes)
        print(f"[PASS] {self.id} — {self.description}")

    def fail(self, expected, actual, *notes):
        self.passed = False
        self.expected = expected
        self.actual = actual
        self.notes.extend(notes)
        print(f"[FAIL] {self.id} — {self.description}", file=sys.stderr)
        print(f"       expected: {expected}", file=sys.stderr)
        print(f"       actual:   {actual}", file=sys.stderr)


def api_request(method, url, token, body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, context=UNVERIFIED_SSL, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw.decode(errors="replace")}
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        # Network-level failure (DNS, connection refused, timeout) — not an HTTP error
        # response, so it never hits HTTPError above. Surface it as a clean pseudo-status
        # instead of letting the traceback kill the whole run.
        return 0, {"network_error": f"{type(e).__name__}: {e}"}


def decode_jwt(token):
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


class OidcSession:
    """WISE-SSO OIDC login -> WEDA JWT, self-contained (no dependency on the
    weda-login skill/repo being present on disk). Ported directly from that
    skill's weda_login.sh — see its header comment for the 5-step protocol this
    mirrors, if this ever needs re-syncing against a spec change. Holds cookie
    state across the 5 steps the way curl's cookie jar does for that script."""

    def __init__(self, base_url, timeout=30):
        self.base = base_url
        self.timeout = timeout
        self.cookies = {}

    def _request(self, method, url, body=None, headers=None):
        u = urllib.parse.urlsplit(url)
        conn = http.client.HTTPSConnection(u.netloc, timeout=self.timeout,
                                            context=UNVERIFIED_SSL)
        path = u.path + (("?" + u.query) if u.query else "")
        hdrs = dict(headers or {})
        if self.cookies:
            hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        try:
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            resp_headers = resp.getheaders()
            for name, value in resp_headers:
                if name.lower() == "set-cookie":
                    m = re.match(r"\s*([^=;]+)=([^;]*)", value)
                    if m:
                        self.cookies[m.group(1)] = m.group(2)
            return resp.status, resp_headers, data
        finally:
            conn.close()

    def _location(self, headers):
        return next((v for k, v in headers if k.lower() == "location"), None)

    def _follow_until_done(self, url):
        """GET, following redirects (curl -L), carrying cookies at each hop."""
        for _ in range(10):
            status, headers, body = self._request("GET", url)
            if status not in (301, 302, 303, 307, 308):
                return status, body
            loc = self._location(headers)
            if not loc:
                raise RuntimeError("redirect with no Location header")
            url = loc if loc.startswith("http") else f"{self.base}{loc}"
        raise RuntimeError("too many redirects")

    def login(self, tenant, app, sso, account, password):
        return_url = f"{self.base}/{tenant}/{app}"
        ext_login = (f"{self.base}/{tenant}/portal/external-login"
                     f"?returnUrl={urllib.parse.quote(return_url, safe='')}")

        status, headers, _ = self._request("GET", ext_login)
        if status != 302:
            raise RuntimeError(f"step 1: expected 302, got {status}")
        loc = self._location(headers)
        acr = urllib.parse.unquote(
            urllib.parse.parse_qs(urllib.parse.urlsplit(loc or "").query).get("acr_values", [""])[0])
        tid_match = re.search(r"tenantId:([a-f0-9\-]+)", acr, re.I)
        if not tid_match:
            raise RuntimeError("step 1: could not extract tenantId from acr_values")
        tenant_id = tid_match.group(1)

        login_body = json.dumps({"account": account, "password": password,
                                  "tenantId": tenant_id}).encode()
        status, _, _ = self._request("POST", f"{self.base}/{sso}/api/v1.0/account/login",
                                      body=login_body, headers={"Content-Type": "application/json"})
        if status != 200:
            raise RuntimeError(f"step 2: expected 200, got {status} "
                                "(bad credentials/tenantId, or wrong sso path)")
        if "Identity.Permission" not in self.cookies:
            raise RuntimeError("step 2: no Identity.Permission cookie in response")

        status, html_bytes = self._follow_until_done(ext_login)
        if status != 200:
            raise RuntimeError(f"step 3: expected 200, got {status}")
        html = html_bytes.decode(errors="replace")
        action_match = re.search(r'<form[^>]*action=["\']([^"\']*)["\']', html, re.I)
        form_action = action_match.group(1) if action_match else ""
        if not form_action:
            raise RuntimeError("step 3: no <form action> in HTML (login may have failed)")
        fields = dict(re.findall(
            r'<input[^>]*name=["\']([^"\']*)["\'][^>]*value=["\']([^"\']*)["\']', html, re.I))
        code = fields.get("code", "")
        if not code:
            raise RuntimeError("step 3: no 'code' hidden field in OIDC form")

        form_url = form_action if form_action.startswith("http") else f"{self.base}{form_action}"
        form_body = urllib.parse.urlencode(
            {"code": code, "state": fields.get("state", ""), "iss": fields.get("iss", "")}).encode()
        status, _, _ = self._request("POST", form_url, body=form_body,
                                      headers={"Content-Type": "application/x-www-form-urlencoded"})
        if status not in (200, 301, 302, 303):
            raise RuntimeError(f"step 4: unexpected status {status}")
        if "AdvOidc.Login" not in self.cookies:
            raise RuntimeError("step 4: no AdvOidc.Login cookie set (OIDC handshake failed)")

        status, _, token_body = self._request(
            "POST", f"{self.base}/{tenant}/{app}/api/v1/auth/tokens", body=b"",
            headers={"Content-Type": "application/json"})
        if status != 201:
            raise RuntimeError(f"step 5: expected 201, got {status}: {token_body[:300]!r}")
        parsed = json.loads(token_body)
        access = parsed.get("accessToken") or parsed.get("token")
        if not access:
            raise RuntimeError("step 5: response had no accessToken/token field")
        return access


def refresh_token_if_needed(token_path, domain, tenant):
    """Mint a fresh JWT via the built-in WISE-SSO login when the one at token_path
    is missing or expired — self-contained, no external script/repo dependency.
    Credentials come only from WEDA_LOGIN/WEDA_PASSWORD, which must already be
    exported in the calling shell; never read from argv or a file."""
    p = Path(token_path)
    if p.exists():
        try:
            if decode_jwt(p.read_text().strip()).get("exp", 0) > time.time() + 60:
                return
        except Exception:
            pass
    account = os.environ.get("WEDA_LOGIN")
    password = os.environ.get("WEDA_PASSWORD")
    if not account or not password:
        print("ERROR: no valid token. Export WEDA_LOGIN and WEDA_PASSWORD in this shell "
              "first (never as script arguments), then rerun.", file=sys.stderr)
        sys.exit(1)
    print(f"No valid token at {p} — logging in as {account}...")
    host = domain.replace("https://", "").replace("http://", "").rstrip("/")
    session = OidcSession(f"https://{host}")
    try:
        access_token = session.login(tenant, "weda", "identity", account, password)
    except RuntimeError as e:
        print(f"ERROR: login failed: {e}", file=sys.stderr)
        sys.exit(1)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(access_token)
    p.chmod(0o600)
    print("Token minted.")


def resolve_org_id(token, org_id_arg):
    claims = decode_jwt(token)
    if claims.get("exp", 0) < time.time():
        print("ERROR: token expired. Get a fresh one from weda-login.", file=sys.stderr)
        sys.exit(1)
    if org_id_arg:
        return org_id_arg
    resource_access = claims.get("resource_access") or {}
    if not resource_access:
        print("ERROR: token has no resource_access — no org membership at all. "
              "An org admin must add this account; a new token of the same account "
              "will not fix this.", file=sys.stderr)
        sys.exit(1)
    if len(resource_access) > 1:
        print("ERROR: multiple orgs in token, pass --org-id explicitly:", file=sys.stderr)
        for oid, info in resource_access.items():
            print(f"  {oid}  ({info.get('orgName')})", file=sys.stderr)
        sys.exit(1)
    org_id, info = next(iter(resource_access.items()))
    print(f"Using org {org_id} ({info.get('orgName')})")
    return org_id


def warn_if_device_id_mismatch(device_id):
    """If this script is running directly on a WEDA-managed device (local docker
    access to a 'dmagent' container), cross-check --device-id against what dmagent
    itself reports as its own device ID. A wrong --device-id here doesn't fail fast —
    it just targets a different, unreachable-from-here device and silently times out
    minutes later waiting for a deploy/tunnel that dmagent on THIS machine never even
    receives. Warn immediately instead. Best-effort only: no local docker/dmagent
    access (e.g. running remotely) just skips the check, it's not an error on its own."""
    try:
        result = subprocess.run(
            ["docker", "logs", "dmagent"],
            capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return
        match = re.search(r'"deviceId":"([a-f0-9]+)"', result.stdout + result.stderr)
        if match and match.group(1) != device_id:
            print(f"WARNING: --device-id {device_id} does not match this machine's own "
                  f"dmagent-reported device ID ({match.group(1)}). If you meant to target "
                  f"THIS machine, use --device-id {match.group(1)} instead.",
                  file=sys.stderr)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass


def real_tls_check(host, port, timeout=10):
    """One real request through the tunnel — the check that actually proves relay.

    Container-agnostic (per SKILL.md: proves "answers something over TLS/TCP", not
    specifically HTTPS) — tries plain HTTP first since that's the more common case for
    an arbitrary deployed service, then falls back to a TLS-wrapped request for
    HTTPS-only targets like NeuVector. A plain-text request against a TLS-only server
    fails fast (RST or garbage bytes) rather than hanging, so trying plain first costs
    little even when the target turns out to need TLS.
    """
    plain_ok, plain_err = _plain_http_check(host, port, timeout)
    if plain_ok:
        return True, None
    tls_ok, tls_err = _tls_http_check(host, port, timeout)
    if tls_ok:
        return True, None
    return False, f"plain: {plain_err}; tls: {tls_err}"


def _plain_http_check(host, port, timeout):
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        try:
            sock.send(b"HEAD / HTTP/1.0\r\n\r\n")
            reply = sock.recv(64)
        finally:
            sock.close()
        return bool(reply), None
    except (socket.timeout, ConnectionResetError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


def _tls_http_check(host, port, timeout):
    try:
        raw_sock = socket.create_connection((host, port), timeout=timeout)
        try:
            tls_sock = UNVERIFIED_SSL.wrap_socket(raw_sock, server_hostname=host)
            tls_sock.send(b"HEAD / HTTP/1.0\r\n\r\n")
            reply = tls_sock.recv(64)
            tls_sock.close()
        finally:
            raw_sock.close()
        return bool(reply), None
    except (socket.timeout, ConnectionResetError, ssl.SSLError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


def write_report(name, lines):
    REPORT_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPORT_DIR / f"{ts}-{name}.md"
    path.write_text("\n".join(lines))
    print(f"\nReport written: {path}")
    return path


def open_tunnel_and_verify(base_url, token, device_id, application_host, port, service_type):
    """Create a tunnel session, poll it to ESTABLISHED, confirm it stays up across a
    short stability recheck, and confirm a real TLS request actually relays through
    it — the same sequence the full flow uses for E2E-2.1/2.2, pulled out so a
    standalone 'tunnel' command (or anything else) can reuse it directly instead of
    re-implementing it. Returns (session_id, tunnel_endpoint, host, err) — err is
    None on success, a human-readable string on failure."""
    status, resp = api_request(
        "POST", f"{base_url}/api/v1/devices/{device_id}/tunnels/sessions", token,
        {"serviceType": service_type, "applicationHost": application_host,
         "applicationPort": port, "purpose": "weda-deploy-skill", "tags": ["e2e"]})
    if status != 202:
        return None, None, None, f"create failed {status}: {resp}"
    session_id = resp["tunnelSessionId"]

    session_status = None
    for _ in range(20):
        status, resp = api_request(
            "GET", f"{base_url}/api/v1/devices/{device_id}/tunnels/sessions/{session_id}", token)
        session_status = normalize_session_status(resp.get("sessionStatus"))
        if session_status != "ESTABLISHING":
            break
        time.sleep(2)
    if session_status != "ESTABLISHED":
        return None, None, None, f"did not reach ESTABLISHED: {session_status}: {resp}"

    tunnel_endpoint = resp["tunnelEndpoint"]
    for _ in range(3):
        time.sleep(1)
        status, resp = api_request(
            "GET", f"{base_url}/api/v1/devices/{device_id}/tunnels/sessions/{session_id}", token)
        if normalize_session_status(resp.get("sessionStatus")) != "ESTABLISHED":
            return None, None, None, f"left ESTABLISHED during stability recheck: {resp}"

    host, _, port_str = tunnel_endpoint.rpartition(":")
    relay_ok, relay_err = real_tls_check(host, int(port_str))
    if not relay_ok:
        return None, None, None, f"real relay check failed: {relay_err}"

    return session_id, tunnel_endpoint, host, None


def _deploy_stack(base_url, token, org_id, device_id, stack_name, b64_compose):
    """One deploy attempt: create-or-redeploy the stack config, deploy it, and wait
    up to 300s for a container to confirm running. Returns (ok, existing_was_reused,
    last_seen_item) — never exits the process, so the caller can decide whether to
    retry under a new name."""
    status, resp = api_request(
        "GET", f"{base_url}/api/v1/orgs/{org_id}/stack-configs?stackName={stack_name}", token)
    if status != 200:
        print(f"ERROR: could not check for existing stack config: {status}: {resp}",
              file=sys.stderr)
        sys.exit(1)
    existing = (resp.get("items") or [None])[0]

    if existing is None:
        status, resp = api_request(
            "POST", f"{base_url}/api/v1/orgs/{org_id}/stack-configs", token,
            {"stackName": stack_name, "type": "compose", "composeFileContent": b64_compose,
             "environment": {}, "description": "Deployed by weda-deploy-skill"})
        if status not in (200, 201):
            print(f"ERROR: could not create stack config: {status}: {resp}", file=sys.stderr)
            sys.exit(1)
        stack_config_id, stack_revision_id = resp["stackConfigId"], resp["stackRevisionId"]
    else:
        stack_config_id = existing["stackConfigId"]
        status, resp = api_request(
            "PATCH", f"{base_url}/api/v1/orgs/{org_id}/stack-configs/{stack_config_id}", token,
            {"composeFileContent": b64_compose, "environment": {},
             "description": "Redeployed by weda-deploy-skill"})
        if status != 200:
            print(f"ERROR: could not update stack config: {status}: {resp}", file=sys.stderr)
            sys.exit(1)
        stack_revision_id = resp["stackRevisionId"]

        # Undeploy before redeploying — same reasoning as the full flow's E2E-1.2:
        # gives dmagent a moment to clear the old deployment record before the new
        # :deploy call lands, reducing the odds of the stale-revision race.
        undeploy_status, undeploy_resp = api_request(
            "DELETE", f"{base_url}/api/v1/devices/{device_id}/docker/stacks"
                      f"?stackConfigId={stack_config_id}", token)
        if undeploy_status not in (204, 404):
            print(f"WARNING: undeploy returned {undeploy_status}: {undeploy_resp} "
                  "(continuing anyway)", file=sys.stderr)
        time.sleep(3)

    status, resp = api_request(
        "POST", f"{base_url}/api/v1/orgs/{org_id}/stack-configs/{stack_config_id}/"
                f"revisions/{stack_revision_id}:deploy", token, {"deviceIds": [device_id]})
    if status != 200:
        print(f"ERROR: could not deploy stack: {status}: {resp}", file=sys.stderr)
        sys.exit(1)

    last_seen = None
    for i in range(60):
        status, resp = api_request(
            "GET", f"{base_url}/api/v1/devices/{device_id}/docker/stacks", token)
        if status == 200:
            for item in resp.get("items", []):
                # Match the just-deployed revision specifically, not just the stack name —
                # on a redeploy, the OLD revision's container is usually still 'running'
                # under the same stackName for a few seconds, which would otherwise pass
                # this check immediately on the stale container (exactly the "running stack
                # matches stackRevisionId, not a stale one" criterion REFERENCE.md requires,
                # and exactly the case the stale-revision retry below is meant to catch —
                # matching on name alone made it almost never fire).
                if (item.get("stackName") == stack_name
                        and item.get("stackRevisionId") == stack_revision_id
                        and item.get("containers")):
                    last_seen = item
                    if any(c.get("state") == "running" for c in item["containers"]):
                        print(f"'{stack_name}' confirmed running after {i * 5}s "
                              f"({len(item['containers'])} container(s)), "
                              f"revision {stack_revision_id}.")
                        return True, existing is not None, last_seen
        if i > 0 and i % 6 == 0:
            print(f"...still waiting for '{stack_name}' to come up ({i * 5}s so far)")
        time.sleep(5)
    return False, existing is not None, last_seen


def cmd_deploy(args):
    """Deploy a compose stack and confirm it's actually running — nothing else,
    no tunnel. Standalone version of the full flow's E2E-1.1-1.3, with the same
    stale-revision auto-retry: if redeploying under an existing name never comes
    up, retries once under a brand-new name before giving up."""
    warn_if_device_id_mismatch(args.device_id)
    refresh_token_if_needed(args.token_file, args.domain, args.tenant)
    token = Path(args.token_file).read_text().strip()
    base_url = f"https://{args.domain}/{args.tenant}/weda"
    org_id = resolve_org_id(token, args.org_id)

    compose_content = Path(args.compose_file).read_text()
    b64 = base64.b64encode(compose_content.encode()).decode()
    stack_name = args.stack_name

    for attempt in range(2):
        ok, reused_existing, last_seen = _deploy_stack(
            base_url, token, org_id, args.device_id, stack_name, b64)
        if ok:
            return
        if attempt == 0 and reused_existing:
            retry_name = f"{args.stack_name}-r{int(time.time())}"
            print(f"'{stack_name}' looks like the stale-revision churn bug described in "
                  f"REFERENCE.md (reused an existing stack config, container never came up) "
                  f"— retrying once under a brand-new name '{retry_name}' instead of "
                  f"failing outright.")
            stack_name = retry_name
            continue
        if last_seen is None:
            print(f"ERROR: '{stack_name}' never appeared in the device's stack list within "
                  f"300s.", file=sys.stderr)
        else:
            states = ", ".join(
                f"{c.get('name', '?')}={c.get('state', '?')}" for c in last_seen["containers"])
            print(f"ERROR: '{stack_name}' appeared but no container reached 'running' within "
                  f"300s — last observed state(s): {states}.", file=sys.stderr)
        sys.exit(1)


def cmd_tunnel(args):
    """Open a tunnel to a device/port and hand back the live endpoint — nothing
    else, no deploy. WEDA tunnels auto-close after 120s idle with no way to
    extend one, so this keeps running (a light keep-alive every 30s) until
    interrupted, then closes the tunnel explicitly."""
    warn_if_device_id_mismatch(args.device_id)
    refresh_token_if_needed(args.token_file, args.domain, args.tenant)
    token = Path(args.token_file).read_text().strip()
    base_url = f"https://{args.domain}/{args.tenant}/weda"

    session_id, tunnel_endpoint, host, err = open_tunnel_and_verify(
        base_url, token, args.device_id, args.application_host, args.port, args.service_type)
    if err:
        print(f"ERROR: could not establish a working tunnel: {err}", file=sys.stderr)
        sys.exit(1)
    print(f"Tunnel ready: {tunnel_endpoint}")
    print("Keeping it alive with a light request every 30s — Ctrl+C to close it "
          "explicitly when you're done with it.")
    try:
        while True:
            time.sleep(30)
            real_tls_check(host, int(tunnel_endpoint.rpartition(':')[2]), timeout=5)
    except KeyboardInterrupt:
        pass
    finally:
        api_request(
            "DELETE", f"{base_url}/api/v1/devices/{args.device_id}/tunnels/sessions/{session_id}",
            token, {"reason": "weda-deploy-skill tunnel command exiting", "graceful": True,
                    "forceTimeout": 30})
        print("\nTunnel closed.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", choices=["deploy", "tunnel"], default=None,
                     help="deploy: just deploy the compose stack and confirm it's running, "
                          "nothing else. tunnel: open a tunnel to a device/port and hand back "
                          "the live link, nothing else. Omit entirely for the full "
                          "deploy + tunnel + verify flow (the original behavior).")
    ap.add_argument("--domain", required=True)
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--token-file", required=True, help="Path to a JWT file (never inline)")
    ap.add_argument("--device-id", required=True)
    ap.add_argument("--org-id")
    ap.add_argument("--compose-file", help="Required for the full flow and 'deploy'")
    ap.add_argument("--stack-name",
                     help="Compose project name — use a NEW one if a prior name for this "
                          "device has hit the stale-revision loop described in REFERENCE.md. "
                          "Required for the full flow and 'deploy'")
    ap.add_argument("--port", type=int, help="Required for the full flow and 'tunnel'")
    ap.add_argument("--application-host",
                     help="See REFERENCE.md E2E-2.1 — 127.0.0.1 is usually wrong. Required for "
                          "the full flow and 'tunnel'")
    ap.add_argument("--service-type", default="http")
    args = ap.parse_args()

    if args.command == "deploy":
        if not args.compose_file or not args.stack_name:
            ap.error("--compose-file and --stack-name are required for 'deploy'")
        cmd_deploy(args)
        return
    if args.command == "tunnel":
        if not args.application_host or not args.port:
            ap.error("--application-host and --port are required for 'tunnel'")
        cmd_tunnel(args)
        return
    if not all([args.compose_file, args.stack_name, args.port, args.application_host]):
        ap.error("--compose-file, --stack-name, --port, and --application-host are all "
                 "required for the full deploy+tunnel+verify flow")

    warn_if_device_id_mismatch(args.device_id)
    refresh_token_if_needed(args.token_file, args.domain, args.tenant)
    token = Path(args.token_file).read_text().strip()
    base_url = f"https://{args.domain}/{args.tenant}/weda"
    org_id = resolve_org_id(token, args.org_id)
    steps = []

    compose_content = Path(args.compose_file).read_text()
    b64 = base64.b64encode(compose_content.encode()).decode()
    original_stack_name = args.stack_name
    found = None
    last_seen = None

    for attempt in range(2):
        suffix = "" if attempt == 0 else " (retry)"

        # E2E-1.1
        s = Step("E2E-1.1", f"Create (or identify existing) stack config{suffix}")
        steps.append(s)
        status, resp = api_request(
            "GET", f"{base_url}/api/v1/orgs/{org_id}/stack-configs?stackName={args.stack_name}",
            token)
        if status != 200:
            s.fail("200", f"{status}: {resp}")
            write_report("defect", defect_report(s, args))
            sys.exit(1)
        existing = (resp.get("items") or [None])[0]

        if existing is None:
            status, resp = api_request(
                "POST", f"{base_url}/api/v1/orgs/{org_id}/stack-configs", token,
                {"stackName": args.stack_name, "type": "compose", "composeFileContent": b64,
                 "environment": {}, "description": f"E2E test deploy of {args.stack_name}"})
            if status not in (200, 201):
                s.fail("200/201", f"{status}: {resp}")
                write_report("defect", defect_report(s, args))
                sys.exit(1)
            stack_config_id = resp["stackConfigId"]
            stack_revision_id = resp["stackRevisionId"]
            s.ok(f"created {stack_config_id}")
        else:
            stack_config_id = existing["stackConfigId"]
            status, resp = api_request(
                "PATCH", f"{base_url}/api/v1/orgs/{org_id}/stack-configs/{stack_config_id}",
                token,
                {"composeFileContent": b64, "environment": {},
                 "description": f"E2E test redeploy of {args.stack_name}"})
            if status != 200:
                s.fail("200", f"{status}: {resp}")
                write_report("defect", defect_report(s, args))
                sys.exit(1)
            stack_revision_id = resp["stackRevisionId"]
            s.ok(f"existing config {stack_config_id}, new revision {stack_revision_id}")

            # Always undeploy before redeploying — see REFERENCE.md E2E-1.2. A short pause
            # after the DELETE (confirmed necessary in real testing, not just theoretical)
            # gives dmagent a moment to actually clear the old deployment record before the
            # new :deploy call lands, reducing the odds of the stale-revision race.
            undeploy_status, undeploy_resp = api_request(
                "DELETE",
                f"{base_url}/api/v1/devices/{args.device_id}/docker/stacks"
                f"?stackConfigId={stack_config_id}", token)
            if undeploy_status not in (204, 404):
                print(f"WARNING: undeploy returned {undeploy_status}: {undeploy_resp} "
                      "(continuing anyway)", file=sys.stderr)
            time.sleep(3)

        # E2E-1.2
        s = Step("E2E-1.2", f"Deploy the revision to the device{suffix}")
        steps.append(s)
        status, resp = api_request(
            "POST", f"{base_url}/api/v1/orgs/{org_id}/stack-configs/{stack_config_id}/"
                    f"revisions/{stack_revision_id}:deploy", token,
            {"deviceIds": [args.device_id]})
        if status != 200:
            s.fail("200", f"{status}: {resp}")
            write_report("defect", defect_report(s, args))
            sys.exit(1)
        s.ok("deploy accepted")

        # E2E-1.3
        s = Step("E2E-1.3", f"Confirm containers are actually running{suffix}")
        steps.append(s)
        found = None
        last_seen = None
        # 60 x 5s = 300s, not the original 100s — dmagent can fall behind under load
        # (repeated "context deadline exceeded" in its own logs) and take several minutes
        # to actually apply a deploy it already accepted; bailing out early in that state
        # fails on a problem that would have resolved itself given more time.
        for i in range(60):
            status, resp = api_request(
                "GET", f"{base_url}/api/v1/devices/{args.device_id}/docker/stacks", token)
            if status == 200:
                for item in resp.get("items", []):
                    # Match the just-deployed revision, not just the stack name — on a
                    # redeploy the OLD revision's container is usually still 'running' under
                    # the same stackName for a few seconds, which would otherwise satisfy
                    # this check immediately on the stale container (see the identical fix
                    # and its reasoning in _deploy_stack, used by the standalone `deploy`
                    # subcommand).
                    if (item.get("stackName") == args.stack_name
                            and item.get("stackRevisionId") == stack_revision_id
                            and item.get("containers")):
                        last_seen = item
                        running = [c for c in item["containers"] if c.get("state") == "running"]
                        if running:
                            found = item
                            break
            if found:
                break
            if i > 0 and i % 6 == 0:
                print(f"...still waiting for containers to come up ({i * 5}s so far)")
            time.sleep(5)
        if found:
            break

        if attempt == 0 and existing is not None:
            # Only auto-retry when we reused an *existing* stack-config — this is the
            # specific pattern the stale-revision churn bug follows (see REFERENCE.md):
            # redeploying under a name already used on this device can silently never
            # bring the container up. A first-ever deploy under a brand-new name hitting
            # this is a different, genuine problem — not one a retry under yet another
            # name is likely to fix, so that case fails immediately below instead.
            retry_name = f"{original_stack_name}-r{int(time.time())}"
            print(f"'{args.stack_name}' looks like the stale-revision churn bug described "
                  f"in REFERENCE.md (reused an existing stack config, container never came "
                  f"up) — retrying once under a brand-new name '{retry_name}' instead of "
                  f"failing outright.")
            args.stack_name = retry_name
            continue

        if last_seen is None:
            s.fail("non-empty containers array with at least one 'running'",
                   "the stack never appeared in the device's stack list within 300s — "
                   "check the deploy call itself was accepted for this device, or the "
                   "stale-revision loop described in REFERENCE.md")
        else:
            states = ", ".join(
                f"{c.get('name', '?')}={c.get('state', '?')}" for c in last_seen["containers"])
            s.fail("non-empty containers array with at least one 'running'",
                   f"stack appeared but no container reached 'running' within 300s — "
                   f"last observed state(s): {states}. A container stuck in 'created' "
                   f"usually means it failed to actually start (e.g. a port/IP already in "
                   f"use, or a bad image) — check the compose file for a hardcoded static "
                   f"IP or port that could collide with something else already running on "
                   f"this device.")
        write_report("defect", defect_report(s, args))
        sys.exit(1)
    s.ok(f"{len(found['containers'])} container(s), stackConfigId={found['stackConfigId']}")

    # E2E-2.1 / E2E-2.2 — open and verify the tunnel
    s21 = Step("E2E-2.1", "Open a tunnel session")
    s22 = Step("E2E-2.2", "Poll until ESTABLISHED and verify real relay")
    steps.append(s21)
    steps.append(s22)
    status, resp = api_request(
        "POST", f"{base_url}/api/v1/devices/{args.device_id}/tunnels/sessions", token,
        {"serviceType": args.service_type, "applicationHost": args.application_host,
         "applicationPort": args.port, "purpose": "weda-deploy-skill", "tags": ["e2e"]})
    if status != 202:
        s21.fail("202", f"{status}: {resp}")
        write_report("defect", defect_report(s21, args))
        sys.exit(1)
    session_id = resp["tunnelSessionId"]
    s21.ok(f"session {session_id} created")

    session_status = None
    for i in range(20):
        status, resp = api_request(
            "GET", f"{base_url}/api/v1/devices/{args.device_id}/tunnels/sessions/{session_id}",
            token)
        session_status = normalize_session_status(resp.get("sessionStatus"))
        if session_status != "ESTABLISHING":
            break
        time.sleep(2)

    if session_status != "ESTABLISHED":
        s22.fail("ESTABLISHED", f"{session_status}: {resp}")
        write_report("defect", defect_report(s22, args))
        sys.exit(1)

    tunnel_endpoint = resp["tunnelEndpoint"]
    still_ok = True
    for i in range(3):
        time.sleep(1)
        status, resp = api_request(
            "GET", f"{base_url}/api/v1/devices/{args.device_id}/tunnels/sessions/{session_id}",
            token)
        if normalize_session_status(resp.get("sessionStatus")) != "ESTABLISHED":
            still_ok = False
            break
    if not still_ok:
        s22.fail("stays ESTABLISHED across 3 rechecks", f"left ESTABLISHED: {resp}")
        write_report("defect", defect_report(s22, args))
        sys.exit(1)

    host, _, port_str = tunnel_endpoint.rpartition(":")
    relay_ok, relay_err = real_tls_check(host, int(port_str))
    if not relay_ok:
        s22.fail("real TLS request succeeds through the tunnel", relay_err)
        write_report("defect", defect_report(s22, args))
        sys.exit(1)
    s22.ok(f"tunnelEndpoint={tunnel_endpoint}, real relay confirmed")

    # E2E-2.3 — close the tunnel
    s = Step("E2E-2.3", "Close the tunnel")
    steps.append(s)
    status, resp = api_request(
        "DELETE", f"{base_url}/api/v1/devices/{args.device_id}/tunnels/sessions/{session_id}",
        token, {"reason": "E2E test complete", "graceful": True, "forceTimeout": 30})
    if status != 202:
        s.fail("202", f"{status}: {resp}")
        write_report("defect", defect_report(s, args))
        sys.exit(1)
    time.sleep(2)
    ok, err = real_tls_check(host, int(port_str), timeout=5)
    if ok:
        s.fail("subsequent request fails to connect", "connection still succeeded")
        write_report("defect", defect_report(s, args))
        sys.exit(1)
    s.ok("session terminated, endpoint unreachable")

    print("\nAll steps PASSED.")
    write_report("signoff", signoff_report(steps, args, tunnel_endpoint))


def defect_report(step, args):
    return [
        f"Title: [WEDA Deploy] {step.id} failed — {step.description}",
        "",
        f"Environment: {args.domain}/{args.tenant}",
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
        f"Step: {step.id}",
        f"Device: {args.device_id}",
        "",
        "Expected:",
        f"  {step.expected}",
        "",
        "Actual:",
        f"  {step.actual}",
        "",
        "Notes:",
        *[f"  - {n}" for n in step.notes],
    ]


def signoff_report(steps, args, tunnel_endpoint):
    lines = [
        "# WEDA-Deployment-E2E-Test-Scenario.md — Sign-off",
        "",
        f"Environment: {args.domain}/{args.tenant}",
        f"Device: {args.device_id}",
        f"Tester: (automated, run_deploy.py)",
        f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
        f"tunnelEndpoint used: {tunnel_endpoint}",
        "",
        "| # | Criterion | Steps | Result |",
        "|---|---|---|---|",
    ]
    criteria = [
        ("1", "Stack config created or correctly identified for redeploy", "E2E-1.1"),
        ("2", "Deploy call accepted", "E2E-1.2"),
        ("3", "Containers confirmed actually running", "E2E-1.3"),
        ("4", "Tunnel opens and reaches ESTABLISHED", "E2E-2.1, 2.2"),
        ("5", "Tunnel can be closed explicitly", "E2E-2.3"),
    ]
    for num, crit, step_ids in criteria:
        lines.append(f"| {num} | {crit} | {step_ids} | PASS |")
    lines.append("")
    lines.append("Verdict: PASS")
    return lines


if __name__ == "__main__":
    main()
