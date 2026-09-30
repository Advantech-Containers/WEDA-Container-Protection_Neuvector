# weda-neuvector-deploy-skill — Reference

Mirrors `NeuVector-E2E-Test-Scenario.md` (doc-id `E2E-NEUVECTOR-EDGE-001`, current version `v2.7-20260924` — see that document's changelog). If this file and the source document disagree on a Section 3/4/5 step, the document is authoritative — with one deliberate exception: the document is written for a human following a manual checklist and assumes NeuVector is already deployed; this skill additionally deploys NeuVector itself if it isn't already running (see `SKILL.md` §"Deploying NeuVector"), so it works standalone without a separate deployment step or skill. That's an addition for automation, not a correction to the document's own scope.

All calls below go through the tunnel this skill opens itself (see `SKILL.md` for why it doesn't accept a pre-existing `tunnelEndpoint`). Steps are numbered exactly as in the source document.

---

## Section 3 — Confirm NeuVector answers through the tunnel

### E2E-1.1 — Log in and obtain a token

```
POST /v1/auth
{"password": {"username": "admin", "password": "admin"}}
```
**Expect** — `200`, a `token` field.

**Pass criteria**
- Confirms controller + TLS, not just that the tunnel connects.
- If `default_password: true`, flag it — a real deployment should change this before continuing.
- NeuVector session tokens expire (~30 min observed) — a sudden `401` mid-run means re-login, not
a dead tunnel.

---

## Section 4 — Configure protection, a rule, and backup

### E2E-2.1 — Bring the test group under Protect mode

**The document's literal example endpoint, `PATCH /v1/service/config/network`, is correct** — but
two things must both be true for the *effects* tested in Section 5 to actually hold:

```
PATCH /v1/service/config/network
{"config": {"services": ["<group>"], "policy_mode": "Protect"}}
```
**and**
```
PATCH /v1/service/config/profile
{"config": {"services": ["<group>"], "profile_mode": "Protect"}}
```

`<group>` is the service name **without** the `nv.` prefix that appears in the group list (e.g. group `nv.alpine` → service name `alpine`).

**Expect** — `200` on both; `GET /v1/group/nv.<group>` subsequently shows both `policy_mode:
"Protect"` and `profile_mode: "Protect"`.

**Pass criteria**
- The group was visible under Policy → Groups beforehand.
- Both fields, not just `policy_mode`, read back as `Protect` — E2E-3.4 (file-monitor) depends on
`profile_mode` specifically, not `policy_mode`.
- `PATCH /v1/group/<name>` (a different, plausible-looking endpoint) does **not** work for this —
its real schema has no `policy_mode` field at all and silently drops it. Don't use it.

### E2E-2.2 — Add a specific rule

Two variants exist in the wild; know which one you're testing:

**(a) Network policy rule** (block traffic from the group):
```
PATCH /v1/policy/rule
{"insert": {"after": 0, "rules": [{
    "id": <1-10000 for user_created>, "comment": "...",
    "from": "<group>", "to": "external", "ports": "any", "action": "deny",
    "applications": ["any"], "learned": false, "disable": false,
    "created_timestamp": 0, "last_modified_timestamp": 0,
    "cfg_type": "user_created", "priority": 0, "match_counter": 0,
    "last_match_timestamp": 0
}]}}
```
No `"config"` wrapper — this is the whole body. `id` range matters: 1–10000 for `user_created`, 100000–110000 is federal-reserved and will be rejected for any other `cfg_type`.

**(b) File-monitor rule** (the document's actual E2E-2.2 target — block a specific file path):
```
PATCH /v1/file_monitor/<full-group-name-with-nv.-prefix>
{"config": {"add_filters": [{
    "filter": "/etc/passwd", "recursive": false, "behavior": "block_access",
    "applications": [], "group": "<full-group-name-with-nv.-prefix>"
}]}}
```
**Note:** `/etc/passwd` is one of NeuVector's own "predefined" sensitive files
(https://open-docs.neuvector.com/policy/filerules/), which the engine deliberately only alerts on — it never block-enforces a predefined path, even with a matching `block_access` rule like this one. Registering the filter here is still valid (it proves the rule mechanism itself works and gives audit-log coverage), but don't expect a live read of `/etc/passwd` to actually be blocked — that's what E2E-3.4 below tests with a genuinely custom path instead.

**Expect** — `200` on either; for (b), `GET /v1/file_monitor/<group>` shows the filter in
`profile.filters`.

**Pass criteria**
- A registered filter/rule is not itself proof of enforcement — that's E2E-3.3/3.4, not this step.
- If (b) returns `400 Request in wrong format` with `"duplicate filter"` in the message, that means
the filter is already registered from a prior run — treat as PASS, not a new failure.

### E2E-2.3 — Export a full configuration backup

```
GET /v1/file/config?section=all
```
**Expect** — a real `multipart/form-data` body whose one part is `Content-Type: application/x-gzip`.
Extract that part's bytes (after the boundary/header, strip the trailing `\r\n` before the closing boundary) — the raw HTTP response itself is **not** a bare gzip file.

**Pass criteria**
- `gunzip -t` on the extracted part reports no error.
- The decompressed JSON's leading `{"version", "kv_version", ..., "sections": [...]}` header lists
`policy`, `user`, and `config`.

### E2E-2.4 — Apply the whole baseline in one shot via the JSON template (`template` subcommand)

Alternative to configuring E2E-2.1/2.2 by hand: `run_neuvector.py template --template-file ../protect-config-template.example.json` applies `policy_mode`/`profile_mode` (as in E2E-2.1's calls, both endpoints), `file_monitor_rules` (E2E-2.2's `PATCH /v1/file_monitor/<group>`, repeated per rule), `network_rules` via `PATCH /v1/policy/rule` with `{"insert": {"after": 0, "rules": [...]}}` (no `"config"` wrapper — see the network-rule format note under E2E-2.2(a)), and `response_rules` via the same `insert` pattern against `PATCH /v1/response/rule`.

**Idempotency:** file-monitor filters are deduped by NeuVector itself. Network and response
rules are deduped by this script before inserting — on a network rule's full `(comment, from, to, ports, action, applications)`, and a response rule's full `(comment, group, event, actions, conditions)` — deliberately more than just `(comment, action-or-event, group)`, since two legitimate rules can share those three fields (e.g. two allow rules from the same group to two different destinations) and a narrower key either skips a rule that should be inserted or falsely confirms the wrong rule as proof this one was applied. Re-applying the same template to the same group creates nothing new; applying it to a
*different* group still creates that group's own rules (the dedup check resolves `{group}` to
the real group name before comparing, so it never matches across groups).

**Pass criteria**
- Re-fetching `GET /v1/service/config/network` + `/config/profile`, `GET
/v1/file_monitor/<group>`, `GET /v1/policy/rule`, and `GET /v1/response/rule` after the command confirms every value from the template is present.
- Network rule IDs come back in the same relative order as the template's own array (top-to-bottom
evaluation order is a correctness property here, not cosmetic).
- A second run against the same group creates zero duplicate rules; a first run against a
different group is not incorrectly skipped as "already present".

---

## Section 5 — Verification (the most important part — do not skip)

### E2E-3.1 — Confirm the container is a discovered/tracked NeuVector workload

Check this **before** anything else in Section 5:
```
GET /v1/workload/<container-id>
```
**Expect** — a real `workload` object with a `policy_mode` field, not `{"code":7,"error":"Object not found"}`.

**Pass criteria**
- A found workload with `policy_mode` set is required before E2E-3.2 onward can mean anything.
- If not found: restart the NeuVector container itself (not the test container) and recheck —
its startup inventory scan picks up already-running containers within ~30-35s.

### E2E-3.2 — Wait for enforcement to actually activate

Setting `policy_mode`/`profile_mode` to `Protect` is not instant. Observed: wait at least 40s before E2E-3.3/3.4, and if the target container was already running *before* Protect mode was set, **restart it** — file-monitor enforcement in particular has been observed to only attach at container-start time, not retroactively to an already-running container. (This restart is a plain `docker restart`, which keeps the same container ID — it does not undo E2E-3.1's discovery.)

### E2E-3.3 — Process enforcement blocks unrecognized activity

Use a **long-running** unrecognized process, not a one-shot command (`wget ... | sh` finishes in under a second and can complete before the kill lands — a timing race, not a missing control; see Pass criteria below). Produce the unrecognized binary by copying the container's own `busybox` to a new path — fully local and offline, no network call or package manager (see gotcha #9 in `SKILL.md` for why that matters), and NeuVector's process profile matches by path so a byte- identical binary at a never-seen path is still "unrecognized":
```
docker exec <container> sh -c 'cp /bin/busybox /tmp/e2e-unrecognized-tool && chmod +x /tmp/e2e-unrecognized-tool'
docker exec -d <container> /tmp/e2e-unrecognized-tool sleep 300
```
then poll for the process's own disappearance (`pgrep -f /tmp/e2e-unrecognized-tool` inside the container) — **Expect** — the process is killed within ~20s of starting.

A matching `Process.Profile.Violation` entry via `GET /v1/log/incident?start=0` is a secondary, corroborating check, not the primary pass signal — see Pass criteria.

**Pass criteria**
- The process's own termination (confirmed by re-checking for its PID) is the authoritative
signal when it happens — not the incident log alone, which has the same reporting-pipeline gap noted in E2E-3.4 below.
- **Not killed within ~20s is informational, not a hard fail.** On a kernel without
`CONFIG_CONNECTOR` (confirmed via NeuVector's own source, `agent/probe/probe.go`), process-change detection falls back from netlink push to a periodic `/proc` scan — still feeding the same kill path (`scanNewProcess` → `killProcess`), but with latency that's been observed to vary well beyond 20s depending on host/NeuVector CPU load at the time. This means the check can't reliably distinguish a genuine enforcement miss from ordinary scan-mode latency on this environment, so it doesn't fail the step — treat a non-kill here as inconclusive, not proof of either outcome.

### E2E-3.3b — File-access monitoring pipeline is alive (predefined-file sanity check)

Added 2026-09-30 to tell apart two different failure modes for E2E-3.4 below: a device-wide monitoring outage versus a defect specific to custom, user-created filters. Access a predefined sensitive file (alert-only by design, same caveat as E2E-2.2/E2E-3.4) and confirm a real incident is logged for it:
```
docker exec <container> sh -c 'cat /etc/passwd'
curl -sk -H "X-Auth-Token: <nv-token>" "https://<tunnelEndpoint>/v1/log/incident?start=0"
```
**Expect** — a `Container.FileAccess.Violation` incident for `/etc/passwd`, scoped to this container's workload, within a few seconds.

**Pass criteria**
- At least one matching incident is found for this workload after the access.
- If this fails, the file-access-monitoring pipeline itself may be down device-wide — E2E-3.4 failing is then unsurprising rather than a distinct defect.
- If this passes but E2E-3.4 still fails, the problem is isolated to custom, user-created filters specifically — confirmed live (2026-09-30) as the actual pattern on this device; see E2E-3.4's pass criteria item 4 below.

### E2E-3.4 — The file-monitor rule holds under a live test

**Use a genuinely custom path, not `/etc/passwd`.** As noted in E2E-2.2 above, `/etc/passwd` is
one of NeuVector's own predefined sensitive files and is alert-only by design — it will never be block-enforced, so testing against it can never demonstrate a real block. Register a `block_access` filter on a custom path first (e.g. `/tmp/e2e-custom-secret.txt`, created inside the container), then test that path:
```
docker exec <container> sh -c 'echo secret > /tmp/e2e-custom-secret.txt'
# register a block_access filter on /tmp/e2e-custom-secret.txt via the E2E-2.2 pattern, then:
docker exec <container> sh -c 'cat /tmp/e2e-custom-secret.txt'
```
**Expect** — the command itself fails at the OS level: `Operation not permitted`
(non-zero exit), not merely "no incident logged."

**Pass criteria**
- The read/write is genuinely refused by the OS, confirmed by exit code and stderr, not inferred
from the incident log alone.
- A matching incident-log entry is the secondary check — the corresponding `/v1/log/incident`,
`/v1/log/security`, and `/v1/log/violation` entries have not reliably appeared within a ~10s window even on a genuine block. Treat the OS-level refusal as the authoritative pass signal for this step; a missing log entry alongside a genuine refusal is a logging-pipeline gap, not an enforcement failure — note it, don't fail the step over it alone.
- If the command instead succeeds (`exit 0`, file content printed) **and the path is genuinely custom** (not a predefined file like `/etc/passwd`/`/etc/hosts`/`/etc/resolv.conf`), check in order: (1) is `profile_mode` actually `Protect` (not just `policy_mode`)? (2) was the container restarted after Protect mode was set? (3) does the deployed image actually contain `pathWalker` (`docker exec <container> ls -la /usr/local/bin/pathWalker`)? A missing (3) is a real image gap, not fixable by any API call — see `SKILL.md` point 4.
- (4) If (1)-(3) all check out and E2E-3.3b above passed, this matches a known NeuVector regression independently reported on unrelated infrastructure, not a config or image problem: [SUSE forum, "File monitoring in Neuvector versions 5.4.1, 5.4.2, 5.4.3, 5.4.8 and 5.4.9 doesn't work"](https://forums.suse.com/t/file-monitoring-in-neuvector-versions-5-4-1-5-4-2-5-4-3-5-4-8-and-5-4-9-doesnt-work/46192) — custom filter locations show no activity in any of those versions. No fix is documented in the 5.4.9 through 5.5.4 release notes checked (2026-09-30).
- **As of 2026-09-30, the script treats a successful read (`exit 0`) here as informational, not a hard fail**, once (1)-(3) above check out — the same class of exception as E2E-3.3's kill-timeout, for a confirmed external limitation this script can't distinguish from a genuine defect on every environment.

### E2E-3.5 — Compliance/CIS-benchmark scan produces real findings

Added 2026-09-30. A genuinely different subsystem from E2E-3.3/3.3b/3.4 above: a static host/container configuration audit (Docker CIS Benchmark), not a live fanotify/netlink/NFQ enforcement hook — none of the kernel/version limitations affecting those checks apply here.
```
curl -sk -H "X-Auth-Token: <nv-token>" "https://<tunnelEndpoint>/v1/host"
curl -sk -H "X-Auth-Token: <nv-token>" "https://<tunnelEndpoint>/v1/compliance/asset"
```
**Expect** — `docker_bench_status: "finished"` on the host, at least one finding from `/v1/compliance/asset`.

**Pass criteria**
- `docker_bench_status` is `"finished"`.
- At least one non-empty compliance finding is returned.
- Confirmed live (2026-09-30): 46 real findings on this device.

---

## Sign-off mapping

| # | Criterion | Steps |
|---|---|---|
| 1 | NeuVector answers through the tunnel, returns a token | E2E-1.1 |
| 2 | Test group can be switched to Protect mode (both `policy_mode` and `profile_mode`) | E2E-2.1 |
| 3 | A specific rule can be added | E2E-2.2 |
| 4 | A full configuration backup exports as a valid archive | E2E-2.3 |
| 5 | Container confirmed as a discovered/tracked NeuVector workload | E2E-3.1 |
| 6 | Protection confirmed active via live check, not assumed from engine health | E2E-3.2 |
| 7 | Process enforcement blocks the live test | E2E-3.3 |
| 7b | File-access monitoring pipeline is alive (predefined-file sanity check, informational) | E2E-3.3b |
| 8 | File-access enforcement blocks the live test on a custom path | E2E-3.4 |
| 9 | Compliance/CIS-benchmark scan produces real findings (different subsystem) | E2E-3.5 |
| 10 (optional) | Whole baseline applies in one shot via the JSON template, idempotent and per-group scoped | E2E-2.4 |

## Defect template

Same as the source document's Section 7 — reproduced by the script on failure.
