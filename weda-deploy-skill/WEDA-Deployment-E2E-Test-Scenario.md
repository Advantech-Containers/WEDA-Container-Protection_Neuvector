---
doc-id:    E2E-WEDA-DEPLOY-001
title:     "WEDA Container Deployment & Access — End-to-End Test Scenario"
product:   "WEDA Platform"
version:   v1.0-20260910
status:    Draft
date:      2026-09-10
traces-to: weda-neuvector-deploy-skill/NeuVector-E2E-Test-Scenario.md (E2E-NEUVECTOR-EDGE-001)
tags:      [doc:test, feature:container-management, service:weda, service:dmagent, layer:cloud, layer:edge, tech:rest, status:draft]
---

# WEDA Container Deployment & Access — End-to-End Test Scenario

> **Who this is for:** QA engineers verifying that WEDA can deploy and provide remote access to any container on a device — not specific to NeuVector.
> **What it covers:** creating a stack config, deploying it to a device, confirming it actually runs, then opening and closing a temporary tunnel to reach whatever port that container exposes.
> **What it is not:** anything about what the deployed container does once it's running — that's a separate, container-specific test report (e.g. `NeuVector-E2E-Test-Scenario.md`). It applies to any container deployed through WEDA, not only NeuVector.

Every step is a literal, reproducible command against the WEDA Core API.

[[_TOC_]]

---

## 1. Test Environment & Prerequisites

### 1.1 Environment

| Item | Value | Notes |
|---|---|---|
| WEDA base URL | `https://{domain}/{tenant}/weda` | e.g. `weda-sit-k3s.weda.dev` |
| API prefix | `/api/v1` | Full path = `{BASE}/api/v1/orgs/{orgId}/stack-configs` etc. |
| Auth | `Authorization: Bearer <JWT>` | Scoped to one org, typically expires within an hour or two |
| Client | `curl` | No direct network route to the device is required |

**Verify the token before using it:**

```bash
echo "<jwt>" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
```

Check `exp` hasn't passed and `resource_access` has a non-empty entry for the org that owns the target device. An empty `resource_access` is an org-membership problem, not fixable by minting a different token for the same account.

### 1.2 Devices and inputs required

| # | Item | State needed |
|---|---|---|
| D1 | A WEDA-registered device | `Activated`, reachable over NATS |
| — | A compose file for the container to deploy | Valid Docker Compose YAML, base64-encoded for the API body |

**Precondition check before you start**

- [ ] D1 shows `Activated` in WEDA. If it does not, nothing below will succeed.
- [ ] D1 is not mid-redeploy (no other operator is targeting it right now) — a stale-revision conflict is a WEDA-side issue, not a bug in the deployed container.
- [ ] No existing stack config with the same `stackName` is already active on D1, unless this is intentionally a redeploy (see the warning in E2E-1.2).

---

## 2. How to read a test step

| Field | Meaning |
|---|---|
| **Step** | Stable ID — quote it in the defect title |
| **Call** | The exact request |
| **Expect** | The response that means PASS |
| **Pass criteria** | Check every line — all of them must hold |

A step FAILS if the status code or any field differs from **Expect**.

---

## 3. Act 1 — Create and deploy a stack

### E2E-1.1 — Create a stack config

```bash
curl -sk -X POST "https://<domain>/<tenant>/weda/api/v1/orgs/<orgId>/stack-configs" \
  -H "Authorization: Bearer <jwt>" -H "Content-Type: application/json" \
  -d '{"stackName":"<stackName>","type":"compose","composeFileContent":"<base64-compose>","environment":{},"description":"<description>"}'
```

**Expect** — `200 OK` (or `201`), with `stackConfigId` and `stackRevisionId` (revision 0) in the response

**Pass criteria**
- [ ] Before creating, `GET /api/v1/orgs/<orgId>/stack-configs?stackName=<stackName>` was checked — if a config with this name already exists, this is a redeploy and must follow the undeploy-before-redeploy sequence in E2E-1.2's warning, not a fresh create.
- [ ] Both `stackConfigId` and `stackRevisionId` are recorded for the next step.

### E2E-1.2 — Deploy the revision to the device

```bash
curl -sk -X POST "https://<domain>/<tenant>/weda/api/v1/orgs/<orgId>/stack-configs/<stackConfigId>/revisions/<stackRevisionId>:deploy" \
  -H "Authorization: Bearer <jwt>" -H "Content-Type: application/json" \
  -d '{"deviceIds":["<deviceId>"]}'
```

**Expect** — `200 OK` / `"deploying"`

**Pass criteria**
- [ ] A `200`/`"deploying"` response is accepted as "instruction received," not as PASS on its own — E2E-1.3 is the actual confirmation.

**Never PATCH-and-deploy on top of an active deployment.** If a stack config is updated while the old revision is still active on the device, `dmagent` receives conflicting instructions and can end up tearing down the new containers repeatedly, since both revisions share the same Compose project name. Always undeploy first:

```
PATCH stack-configs/{id}                                    → new stackRevisionId
DELETE devices/{deviceId}/docker/stacks?stackConfigId={id}   → clears old deployment record
POST stack-configs/{id}/revisions/{newId}:deploy             → clean redeploy
```

### E2E-1.3 — Confirm containers are actually running

```bash
curl -sk -H "Authorization: Bearer <jwt>" \
  "https://<domain>/<tenant>/weda/api/v1/devices/<deviceId>/docker/stacks"
```

**Expect** — `200 OK`, with a non-empty `containers` array for the deployed stack

**Pass criteria**
- [ ] The `containers` array is non-empty — an empty array means the deploy call was accepted but nothing is actually running (commonly caused by a registry-pull failure on the device).
- [ ] The running stack matches the `stackConfigId`/`stackRevisionId` just deployed, not a stale prior revision.

---

## 4. Act 2 — Access the deployed container through a tunnel

### E2E-2.1 — Open a tunnel session

```bash
curl -sk -X POST "https://<domain>/<tenant>/weda/api/v1/devices/<deviceId>/tunnels/sessions" \
  -H "Authorization: Bearer <jwt>" -H "Content-Type: application/json" \
  -d '{"serviceType":"http","applicationHost":"<application-host>","applicationPort":<port>,"purpose":"e2e-test","tags":["e2e"]}'
```

**Expect** — `202`, with `sessionStatus: "ESTABLISHING"` and a `tunnelSessionId`

**Pass criteria**
- [ ] A separate tunnel session is created for each port that needs reaching — one session only proxies the single `applicationPort` it was created with.
- [ ] `<application-host>` is the device's real, WEDA-visible LAN IP (or a shared-network IP if the
target container is on `dmagent`'s own Docker network) — never `localhost`/`127.0.0.1`. That value can fail the tunnel's own health check outright even though the target is genuinely reachable locally on the device; see `REFERENCE.md`'s `applicationHost` guidance under E2E-2.1.
- [ ] `tags` must be a non-empty array — `tags: []` is rejected with a `400 ValidationError` on the `Tags` field; confirmed live.

### E2E-2.2 — Poll until the tunnel is ready

```bash
curl -sk -H "Authorization: Bearer <jwt>" \
  "https://<domain>/<tenant>/weda/api/v1/devices/<deviceId>/tunnels/sessions/<tunnelSessionId>"
```

**Expect** — `sessionStatus: "ESTABLISHED"` with a `tunnelEndpoint`

**Pass criteria**
- [ ] Poll rather than assuming success from E2E-2.1's `202` alone.
- [ ] Record `tunnelEndpoint` for use by the container-specific test report.

### E2E-2.3 — Close the tunnel

```bash
curl -sk -X DELETE "https://<domain>/<tenant>/weda/api/v1/devices/<deviceId>/tunnels/sessions/<tunnelSessionId>" \
  -H "Authorization: Bearer <jwt>"
```

**Expect** — the session terminates immediately

**Pass criteria**
- [ ] A subsequent request to `<tunnelEndpoint>` fails to connect.

(Not tested here: a tunnel also auto-closes after 120 s idle, or 3600 s regardless of activity.)

---

## 5. Exit Criteria — Sign-off Sheet

| # | Criterion | Steps | Result |
|---|---|---|---|
| 1 | A stack config can be created (or an existing one correctly identified for redeploy) | E2E-1.1 | ☐ |
| 2 | A deploy call is accepted | E2E-1.2 | ☐ |
| 3 | Containers are confirmed actually running, not just accepted | E2E-1.3 | ☐ |
| 4 | A tunnel session opens and reaches `ESTABLISHED` | E2E-2.1, 2.2 | ☐ |
| 5 | A tunnel session can be closed explicitly | E2E-2.3 | ☐ |

Environment: ＿＿＿＿＿　Device: ＿＿＿＿＿　Tester: ＿＿＿＿＿　Date: ＿＿＿＿＿　Verdict: ☐ PASS ☐ FAIL

---

## 6. Defect Reporting Template

```
Title: [WEDA Deploy] E2E-x.y failed — <one-line symptom>

Environment: <WEDA domain/tenant>
Time: <YYYY-MM-DD HH:MM> UTC (use UTC)
Step: E2E-x.y
Device: <deviceId>

Expected:
  <copy from this document's Expect field>

Actual:
  <the full HTTP status code and response body>

Notes (optional):
  - Was an existing stack config with the same name already active (redeploy case)?
  - Did E2E-1.3's containers array come back empty (registry-pull failure) or non-empty but wrong revision?
```

---

## 7. Changelog

| Version | Date | Changes |
|---|---|---|
| v1.0-20260910 | 2026-09-10 | Initial |
