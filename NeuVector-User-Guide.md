---
doc-id:    GUIDE-NEUVECTOR-EDGE-001
title:     "WEDA Container Runtime Protection — NeuVector User Guide: Manual Install and AI-Skill-Driven Install"
product:   "WEDA-Container-Runtime-Protection"
version:   1.1
status:    Completed
date:      2026-11-11
tags:      [doc:guide, feature:container-security, service:neuvector, layer:edge, status:completed]
---

# WEDA Container Runtime Protection — NeuVector User Guide

Two ways to get NeuVector running and verified on a device. Pick one:

| | Manual install | AI-skill-driven install |
|---|---|---|
| **Who it's for** | Anyone with shell access to the device | Anyone driving deployment through an AI agent against a WEDA-managed device |
| **What it needs** | This repo, Docker | This repo's two bundled E2E skills, a WEDA account |
| **What you get** | NeuVector running locally, reachable at `https://<host>:8443` | NeuVector deployed to a remote WEDA device, verified end-to-end, with a pass/fail report |

Both paths deploy the same image: the unmodified upstream `docker.io/neuvector/allinone:5.4.8`. NeuVector is open-source software by SUSE; this project is not affiliated with SUSE — see [NOTICE.md](NOTICE.md). Both the manual scripts and the AI skills now live in this single repo — nothing else to clone.

---

## Part A — Manual Install

Use this when you have direct shell access to the target machine (or a device you can SSH into) and just want NeuVector running.

### A.1 Prerequisites

| Requirement | Details |
|---|---|
| OS | Linux, x86_64 or ARM64 |
| CPU / RAM / Disk | 2 cores / 2 GB / 4 GB recommended (1 core / 1 GB / 2 GB minimum) |
| Docker | `docker` + `docker compose` v2, running |
| Access | `sudo` / root |

```bash
# Ubuntu / Debian
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 curl jq openssl
sudo systemctl enable --now docker
```

### A.2 Get the repo

```bash
git clone git@github.com:Advantech-Containers/Advantech-WEDA-Container-Runtime-Protection.git
cd Advantech-WEDA-Container-Runtime-Protection
```

No scripts to `chmod` — there's no cert-gen or deploy step to run yourself. The Web UI and REST API use NeuVector's own self-signed certificates.

### A.3 Deploy

```bash
docker compose up -d
```

This pulls `neuvector/allinone:5.4.8` from Docker Hub if it isn't already local, stops any previous NeuVector container of the same name, and starts a fresh one. Allow ~90 seconds after it starts for the controller/enforcer to finish initializing before logging in.

**Verify it's healthy:**

```bash
TOKEN=$(curl -sk -X POST https://localhost:10443/v1/auth \
  -H "Content-Type: application/json" \
  -d '{"password":{"username":"admin","password":"admin"}}' | jq -r '.token.token')
curl -sk https://localhost:10443/v1/system/summary -H "X-Auth-Token: $TOKEN" | jq .
```

A healthy response reports `"controllers": 1`, `"enforcers": 1`, `"hosts": 1` (allow ~30-45s after first boot for these to populate). See [Quickstart.md](Quickstart.md#step-5--verify-the-deployment) for the full verification walkthrough and [Troubleshooting](Quickstart.md#troubleshooting) if something looks wrong.

> **No shell access to the device, but you do have WEDA access?** You don't need an AI agent for
> that either — see [Quickstart.md's "Create Stack"](Quickstart.md#create-stack)
> for the same deployment done as three raw calls against WEDA's own API instead of `docker compose`.

### A.4 Log in and configure

| Interface | URL |
|---|---|
| Web UI | `https://<host-ip>:8443` |
| REST API | `https://<host-ip>:10443` |

Default credentials are `admin` / `admin` — change the password on first login. From here, configuring Protect mode, rules, and backups can be done through the console or the REST API; see [`weda-neuvector-deploy-skill/NeuVector-E2E-Test-Scenario.md`](weda-neuvector-deploy-skill/NeuVector-E2E-Test-Scenario.md) for the exact REST calls if you want to script it yourself.

### A.5 Full reference

`README.md` and `Quickstart.md` in this same repo cover the full install in more detail — hardware sizing, troubleshooting, and every config option. This section is the short version.

---

## Part B — AI-Skill-Driven Install (WEDA-managed devices)

Use this when NeuVector needs to go onto a device managed through the WEDA platform, and you want an AI agent to deploy, configure, and *prove* it's working — not just deploy it and hope.

This path uses two independent, self-contained skills bundled directly in this repo — no separate skills repo to clone. Neither needs the other, and neither needs any separate login or deploy tool already set up — each mints its own WEDA session and does its own deployment.

### B.1 The skills

Already present once you have this repo (see [A.2](#a2-get-the-repo) if you haven't cloned it yet):
- [`weda-deploy-skill`](weda-deploy-skill/) — deploys *any* container to a WEDA device and proves a tunnel to it actually relays traffic. Not NeuVector-specific; useful for confirming a device is reachable at all before testing NeuVector on it.
- [`weda-neuvector-deploy-skill`](weda-neuvector-deploy-skill/) — deploys NeuVector itself (if it isn't already running), configures Protect mode + a file-access rule + a backup, then actually triggers each control and confirms it fires live.

Point your agent (any tool that reads a `SKILL.md`) at these two directories, or copy them into your own skills folder — they have no dependency on where they're run from.

### B.2 Prerequisites

| Need | Notes |
|---|---|
| WEDA domain + tenant | e.g. `weda.example.com` / `<tenant>` |
| A WEDA account | Exported as `WEDA_LOGIN` / `WEDA_PASSWORD` in the shell — the skill mints its own session token automatically; it never needs a separate login step or tool |
| `--token-file` | Required by every command below — a path to store the session token at (e.g. `/tmp/weda.jwt`). It doesn't need to exist yet; it's created/refreshed automatically |
| `deviceId` | The target device, already `Activated` on WEDA |
| Python 3 | The skills are plain Python scripts, no extra install |

```bash
export WEDA_LOGIN='<account>'
export WEDA_PASSWORD='<password>'    # exported in your shell, never passed as a script argument
```

### B.3 Deploy NeuVector and verify it, end to end

```bash
python3 weda-neuvector-deploy-skill/scripts/run_neuvector.py \
  --domain <weda-domain> --tenant <tenant> \
  --token-file /tmp/weda.jwt \
  --device-id <deviceId> \
  --group <test-container-group>
```

`--token-file` always takes a path (e.g. `/tmp/weda.jwt`) — it doesn't need to exist yet. If it's missing or expired, the script mints a fresh token there automatically using `WEDA_LOGIN`/`WEDA_PASSWORD`.

This single command:
1. Checks whether NeuVector is already running on the device; if not, deploys it from the skill's own bundled compose file (same image as the manual path, `neuvector/allinone:5.4.8`).
2. Opens its own tunnel to the device — no separate deploy or login step needed first.
3. Logs into NeuVector and configures Protect mode, a file-access rule, and a config backup.
4. Actually tries to trigger each control (runs an unrecognized process, tries to read a protected file) and confirms the block happened live — not just that the setting saved.
5. Prints PASS/FAIL per step and writes a sign-off report.

If no container matching `--group` is already running locally, the command above deploys a throwaway one itself (standing in for an unknown vendor workload) — no separate precondition step needed. Point `--group` at an existing container instead if you'd rather test one that's already running.

### B.4 Confirming the device itself is reachable (optional, run first on a new device)

```bash
python3 weda-deploy-skill/scripts/run_deploy.py \
  --domain <weda-domain> --tenant <tenant> \
  --token-file /tmp/weda.jwt \
  --device-id <deviceId> \
  --compose-file <any-test-compose.yml> \
  --stack-name <a-new-stack-name> \
  --port <port> --application-host <container-ip-or-host>
```

`--stack-name` should be a name not already used on this device — reusing one can hit a stale-revision issue documented in the skill's own `REFERENCE.md`. `--application-host` is the address the target port is actually reachable at (often not `127.0.0.1` — see the same reference). Confirms WEDA can deploy a container to this device at all and that a tunnel to it relays real traffic — useful once per new device, before testing NeuVector on it specifically.

### B.5 Full reference

Each skill's `SKILL.md` documents its own prerequisites, defaults, and a running list of real-hardware gotchas discovered while building it (endpoint quirks, timing behavior, etc.); each `REFERENCE.md` has the exact API calls and pass criteria, and each skill carries its own test scenario doc (`WEDA-Deployment-E2E-Test-Scenario.md` / [`NeuVector-E2E-Test-Scenario.md`](weda-neuvector-deploy-skill/NeuVector-E2E-Test-Scenario.md)) with the full step-by-step pass criteria and sign-off sheet.

---

## Which path should I use?

- Testing or developing locally, or deploying to a machine you can SSH into directly → **Part A**.
- Deploying to a fleet of WEDA-managed edge devices, or need a repeatable, provable pass/fail check every time → **Part B**.

Both produce the same running NeuVector, on the same image. Part B additionally proves the security controls actually work, live, every time it's run.
