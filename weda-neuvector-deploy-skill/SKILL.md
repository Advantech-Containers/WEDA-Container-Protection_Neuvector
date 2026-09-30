---
name: weda-neuvector-deploy-skill
description: Run this when asked to verify NeuVector's actual security behavior on a WEDA-deployed device — i.e. to execute NeuVector-E2E-Test-Scenario.md (doc-id E2E-NEUVECTOR-EDGE-001) for real. Configures Protect mode, a specific file-access rule, and a config backup, then proves each one actually took effect with a live test (a saved config is never itself a pass). Use when asked to "run the NeuVector E2E", "verify NeuVector enforcement really works", or to reproduce E2E-1.1 through E2E-3.4 live.
---

# weda-neuvector-deploy-skill — Live NeuVector deploy + configure + verify E2E

Executes `NeuVector-E2E-Test-Scenario.md` end to end, genuinely from scratch: deploys NeuVector itself if it isn't already running (bundled compose, `assets/neuvector-compose.yml`), logs into it through a WEDA tunnel, configures Protect mode and a specific file-access rule, exports a backup, then actually triggers the enforcement (attempts the blocked action for real) and checks the incident log for a matching entry — never trusting a `200` on the config call alone.

## Deploying NeuVector

`ensure_neuvector_deployed()` runs first, before anything else: it checks whether NeuVector is
**actually running on this device** under `--nv-stack-name` (default `weda-neuvector-deploy-skill-nv`,
namespaced to this skill to avoid colliding with an unrelated stack of the same generic name — a stack-config existing is not enough on its own, it must have running containers on this specific device). If not running, it deploys: creating a new stack config if none exists by this name, or deploying the existing one's current revision if it does — never PATCHing or replacing its content. Once genuinely running, it's reused as-is and never redeployed — redeploying an already-active stack risks the stale-revision churn bug documented in `weda-deploy-skill` (`dmagent` can end up tearing down its own freshly-started containers).

The bundled compose pins NeuVector to a static IP (`10.10.0.60`) on `dmagent`'s own Docker network (`digitaltwin_device_mgmt_agent_weda_network`) — this is required, not cosmetic: a second network alongside it breaks NeuVector's internal Consul-based leader election, and cross-bridge traffic to a container's published ports is otherwise blocked by this host's iptables rules. `--application-host` defaults to that same IP; only override it if NeuVector was deployed some other way.

A test container (an "unknown vendor" stand-in) is also handled automatically now: if `--container` (or `--group`, its default) isn't already running locally, this skill deploys a minimal throwaway `alpine` container itself, under a WEDA stack named after `--group`, before opening the tunnel — the same self-sufficiency principle as NeuVector's own deploy. It deliberately doesn't pin a static IP for this container, since it only needs to be discovered by NeuVector on the shared network, not reached from outside — a hardcoded IP would risk colliding with something else already using it. If a container matching `--container` is already running (however it got there — by hand, or via `weda-deploy-skill`), it's reused as-is and nothing new is deployed.

**This skill opens its own tunnel — it does not accept a pre-existing `tunnelEndpoint`.**
That's a deliberate, hard-won design choice, not an oversight: a WEDA tunnel session has no keep-alive and auto-closes after 120s idle (confirmed directly against the tunnel-management API — there is no PATCH to extend one). A `tunnelEndpoint` handed off from a separate `weda-deploy-skill` run is very likely to already be dead by the time this skill's script starts, especially with any human-in-the-loop delay between the two. So instead, this skill calls the exact same create-session → poll-`ESTABLISHED` → verify-real-relay sequence that `weda-deploy-skill` uses (see that skill's `REFERENCE.md` §Act 2 for the full rationale), then immediately runs every NeuVector step through that fresh tunnel in one continuous script execution — login, configure, and the live verification triggers — before the tunnel has any chance to go idle.

This is the sense in which this skill "refers to" `weda-deploy-skill`: it reuses its proven tunnel-opening approach (same API calls, same stability rechecks, same real-relay check), not a literal call-out to its script or a borrowed live session.

## Dependency chain

```
weda-deploy-skill (proves deploy+tunnel work at all, once)
  →  weda-neuvector-deploy-skill (opens its own fresh tunnel, configures + verifies NeuVector)
```

Run `weda-deploy-skill` first to confirm the platform-level deploy/tunnel mechanics work at all on this device. Once that's established, this skill is self-sufficient for repeat NeuVector testing — it re-opens a tunnel itself every time it runs, rather than depending on a long-lived one from the other skill.

This skill has no external skill/repo dependency, including on `weda-login`: `mint_weda_token()` in `run_neuvector.py` is a direct Python port of its WISE-SSO OIDC flow, called automatically whenever `--token-file` is missing or expired. It never reads your password on its own behalf — `WEDA_LOGIN` and `WEDA_PASSWORD` must be exported in the shell first; the script reads those two env vars directly and fails with a clear message if neither a valid token nor both variables are present.

## Prerequisites — ask fresh every time

WEDA domain, tenant, deviceId, and either a valid JWT file or `WEDA_LOGIN`+`WEDA_PASSWORD` exported (so a token can be minted automatically), plus:

| Need | Notes |
|---|---|
| `--org-id` | Only if the token has access to more than one org — `ensure_neuvector_deployed()`'s stack-config calls are org-scoped, unlike the tunnel/NeuVector calls |
| `--group` | A name for the test container's group, e.g. `alpine` — deployed automatically if nothing matching is already running (see "Deploying NeuVector" above) |

`--application-host` and `--nv-stack-name` both have working defaults (see above) — pass them only to override.

## Usage

Five ways to run it — four lightweight standalone operations, plus the full flow:

```bash
export WEDA_LOGIN='<account>'
export WEDA_PASSWORD='<password>'   # set once in your shell, never as a script argument

# Just ensure NeuVector is running on the device — nothing else
python3 scripts/run_neuvector.py deploy \
  --domain <domain> --tenant <tenant> --token-file /tmp/weda.jwt --device-id <deviceId>

# Open a tunnel and hand back the live link — nothing else. Keeps running (light
# keep-alive every 30s) until you Ctrl+C, then closes the tunnel; a WEDA tunnel
# otherwise auto-closes after 120s idle with no way to extend it. --port controls
# WHICH service the tunnel reaches, same ports as the manual install:
#   --port 10443 (default) -> REST API      --port 8443 -> Web UI (browser-usable link)
# Default credentials are admin/admin either way.
python3 scripts/run_neuvector.py tunnel \
  --domain <domain> --tenant <tenant> --token-file /tmp/weda.jwt --device-id <deviceId> \
  [--port 8443]

# Read-only: log in and print NeuVector's current state — no changes made
python3 scripts/run_neuvector.py check \
  --domain <domain> --tenant <tenant> --token-file /tmp/weda.jwt --device-id <deviceId> \
  [--group <group-name>]

# Apply a JSON security-config template (Protect mode, file rules, network rules,
# response rules) to a group, then re-fetch everything to confirm it actually took.
# --deploy-if-missing spins up a throwaway container to protect if --group isn't
# running yet. See ../protect-config-template.example.json for the template
# format — the '_note*' keys in it are plain-JSON comments for a human reader; the
# script itself only reads the real fields and ignores the rest.
python3 scripts/run_neuvector.py template \
  --domain <domain> --tenant <tenant> --token-file /tmp/weda.jwt --device-id <deviceId> \
  --group <container-group> \
  --template-file ../protect-config-template.example.json \
  [--deploy-if-missing]

# No subcommand: the full deploy + configure + live-verify flow, with a PASS/FAIL report
python3 scripts/run_neuvector.py \
  --domain <domain> --tenant <tenant> --token-file /tmp/weda.jwt \
  --device-id <deviceId> --group <test-group-name> \
  [--nv-user admin --nv-password admin]
```

The full flow prints each step (`E2E-1.1`, `E2E-2.1`, ...) PASS/FAIL as it runs. On any failure, writes a filled defect report to `scripts/report/`; on full success, a filled sign-off sheet. `scripts/report/` is gitignored. The four standalone subcommands (`deploy`/`tunnel`/`check`/`template`) print plainly and don't write a report — use the full flow when you need a formal sign-off. `template` is safe to re-run against the same group — it checks what's already applied before inserting anything, so a repeat run is a no-op rather than a pile of duplicate rules.

## Real-hardware gotchas this script encodes

These were found by actually running this doc against a live NeuVector instance — several of the document's own example calls turned out to hit the wrong endpoint or wrong field name. The document itself is being corrected to match; this list is the durable record of *why*.

1. **Protect mode is not a `/v1/group/{name}` field.** NeuVector's real `RESTGroupConfig` schema
(confirmed from source) only has `name`, `criteria`, `cfg_type` — no `policy_mode`. A `PATCH` there with `policy_mode` in the body returns `200` but silently drops the field; Go's JSON unmarshal ignores unknown fields rather than erroring. The real endpoint is:
   ```
   PATCH /v1/service/config/network
   {"config": {"services": ["<group-name-without-nv.-prefix>"], "policy_mode": "Protect"}}
   ```

2. **`profile_mode` is a *separate* field from `policy_mode`, and file-access enforcement depends
on it, not on `policy_mode`.** Setting only `policy_mode: Protect` will make process-profile enforcement work (unrecognized process execution gets blocked) but file-monitor rules will silently never trigger — reads and writes to a filtered path both succeed with no incident logged, even though the filter is correctly registered and `policy_mode` is `Protect`. Fix:
   ```
   PATCH /v1/service/config/profile
   {"config": {"services": ["<group-name>"], "profile_mode": "Protect"}}
   ```
Both calls are needed for the full E2E to actually pass end to end.

3. **Adding a rule via `/v1/policy/rule` is not `{"config": {"rules": [...]}}`.** That shape
returns a generic `400 Request in wrong format` regardless of content — it looks like a body validation failure but is actually a completely different top-level shape. The real one, per NeuVector's own OpenAPI spec (`RESTPolicyRuleActionData`), has **no `config` wrapper at all**:
   ```
   PATCH /v1/policy/rule
   {"insert": {"after": 0, "rules": [{"id": ..., "from": ..., "to": ..., "ports": "any",
                                       "action": "deny", "applications": [...],
                                       "cfg_type": "user_created", ...}]}}
   ```
Rule `id` also has real range constraints: **1–10000** for ordinary rules, **100000–110000** is reserved for federated rules and will be rejected with a specific "ID is reserved for federal rule" error for a non-federal `cfg_type`.

4. **`pathWalker` must actually be present in the deployed image for file-monitor enforcement to
do anything.** On a minimized/stripped edge image missing this component, a `file_monitor` filter registers successfully (`200`, visible via `GET`), `policy_mode`/`profile_mode` both show `Protect`, and the container can be freshly restarted — and reads/writes to the filtered path still succeed with zero enforcement and zero incident log entry. This is not fixable via any API call; it needs the image itself to include the component. If E2E-3.4 fails with a silently-succeeding read/write despite correct config, check the image for this before assuming an API/config mistake.

   **If `pathWalker` is present and config is confirmed correct (E2E-3.3b passes) and E2E-3.4 still
   fails this way, it's likely not an image or config problem at all.** Confirmed live
   (2026-09-30): a *custom* `file_monitor` filter never generates any incident or block, while a
   *predefined* sensitive file (e.g. `/etc/passwd`) reliably does, on the same group, same device,
   same NeuVector build — i.e. the file-monitoring pipeline itself works, but custom user-created
   filters specifically don't fire. This matches a known NeuVector regression independently
   reported on unrelated infrastructure (Kubernetes/containerd, not Docker/edge): [SUSE forum,
   "File monitoring in Neuvector versions 5.4.1, 5.4.2, 5.4.3, 5.4.8 and 5.4.9 doesn't
   work"](https://forums.suse.com/t/file-monitoring-in-neuvector-versions-5-4-1-5-4-2-5-4-3-5-4-8-and-5-4-9-doesnt-work/46192)
   — only a few hardcoded predefined files get monitored, custom filter locations show no
   activity at all, tracing to a monitoring-method change introduced in 5.4.3 that persists
   through 5.4.9. Check whether a version past 5.4.9 fixes this before spending more time
   debugging the deployment itself.

5. **Alert forwarding (`syslog_cfg`) is not part of this E2E** as of the current document version
— confirmed broken in the edge image tested against (config accepts and reports enabled, destination is verified reachable, but no syslog message is ever actually emitted for a real event). Removed from the document's Section 4/5 rather than tested here; re-add both the doc step and this skill's coverage once the underlying delivery mechanism is fixed.

6. **The real syslog field names, if this is ever re-added**, are flat under `config` —
`syslog_ip`, `syslog_port`, `syslog_level` (title-case values like `"Info"`, not `"INFO"`), `syslog_status` (boolean) — not a nested `syslog_cfg` object.

7. **A NeuVector session token expires** (observed ~30 min idle) — if a call suddenly returns
`401`, re-login rather than assuming the tunnel died.

8. **E2E-3.3 needs a long-running process, not a one-shot command — and its kill-timeout is
informational, not a hard fail.** A quick `wget ... | sh`-style invocation can finish before Protect mode's kill lands — a timing race, not a missing control. On a kernel missing `CONFIG_CONNECTOR`, process-change detection falls back from netlink push to a periodic `/proc` scan; both paths feed the same kill pipeline, but observed latency on this fallback varies well beyond any fixed window (5s, 20s, or otherwise) depending on host/NeuVector CPU load at the time. So the script waits ~20s and confirms the process is actually killed (re-check for its PID, not just an incident logged) when it happens — but doesn't fail the step if it doesn't, since that can't be reliably distinguished from ordinary scan-mode latency here. See REFERENCE.md E2E-3.3.

9. **Don't install the unrecognized binary via a package manager (`apk add`, etc.) — copy one
that's already in the container to a new path instead** (`cp /bin/busybox /tmp/some-new-path`). A package-manager install depends on network reachability and can hang indefinitely under host CPU/daemon load — confirmed live, this made E2E-3.3 fail with "process never appeared to begin with" for reasons unrelated to NeuVector at all. `cp`-ing an existing binary to a new path is instant, fully offline, and still counts as "unrecognized" since NeuVector's process profile matches by path, not just by binary content.

10. **A container is invisible to NeuVector until it's discovered as a workload — `GET
/v1/workload/<id>` returning `Object not found` means none of the Section 5 checks can mean anything, regardless of group policy_mode.** Check this first (E2E-3.1). If not found, restart the NeuVector container itself (not the test container) — its startup inventory scan picks up already-running containers within ~30-35s.

11. **A wrong `--device-id` doesn't fail fast — it silently times out instead.** If
`--device-id` points at a real WEDA device that just isn't reachable from wherever this script runs, the deploy/tunnel calls succeed at the API level but nothing ever confirms running, and it looks identical to real device-side congestion until the full 300s timeout is burned. When running directly on the device itself, the script cross-checks `--device-id` against this machine's own `dmagent` container (via `docker logs dmagent`) and warns immediately on a mismatch. Only works with local docker access — a genuinely remote device from a different machine skips the check silently, which is correct.

For the full endpoint reference and Call/Expect/Pass-criteria detail, see [REFERENCE.md](REFERENCE.md).
