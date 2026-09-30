---
name: weda-deploy-skill
description: "Deploy any Docker Compose stack to a WEDA-managed device and confirm it's genuinely reachable through a live tunnel — not just that the deploy call was accepted. Container-agnostic: works for any compose stack and any device, not specific to NeuVector or any other product. Use this whenever you need a container running on a WEDA device with real, confirmed tunnel access to it — a one-off deployment, part of a larger workflow, or to check a device is reachable at all before deploying something specific to it."
---

# weda-deploy-skill — Deploy any container to a WEDA device and confirm it's reachable

Talks to the real WEDA Container Management and Tunnel Management APIs: creates a stack config, deploys it to a device, confirms containers are actually running (not just that the deploy call was accepted), then opens a tunnel session, polls it to `ESTABLISHED`, and verifies real traffic actually relays through it. Its checks line up one-for-one with `WEDA-Deployment-E2E-Test-Scenario.md` (doc-id `E2E-WEDA-DEPLOY-001`) if you need a formal pass/fail record — prints PASS/FAIL per step and a filled sign-off table — but the skill itself is just "deploy this, and prove it works," useful any time you need that, not only for formal test runs.

**What this does not cover**: anything about what the deployed container does once it's
running, or whether it's actually healthy at the application layer — that's a separate, container-specific verification skill (e.g. `weda-neuvector-deploy-skill` for NeuVector). This skill's job ends the moment a tunnel to the target port is proven to relay real traffic; it does not assume any particular thing is listening on that port beyond "answers something over TLS/TCP."

## Self-contained — no dependency on the weda-login skill

```
weda-deploy-skill  →  (optionally) a container-specific verification skill
```

This skill has no external skill/repo dependency: `mint_weda_token()` in `run_deploy.py` is a direct Python port of `weda-login`'s WISE-SSO OIDC flow (the same 5-step protocol, no shelling out to that script or requiring it to exist on disk). It's called automatically whenever `--token-file` is missing or expired. It never reads or touches your password on its own behalf — `WEDA_LOGIN`/`WEDA_PASSWORD` must already be exported in the shell this script runs from; the script reads those two env vars itself (there's no subprocess to inherit them anymore) and fails with a clear message if neither is set and no valid token exists.

A container-specific skill that picks up from here should reference this skill **by name** (`weda-deploy-skill`), not by doc-id or file path — matching the pass-criteria documents' own convention of cross-referencing each other by doc-id rather than filename, so a rename doesn't break the chain.

## Prerequisites — ask fresh every time

| Need | Notes |
|---|---|
| WEDA domain | e.g. `weda-sit-k3s.weda.dev` — no scheme, no trailing slash |
| Tenant | e.g. `central` |
| A JWT, or `WEDA_LOGIN`+`WEDA_PASSWORD` exported | Either point `--token-file` at an existing valid token, or export both env vars and let the script mint one automatically |
| deviceId | Target device, already `Activated` |
| Compose file | The stack to deploy — any valid Docker Compose YAML |
| Target port | The port on the deployed container to prove tunnel access to |

Never reuse a token, domain, or deviceId from a prior run or another skill's documentation examples — tokens are short-lived and org-scoped.

## Usage

Three ways to run it — two lightweight standalone operations, plus the full flow:

```bash
export WEDA_LOGIN='<account>'
export WEDA_PASSWORD='<password>'   # set once in your shell, never as a script argument

# Just deploy the compose stack and confirm it's running — nothing else
python3 scripts/run_deploy.py deploy \
  --domain <domain> --tenant <tenant> --token-file /tmp/weda.jwt \
  --device-id <deviceId> --compose-file <path-to-compose.yml> --stack-name <name>

# Just open a tunnel to a device/port and hand back the live link — nothing else.
# Keeps running (light keep-alive every 30s) until you Ctrl+C, then closes the tunnel.
python3 scripts/run_deploy.py tunnel \
  --domain <domain> --tenant <tenant> --token-file /tmp/weda.jwt \
  --device-id <deviceId> --port <port> --application-host <device-real-ip-or-shared-network-ip>

# No subcommand: the full deploy + tunnel + verify flow, with a PASS/FAIL report
python3 scripts/run_deploy.py \
  --domain <domain> --tenant <tenant> --token-file /tmp/weda.jwt \
  --device-id <deviceId> --compose-file <path-to-compose.yml> \
  --stack-name <name> --port <port> --application-host <device-real-ip-or-shared-network-ip>
```

If `/tmp/weda.jwt` doesn't exist yet or is expired, the script logs in automatically (its own built-in WISE-SSO flow) before doing anything else. If it's already valid, login is skipped entirely. The `deploy` subcommand carries the same stale-revision auto-retry as the full flow.

The full flow (no subcommand) prints each step (`E2E-1.1`, `E2E-1.2`, ...) as it runs, PASS/FAIL, and on any failure writes a filled-in defect report to `scripts/report/<timestamp>-defect.md` using the source document's own template — copy that directly into a bug ticket. On full success, writes a filled sign-off sheet to `scripts/report/<timestamp>-signoff.md`. The `deploy`/`tunnel` subcommands print plainly and don't write a report.

`scripts/report/` is gitignored — it's per-run output, not something to commit.

## Real-hardware gotchas this script encodes (read before hand-writing the API calls)

These were found the hard way running this exact E2E doc against real WEDA/`dmagent` — the doc describes the *intended* behavior; these are the *actual* operational hazards on top of it.

1. **Never PATCH-and-deploy on top of an active deployment.** The doc's own Act 1 warning box
covers this: undeploy before redeploying, or `dmagent` receives conflicting instructions and tears down its own freshly-started containers in a loop. This script does this automatically whenever a stack config with the same name already exists.

2. **`dmagent` can get stuck on stale revision history for a `stackName`, even after the WEDA API
confirms the old deployment and stack config are both deleted.** Symptom: the container repeatedly appears then disappears within seconds of a fresh deploy, and `dmagent`'s own logs (`docker logs dmagent`) show it cycling through **multiple old revision IDs** under the same stack name, each triggering a "Removing stack" action that tears down the just-started new container (since all revisions of one `stackName` share the same Compose project name). This is a `dmagent`-side caching issue, not something fixable via the WEDA API alone. **The script now auto-detects this**: when E2E-1.3 fails to confirm a container running after redeploying under an *existing* stack config (not a first-ever deploy under a brand-new name, which is a different problem this doesn't retry), it automatically retries once under a fresh, timestamp-suffixed `stackName` before giving up. If both attempts fail, the failure report includes the actual observed container state(s) from the WEDA API, not just a bare timeout.

3. **A tunnel closes after 120s of no client connection, or 3600s max regardless of activity —
there is no keep-alive or extend call.** (Confirmed directly against the official `weda-tunnel-mgmt` skill: session metadata, including its lifetime, is fixed at creation and cannot be updated.) This script opens the tunnel and does everything it needs to do with it in one continuous run — it does not hold a tunnel open across separate invocations, and neither should any caller building on top of this skill.

4. **`sessionStatus: ESTABLISHED` is not proof the tunnel actually relays traffic.** A session can
report `ESTABLISHED`/`healthStatus: HEALTHY` for a few seconds and then flip to `UNHEALTHY` and self-terminate, with every real request through it failing the whole time. This script rechecks stability a few times and makes one real TLS request through the tunnel before declaring `E2E-2.2` a pass.

5. **`--application-host` matters more than it looks.** `127.0.0.1` / `localhost` can fail a
tunnel's own health check outright even though the target is genuinely reachable from the device itself. If the target container shares a Docker network with `dmagent` (e.g. via a `networks:` block in the compose file naming `dmagent`'s own network as `external: true`), use that shared-network IP. Otherwise use the device's real, WEDA-visible LAN IP — never a loopback or container-internal-only address.

6. **A wrong `--device-id` doesn't fail fast — it silently times out instead.** If
`--device-id` points at a real WEDA device that just isn't reachable from wherever this script runs, the deploy/tunnel calls succeed at the API level (WEDA accepts them fine) but nothing ever confirms running, because `dmagent` on the *actual* target device never sees this script's own local docker state, and vice versa — it looks identical to real device-side congestion until you've burned the full 300s timeout. When running directly on the device itself, the script cross-checks `--device-id` against this machine's own `dmagent` container (via `docker logs dmagent`) and warns immediately on a mismatch. This only works with local docker access — running against a genuinely remote device from a different machine skips the check silently, which is correct (that's a legitimate use).

For the full endpoint reference and additional troubleshooting, see [REFERENCE.md](REFERENCE.md).
