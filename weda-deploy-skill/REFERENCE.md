# weda-deploy-skill — Reference

Mirrors `WEDA-Deployment-E2E-Test-Scenario.md` (doc-id `E2E-WEDA-DEPLOY-001`) directly. If the source document and this file ever disagree, treat the document as authoritative and update this file — this is a reformatting of that document's content for script/agent use, not an independent spec.

## How to read a step

Same convention as the source document: **Step** is a stable ID, **Call** is the exact request,
**Expect** is the response that means PASS, **Pass criteria** are all the things that must hold
for a PASS — not just the status code.

---

## Act 1 — Create and deploy a stack

### E2E-1.1 — Create a stack config

**Call**
```
GET /api/v1/orgs/{orgId}/stack-configs?stackName={stackName}
```
If a config with this name already exists, this is a redeploy — follow the undeploy-before-redeploy sequence below, do not create a duplicate.

If none exists:
```
POST /api/v1/orgs/{orgId}/stack-configs
Body: {"stackName", "type": "compose", "composeFileContent": <base64>, "environment": {}, "description"}
```

**Expect** — `200`/`201`, with `stackConfigId` and `stackRevisionId`.

**Pass criteria**
- The existing-config check happened first.
- Both IDs are recorded for the next step.

### E2E-1.2 — Deploy the revision to the device

**Call**
```
POST /api/v1/orgs/{orgId}/stack-configs/{stackConfigId}/revisions/{stackRevisionId}:deploy
Body: {"deviceIds": [deviceId]}
```

**Expect** — `200`, `"deploying"`.

**Pass criteria**
- A `200`/`"deploying"` response is "instruction received," not PASS on its own — E2E-1.3 is the
real confirmation.

**Critical — never PATCH-and-deploy on top of an active deployment.** If a stack config is
updated while the old revision is still active on the device, `dmagent` gets conflicting instructions (deploy new / remove old) and can tear down the just-started new containers repeatedly, since every revision of one `stackName` shares the same Compose project name. Always:
```
PATCH stack-configs/{id}                                   → new stackRevisionId
DELETE devices/{deviceId}/docker/stacks?stackConfigId={id}  → clears old deployment record
POST stack-configs/{id}/revisions/{newId}:deploy            → clean redeploy
```

**Known escalation if this still loops**: if `dmagent`'s own logs (`docker logs dmagent`) show it
repeatedly cycling through multiple *old* revision IDs for this `stackName` even after the above sequence, its internal desired-state cache is stuck — this has been observed even when the WEDA API confirms the stack config and all its deployments are gone. The only reliable fix found so far is deploying fresh under a **different `stackName`** (a genuinely new Compose project name).

**Clearing everything on a device**: this script (and the sequence above) only ever removes one
named stack at a time via `DELETE .../docker/stacks?stackConfigId={id}`. Per the official `weda-container-mgmt` skill, the documented endpoint for wiping *every* deployment on a device in one call is a **different path**: `DELETE /api/v1/devices/{deviceId}/stacks/deployments` (not `docker/stacks` with the query param omitted). `docker/stacks` with no `stackConfigId` was also observed to return `204` and clear all deployments in real testing, but `stacks/deployments` is the one the container-management API actually documents for this purpose — prefer it if writing a "clear this device" recipe outside this script.

### E2E-1.3 — Confirm containers are actually running

**Call**
```
GET /api/v1/devices/{deviceId}/docker/stacks
```

**Expect** — `200`, non-empty `containers` array for the deployed stack.

**Pass criteria**
- `containers` is non-empty (empty commonly means a registry-pull failure on the device).
- The running stack matches the just-deployed `stackConfigId`/`stackRevisionId`, not a stale one.
- If the compose file references a locally-built-only image (no registry to pull from), it must
set `pull_policy: never` on that service — otherwise `dmagent`'s deploy sequence force-pulls and fails with `manifest unknown` even though the image already exists locally on the device.

---

## Act 2 — Access the deployed container through a tunnel

### E2E-2.1 — Open a tunnel session

**Call**
```
POST /api/v1/devices/{deviceId}/tunnels/sessions
Body: {"serviceType": "http", "applicationHost": <see below>, "applicationPort": port, "purpose", "tags": ["e2e"]}
```
**`tags` must be non-empty** — `tags: []` is rejected outright with a `400 ValidationError` on the
`Tags` field; confirmed live. Any non-empty array (e.g. `["e2e"]`) works.

**Expect** — `202`, `sessionStatus: "ESTABLISHING"`, `tunnelSessionId`.

**`applicationHost` guidance** (this is the single most failure-prone field in the whole flow):
- `127.0.0.1` / `localhost` can fail the tunnel's own health check outright, even when the target
is genuinely reachable locally on the device.
- If the target container needs to be reached from `dmagent`'s own container network (common when
`dmagent` itself runs as a Docker container on an isolated bridge network, and the target is a
  *sibling* container published on the host's external ports) — attach the target container to
`dmagent`'s network too (`docker network connect <dmagent-network> <target-container>`, or bake it into the compose file as an `external: true` network) and use the IP it gets on that shared network.
- Otherwise use the device's real, WEDA-visible LAN IP.
- A separate tunnel session is required per port — one session only proxies the single
`applicationPort` it was created with.

### E2E-2.2 — Poll until the tunnel is ready

**Call**
```
GET /api/v1/devices/{deviceId}/tunnels/sessions/{tunnelSessionId}
```

**Expect** — `sessionStatus: "ESTABLISHED"`, `tunnelEndpoint`.

**`sessionStatus` can be returned as an int or a string** — confirmed against the official
`weda-tunnel-mgmt` skill, verified directly against the live spec: `0`=`ESTABLISHING`, `1`=`ESTABLISHED`, `2`=`TERMINATING`, `3`=`TERMINATED` (plus a string-only `FAILED` with no numeric form). Always normalize before comparing — a script that only checks the string literal will silently misbehave on any environment/version that returns the numeric form instead.

**Pass criteria — do all of these, not just the first `ESTABLISHED` sighting:**
- Poll rather than trusting `202` alone.
- Recheck 2-3 more times after first seeing `ESTABLISHED` — a session has been observed to report
`ESTABLISHED`/`healthStatus: HEALTHY` for only a couple of seconds before flipping to `UNHEALTHY` and self-terminating, the whole time genuinely unusable.
- Make one real TCP/TLS request through `tunnelEndpoint` before declaring this a pass. A
successful handshake (any response at all, even an HTTP error) proves real relay; a timeout or reset means the tunnel reached `ESTABLISHED` but never actually worked — this is the exact failure mode confirmed on real hardware, don't treat it as a flake to retry blindly.
- This isn't always a one-time flip — confirmed live, a session can cycle `ESTABLISHED` →
`TERMINATED` → `ESTABLISHED` repeatedly, with `tunnelEndpoint` itself changing value each time it
re-establishes. Re-fetch the session immediately before each request attempt rather than reusing an
endpoint captured earlier in the same run.
- Record `tunnelEndpoint` for the container-specific test report — but only the one confirmed
working by an actual request, since an earlier-recorded one may already be stale.

### E2E-2.3 — Close the tunnel

**Call**
```
DELETE /api/v1/devices/{deviceId}/tunnels/sessions/{tunnelSessionId}
Body: {"reason": "...", "graceful": true, "forceTimeout": 30}
```
A bodiless DELETE returns `415 Unsupported Media Type` on this API — always send the body above.

**Expect** — session moves to `TERMINATING`/`TERMINATED`.

**Pass criteria**
- A subsequent request to `tunnelEndpoint` fails to connect.

**Lifetime facts, not tested here but relevant to any caller**: a tunnel auto-closes after 120s
with no client connection, and has a hard 3600s max lifetime regardless of activity, with **no keep-alive or extend call available** — confirmed directly against the tunnel-management API surface (session metadata is fixed at creation). Any script or skill built on top of a tunnel from this one must do everything it needs in one continuous run.

---

## Sign-off mapping

| # | Criterion | Steps |
|---|---|---|
| 1 | Stack config created or correctly identified for redeploy | E2E-1.1 |
| 2 | Deploy call accepted | E2E-1.2 |
| 3 | Containers confirmed actually running | E2E-1.3 |
| 4 | Tunnel opens and reaches `ESTABLISHED` | E2E-2.1, 2.2 |
| 5 | Tunnel can be closed explicitly | E2E-2.3 |

## Defect template

Same as the source document's Section 6 — reproduced by the script on failure, filled with the real step ID, timestamp, device, expected vs. actual response.
