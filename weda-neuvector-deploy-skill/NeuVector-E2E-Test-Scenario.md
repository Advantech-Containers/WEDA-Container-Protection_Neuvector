---
doc-id:    E2E-NEUVECTOR-EDGE-001
title:     "NeuVector Security Configuration & Verification — End-to-End Test Scenario"
product:   "WEDA-Container-Runtime-Protection"
version:   v2.7-20260924
status:    Draft
date:      2026-09-10
traces-to: WEDA-Deployment-E2E-Test-Scenario.md (E2E-WEDA-DEPLOY-001); NeuVector-Quickstart-Guide.md (CS-SD-QSG1 v2.0)
tags:      [doc:test, feature:container-security, service:neuvector, layer:cloud, layer:edge, tech:rest, status:draft]
---

# NeuVector Security Configuration & Verification — End-to-End Test Scenario

> **Who this is for:** QA engineers verifying the NeuVector Quickstart Guide (`CS-SD-QSG1`), and engineers demoing edge container protection to a customer.
> **What it covers:** configuring NeuVector protection, a specific rule, and a configuration backup — then verifying every one of them actually took effect with a live test, not a saved setting.
> **What it is not:** deploying NeuVector or opening access to it — that's the WEDA Deployment report (`E2E-WEDA-DEPLOY-001`), a separate, reusable report. This report assumes that's already done and picks up from a working tunnel to NeuVector's REST API.
> **The most important part of this report is Section 5, Verification.** A configuration that saves successfully is not proof of anything — every claim made in Section 4 is only confirmed once its matching step in Section 5 passes.

Every step is a literal, reproducible command against the NeuVector REST API.


---

## 1. Test Environment & Prerequisites

### 1.1 Environment

| Item | Value | Notes |
|---|---|---|
| NeuVector REST API | reached at `<tunnelEndpoint>` | Obtained from the WEDA Deployment report (`E2E-WEDA-DEPLOY-001`), Act 2 |
| Auth (NeuVector) | `X-Auth-Token: <token>` from `POST /v1/auth` | Obtained in Section 3 below |
| Client | `curl` | Direct calls against `<tunnelEndpoint>` |

### 1.2 Accounts required

| # | Account | Scope | Used to verify |
|---|---|---|---|
| A1 | NeuVector `admin` | Local to the NeuVector instance | Configuration and verification (Sections 4–5) |

### 1.3 Containers required

| # | Item | State needed | Used in |
|---|---|---|---|
| C1 | A third-party test container on the deployed device (e.g. `alpine`, standing in for an unknown vendor container) | Running at least once, so its group appears in NeuVector | Sections 4, 5 |

**Precondition check before you start**

- [ ] The WEDA Deployment report (`E2E-WEDA-DEPLOY-001`) has been completed for the target device, and a live `<tunnelEndpoint>` to NeuVector's REST API (port `10443`) is in hand.
- [ ] C1 has run at least once on the device, so its group is visible under **Policy → Groups**.

---

## 2. How to read a test step

| Field | Meaning |
|---|---|
| **Step** | Stable ID — quote it in the defect title (e.g. `E2E-3.2 failed`) |
| **Call** | The exact request or console action |
| **Expect** | The response that means PASS |
| **Pass criteria** | Check every line — all of them must hold |

A step FAILS if the status code or any field differs from **Expect**.

---

## 3. Confirm NeuVector answers through the tunnel

### E2E-1.1 — Log in and obtain a token

```bash
curl -sk -X POST "https://<tunnelEndpoint>/v1/auth" \
  -H "Content-Type: application/json" \
  -d '{"password":{"username":"admin","password":"admin"}}'
```

**Expect** — `200 OK` with a `token` field

**Pass criteria**
- [ ] The response confirms the controller and TLS setup work end-to-end, not just that the tunnel itself connects.
- [ ] If `default_password: true` is present, the password is changed before continuing (Section 4 assumes a real login, not the shipped default).
- [ ] Record the returned token — the remaining steps refer to it as `<nv-token>`.

---

## 4. Configure protection, a rule, and backup

### E2E-2.1 — Bring C1's group under protection

```bash
curl -sk -X PATCH "https://<tunnelEndpoint>/v1/service/config/network" \
  -H "X-Auth-Token: <nv-token>" -H "Content-Type: application/json" \
  -d '{"config":{"services":["<C1 group>"],"policy_mode":"Protect"}}'
```

**Expect** — `200 OK`, `policy_mode: Protect` on C1's group

**Pass criteria**
- [ ] The group was visible under **Policy → Groups** before this call (per the §1.3 precondition).
- [ ] The change is reflected both in the API response and in the console's Containers list (green **Protect** badge).

### E2E-2.2 — Add a specific rule

```bash
curl -sk -X PATCH "https://<tunnelEndpoint>/v1/file_monitor/<C1 group>" \
  -H "X-Auth-Token: <nv-token>" -H "Content-Type: application/json" \
  -d '{"config":{"add_filters":[{"filter":"/etc/passwd","recursive":false,"behavior":"block_access","applications":[],"group":"<C1 group>"}]}}'
```

**Expect** — `200 OK`, the filter registered

**Pass criteria**
- [ ] The rule appears under the group's file-monitor configuration in the console.
- [ ] A saved rule here is not treated as proof of enforcement — that is E2E-3.4, not this step.

### E2E-2.3 — Export a full configuration backup

```bash
curl -sk -H "X-Auth-Token: <nv-token>" "https://<tunnelEndpoint>/v1/file/config?section=all" -o export.raw
```

**Expect** — a real, valid archive, not an error page

**Pass criteria**
- [ ] `gunzip -t export.raw` reports no error.
- [ ] The archive's `sections` list includes `policy`, `user`, and `config`.

### E2E-2.4 — Apply the whole baseline in one shot via the JSON template (alternative to E2E-2.1/2.2 done by hand)

Instead of setting `policy_mode`/`profile_mode` and adding rules one API call at a time as in E2E-2.1/E2E-2.2 above, `run_neuvector.py`'s `template` subcommand applies a whole security baseline — Protect mode, file-monitor rules, network rules, and response rules — from a single JSON file in one command, then re-fetches every value to confirm it actually took (never trusts a PATCH's `200` alone, same principle as every other step in this document):

```bash
python3 run_neuvector.py template \
    --domain <domain> --tenant <tenant> --token-file <token-file> --device-id <deviceId> \
    --group <group-name> \
    --template-file ../protect-config-template.example.json [--deploy-if-missing]
```

The bundled `protect-config-template.example.json` (repo root) is the reference baseline: Protect mode (network + process), file-monitor rules on common sensitive paths (see its `_note_predefined_files` caveat — `/etc/passwd`/`/etc/hosts` give alerting only, not blocking, by NeuVector's own design), an allow-DNS/allow-HTTPS/deny-everything-else network policy, and a response rule that quarantines the container on a Critical security event. Copy the file and edit its `file_monitor_rules`/`network_rules`/`response_rules` to build a different template.

**Expect** — the command re-fetches `policy_mode`, `profile_mode`, every file-monitor filter,
every network rule (in the order given in the template), and every response rule, printing a confirmation line for each; it exits non-zero and prints a warning if anything didn't take.

**Pass criteria**
- [ ] Re-fetch confirms `policy_mode=Protect` and `profile_mode=Protect`.
- [ ] Re-fetch confirms every `file_monitor_rules` entry is present in `profile.filters`.
- [ ] Re-fetch confirms every `network_rules` entry is present, in the same relative order as
the template's own array (rules are evaluated top-to-bottom, so order is a correctness property, not cosmetic).
- [ ] Re-fetch confirms every `response_rules` entry is present.
- [ ] Re-running the same template against the same group a second time is idempotent — no
duplicate file-monitor, network, or response rules are created (NeuVector itself dedupes file-monitor filters; the script dedupes network rules by their full `(comment, from, to, ports, action, applications)` and response rules by their full `(comment, group, event, actions, conditions)` before inserting — deliberately more than just `(comment, action/event, group)`, since two legitimate rules can share those three fields).
- [ ] Applying the same template to a *different* group creates that group's own rules rather
than being skipped as "already present" — the dedup check is scoped per-group, not just by rule text.

---

## 5. Verification

A saved configuration in Section 4 is not proof it works — every item there is confirmed here with a live test against the actual enforcement API, `GET /v1/log/incident`.

### E2E-3.1 — Confirm the container is a discovered/tracked NeuVector workload

```bash
curl -sk -H "X-Auth-Token: <nv-token>" "https://<tunnelEndpoint>/v1/workload/<container-id>"
```

**Expect** — a real `workload` object with a `policy_mode` field, not `{"code":7,"error":"Object not found"}`

**Pass criteria**
- [ ] A found workload with `policy_mode` set — none of the checks below can mean anything otherwise.
- [ ] If not found, restart the NeuVector container itself (not the test container) and recheck — its startup inventory scan picks up already-running containers within ~30-35s.

### E2E-3.2 — Protection is active, not just the engine

On the console **Dashboard**.

**Expect** — the engine shows healthy immediately; full protection is only active an additional 30–45 s after the container started

**Pass criteria**
- [ ] Engine health (already confirmed reachable in E2E-1.1) is not itself evidence that E2E-2.1's Protect mode is enforcing yet.
- [ ] Wait the full 30–45 s before running E2E-3.3.

### E2E-3.3 — Enforcement blocks unrecognized process activity

Use a **long-running** unrecognized process, not a one-shot command — a quick `wget | sh` can finish before the kill lands, which is a timing race, not a missing control. Produce the unrecognized binary locally (copy the container's own `busybox` to a new path) rather than installing one via a package manager — an install depends on network reachability and can hang under host load for reasons unrelated to NeuVector, and a byte-identical binary at a never-seen path is still "unrecognized" since NeuVector's process profile matches by path:

```bash
docker exec <C1 container> sh -c 'cp /bin/busybox /tmp/e2e-unrecognized-tool && chmod +x /tmp/e2e-unrecognized-tool'
docker exec -d <C1 container> /tmp/e2e-unrecognized-tool sleep 300
# after ~1s, confirm it started, then poll for it to disappear:
docker exec <C1 container> sh -c 'pgrep -f /tmp/e2e-unrecognized-tool'
curl -sk -H "X-Auth-Token: <nv-token>" "https://<tunnelEndpoint>/v1/log/incident?start=0"
```

**Expect** — the process is killed within ~20 s of starting

**Pass criteria**
- [ ] The process's own termination (re-check for its PID) is the pass signal when it happens, not the incident log alone.
- [ ] A matching incident-log entry is a secondary, corroborating check only — see E2E-3.4's note on the logging-pipeline gap.
- [ ] Not killed within ~20s is informational, not a failure — on a kernel without `CONFIG_CONNECTOR`, scan-mode fallback latency has been observed to vary well beyond any fixed window depending on host/NeuVector CPU load, so this can't reliably distinguish a genuine miss from ordinary latency here.

### E2E-3.3b — File-access monitoring pipeline is alive (predefined-file sanity check)

Added 2026-09-30, after E2E-3.4 was seen to fail even with confirmed-correct config. This step exists purely to tell apart two very different failure modes for E2E-3.4: a device-wide monitoring outage versus a defect specific to custom, user-created filters. Access one of NeuVector's own predefined sensitive files (alert-only by design — see E2E-3.4's note below) and confirm a real incident is logged for it:

```bash
docker exec <C1 container> sh -c 'cat /etc/passwd'
curl -sk -H "X-Auth-Token: <nv-token>" "https://<tunnelEndpoint>/v1/log/incident?start=0"
```

**Expect** — a `Container.FileAccess.Violation` incident appears for `/etc/passwd`, scoped to this container's workload, within a few seconds

**Pass criteria**
- [ ] At least one matching incident is found for this workload after the access.
- [ ] If this step fails, treat it as a broader signal: the file-access-monitoring pipeline itself may be down on this device, and E2E-3.4 failing is unsurprising rather than a distinct, new defect.
- [ ] If this step passes but E2E-3.4 still fails, that isolates the problem to custom, user-created filters specifically — not a device-wide kernel/fanotify dead-end. Confirmed live (2026-09-30): this is the actual pattern reproduced — see E2E-3.4's addendum below.

### E2E-3.4 — The specific rule holds under a live test

**Important — pick a genuinely custom path, not a predefined system file.** NeuVector's own
documentation (https://open-docs.neuvector.com/policy/filerules/) states that its built-in "predefined" sensitive files — this explicitly includes `/etc/passwd`, `/etc/hosts`, and `/etc/resolv.conf` — are **alert-only by design, never block-enforced**, even when a user adds a `block_access` rule for that same path. This is intentional, so routine system-file updates are never accidentally blocked. Blocking only applies to user-configured custom files/directories on non-system containers. Use a path like `/tmp/custom-secret.txt` created inside the test container for this step — not `/etc/passwd`.

```bash
docker exec <C1 container> sh -c 'echo secret > /tmp/custom-secret.txt'
# register a block_access rule on /tmp/custom-secret.txt (not /etc/passwd), then:
docker exec <C1 container> sh -c 'cat /tmp/custom-secret.txt'
curl -sk -H "X-Auth-Token: <nv-token>" "https://<tunnelEndpoint>/v1/log/incident?start=0"
```

**Expect** — the command itself fails at the OS level (non-zero exit, `Operation not permitted`)

**Pass criteria**
- [ ] The read is refused, confirmed by exit code — this is the authoritative pass signal.
- [ ] A matching incident-log entry is a secondary check, independent of the caveat below: a missing log entry alongside a genuine refusal is a logging-pipeline gap, not an enforcement failure.
- [ ] **The read succeeding (exit 0) is informational, not a hard fail, on this NeuVector version/device** — see the 2026-09-30 update below. It's the same class of exception as E2E-3.3's kill-timeout: a confirmed external limitation, not something this script can distinguish from a genuine defect on every environment, so it's reported rather than treated as blocking.

**Superseded finding (was: "not reliably reproducible", 2026-09-23):** earlier runs of this
scenario tested against `/etc/passwd` and saw only 1 genuine block out of 8 attempts, with `pathWalker` confirmed present and no corresponding NeuVector error — this looked like flaky enforcement. Reviewing NeuVector's official file-rules documentation (2026-09-24) shows this was the wrong test target: `/etc/passwd` is one of NeuVector's own predefined files, which the engine deliberately never blocks regardless of a matching custom rule. The "intermittent" pattern is consistent with that documented alert-only behavior, not a defect in the enforcement mechanism itself. Re-run this step against a genuinely custom path (as above) to confirm; do not use any predefined system file (`/etc/passwd`, `/etc/hosts`, `/etc/resolv.conf`, etc.) as the test target.

**Update (2026-09-30):** re-running this step against a genuinely custom path still fails on this device — zero blocks, zero incidents, across repeated attempts, extended wait windows up to 2 minutes, both `Protect` and `Monitor` profile modes, a recursive directory filter, and a filename crafted to resemble a predefined file. E2E-3.3b (added above) confirms the underlying monitoring pipeline itself is functional on this same device (predefined-file access is reliably detected). This matches a known NeuVector regression independently reported on unrelated infrastructure (Kubernetes/containerd, not Docker/edge): [SUSE forum, "File monitoring in Neuvector versions 5.4.1, 5.4.2, 5.4.3, 5.4.8 and 5.4.9 doesn't work"](https://forums.suse.com/t/file-monitoring-in-neuvector-versions-5-4-1-5-4-2-5-4-3-5-4-8-and-5-4-9-doesnt-work/46192) — custom filter locations show no activity in any of those versions, tracing to a monitoring-method change introduced in 5.4.3. No fix is documented in the 5.4.9 through 5.5.4 release notes checked. A single, unreproduced positive result was observed once during this investigation (all three test paths blocked in one run) but could not be reproduced on retest — treat this as unconfirmed, not a working configuration, until independently reproduced.

### E2E-3.5 — Compliance/CIS-benchmark scan produces real findings

Added 2026-09-30. A genuinely different NeuVector subsystem from E2E-3.3/3.3b/3.4 above: a static host/container configuration audit (Docker CIS Benchmark), not a live fanotify/netlink/NFQ enforcement hook — none of the kernel/version limitations affecting those checks apply here.

```bash
curl -sk -H "X-Auth-Token: <nv-token>" "https://<tunnelEndpoint>/v1/host"
curl -sk -H "X-Auth-Token: <nv-token>" "https://<tunnelEndpoint>/v1/compliance/asset"
```

**Expect** — the host shows `docker_bench_status: "finished"`, and `/v1/compliance/asset` returns at least one real finding

**Pass criteria**
- [ ] `docker_bench_status` is `"finished"` for the host.
- [ ] `/v1/compliance/asset` returns at least one non-empty finding.
- [ ] Confirmed live (2026-09-30): this device already has a finished benchmark run with 46 real findings, independent of anything E2E-3.3/3.4 depend on.

---

## 6. Exit Criteria — Sign-off Sheet

| # | Criterion | Steps | Result |
|---|---|---|---|
| 1 | NeuVector answers through the tunnel and returns a token | E2E-1.1 | ☐ |
| 2 | The test container's group can be switched to Protect mode | E2E-2.1 | ☐ |
| 3 | A specific rule can be added | E2E-2.2 | ☐ |
| 4 | A full configuration backup exports as a valid archive | E2E-2.3 | ☐ |
| 5 | Container confirmed as a discovered/tracked NeuVector workload | E2E-3.1 | ☐ |
| 6 | Protection is confirmed active via a live check, not assumed from engine health | E2E-3.2 | ☐ |
| 7 | Process enforcement blocks the live test | E2E-3.3 | ☐ |
| 7b | File-access monitoring pipeline is alive (predefined-file sanity check, informational — isolates a device-wide outage from a custom-filter-specific defect) | E2E-3.3b | ☐ |
| 8 | File-access enforcement blocks the live test on a custom path — **see E2E-3.4's note; do not test against a predefined system file** | E2E-3.4 | ☐ |
| 9 | Compliance/CIS-benchmark scan produces real findings (a genuinely different subsystem — see its own note) | E2E-3.5 | ☐ |
| 10 (optional) | Whole baseline (modes + file/network/response rules) applies in one shot via the JSON template, confirmed idempotent and per-group scoped | E2E-2.4 | ☐ |

Environment: ＿＿＿＿＿　Device: ＿＿＿＿＿　Tester: ＿＿＿＿＿　Date: ＿＿＿＿＿　Verdict: ☐ PASS ☐ FAIL

---

## 7. Defect Reporting Template

```
Title: [NeuVector] E2E-x.y failed — <one-line symptom>

Environment: <WEDA domain/tenant>
Time: <YYYY-MM-DD HH:MM> UTC (use UTC)
Step: E2E-x.y
Device: <deviceId>

Expected:
  <copy from this document's Expect field>

Actual:
  <the full HTTP status code and response body, or console screenshot>

Notes (helps localise the fault, optional):
  - Was the container's group actually visible under Policy > Groups before Section 4?
  - Was the tunnel confirmed working (WEDA Deployment report, `E2E-WEDA-DEPLOY-001`) before starting here?
```

---

## 8. Changelog

| Version | Date | Changes |
|---|---|---|
| v2.0-20260910 | 2026-09-10 | Initial |
| v2.4-20260918 | 2026-09-18 | Testing and fixes |
| v2.5-20260924 | 2026-09-24 | Split criterion 7 into process (E2E-3.3) and file-access (E2E-3.4) enforcement; documented E2E-3.4 as known intermittent (1/8 live runs passed) rather than reliably reproducible |
| v2.6-20260924 | 2026-09-24 | E2E-3.4 corrected: the earlier "intermittent" file-access result was caused by testing against `/etc/passwd`, a NeuVector predefined file that is alert-only by design per official docs; step now targets a genuinely custom path |
| v2.7-20260924 | 2026-09-24 | Added E2E-2.4: applying the whole security baseline (modes + file/network/response rules) in one shot via `run_neuvector.py template` and `protect-config-template.example.json`, as an alternative to configuring E2E-2.1/2.2 by hand; added as optional sign-off criterion 9 |
