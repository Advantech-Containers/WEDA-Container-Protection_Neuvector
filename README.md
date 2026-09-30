# NeuVector Edge

This repo (`neuvector_release`) is the complete release package for NeuVector Edge: the optimized container image, its `docker-compose.yml`, and the AI-agent skills for deploying it on a WEDA-managed device — everything needed to deploy and verify NeuVector on edge and resource-constrained environments. There's no separate deploy/cert-gen/test script to run — deployment and verification are direct calls against WEDA's or NeuVector's own REST APIs, or `docker compose` directly.

**Version:** v5.4.8.edge.v1.1.2.32309
**Base Image:** neuvector/allinone:5.4.8
**License:** Apache 2.0

[[_TOC_]]

## Overview

NeuVector Edge is a size-optimized build of the NeuVector allinone image. It retains full Web UI and REST API functionality while removing components not needed in edge deployments.

### Components Included

| Component | Purpose |
|-----------|---------|
| Controller | Policy engine and cluster coordination |
| Enforcer | Runtime security and network monitoring |
| Manager (Web UI) | Browser-based management console (port 8443) |
| REST API | Programmatic management and automation (port 10443) |
| Path Walker | Required for file-access rule (`block_access`) enforcement in Protect mode — retained from `v1.1.0` onward, see [RELEASE_NOTES.md](RELEASE_NOTES.md) |

### Components Removed

| Component | Path | Reason |
|-----------|------|--------|
| Upgrader | `/usr/local/bin/upgrader` | Not applicable for single-node edge |
| OPA Engine | `/usr/local/bin/opa` | Open Policy Agent not required for edge |
| yq | `/usr/bin/yq` | YAML processor not used at runtime |
| Documentation | `/usr/share/doc` | Reduces image size |
| Man Pages | `/usr/share/man` | Reduces image size |

## Deploy

No cert-gen step and no deploy script — the image generates its own TLS certificates on first boot. Pick whichever matches your situation.

**Option A — via WEDA (no script, just its own API):** three raw calls create a stack config from `docker-compose.yml`, deploy it to your device, and poll until it's running.

**Option B — direct, already on the device:** `docker compose up -d`.

Both need the same ~90 second wait afterward for the Web UI/API to become reachable. See **[Quickstart.md](Quickstart.md#step-2--deploy)** for the exact commands and expected output for each.

## Connect via Tunnel

If you deployed via WEDA (Option A), the container is on a remote device — open a tunnel to it before you can reach its API or Web UI. See **[Quickstart.md](Quickstart.md#step-4--connect-weda-deployed-only)** for the commands, and [`weda-deploy-skill/REFERENCE.md`](weda-deploy-skill/REFERENCE.md) (Act 2) for the gotchas that shaped them — `applicationHost` in particular is the single most failure-prone field in this whole flow.

## Verify It's Working

Log in and check `system/summary` — a healthy instance reports `"controllers": 1`, `"enforcers": 1`, `"hosts": 1`. See **[Quickstart.md](Quickstart.md#step-5--verify-the-deployment)** for the exact commands, expected output, and [Troubleshooting](Quickstart.md#troubleshooting) if something looks wrong.

Two guides cover the rest, depending on what you need:
- **[NeuVector-User-Guide.md](NeuVector-User-Guide.md)** — start here to decide between manual install (this machine) or AI-skill-driven install (a remote WEDA-managed device).
- **[Quickstart.md](Quickstart.md)** — the full manual-install walkthrough: prerequisites, every step with expected output, configuration, operations, troubleshooting, API reference, and file layout.

## Apply the Security Config Template

[`protect-config-template.example.json`](protect-config-template.example.json) (repo root) is a baseline security config: Protect mode, a set of file-monitor rules, an allow-DNS/allow-HTTPS/deny-everything-else network policy, and a response rule that quarantines the container on a critical security event. Apply it via the `weda-neuvector-deploy-skill` (below) in one command, or by hand — see **[Quickstart.md](Quickstart.md#apply-the-security-config-template-optional)** for the raw REST call per field, with expected output for each.

## AI Skills

Two self-contained AI-agent skills are bundled in this repo, for deploying and verifying NeuVector on a WEDA-managed device without manual steps. Neither depends on the other, or on any separate login/deploy tool already set up — each mints its own WEDA session and, if needed, deploys its own test workload.

| Skill | Purpose |
|---|---|
| [`weda-deploy-skill`](weda-deploy-skill/) | Container-agnostic: deploys any Docker Compose stack to a WEDA device and proves a tunnel to it actually relays traffic. |
| [`weda-neuvector-deploy-skill`](weda-neuvector-deploy-skill/) | Deploys NeuVector itself (if not already running), configures Protect mode + a file-access rule + a backup, then actually triggers each control and confirms it fires live — not just that the setting saved. |

```bash
export WEDA_LOGIN='<account>'
export WEDA_PASSWORD='<password>'    # exported in your shell, never passed as a script argument

# Deploy NeuVector, configure it, and verify every control fires live — full sign-off report
python3 weda-neuvector-deploy-skill/scripts/run_neuvector.py \
  --domain <domain> --tenant <tenant> --token-file /tmp/weda.jwt \
  --device-id <deviceId> --group <test-container-group>

# Or: confirm any container deploys and a tunnel to it actually works, before testing NeuVector specifically
python3 weda-deploy-skill/scripts/run_deploy.py \
  --domain <domain> --tenant <tenant> --token-file /tmp/weda.jwt \
  --device-id <deviceId> --compose-file <path-to-compose.yml> \
  --stack-name <name> --port <port> --application-host <device-ip>
```

Each skill's own `SKILL.md` documents every subcommand (`deploy`/`tunnel`/`check`/`template` for `weda-neuvector-deploy-skill`; `deploy`/`tunnel` for `weda-deploy-skill`), prerequisites, and a running list of real-hardware gotchas found while building it. Each `REFERENCE.md` has the exact API calls and pass criteria, and each test scenario doc (`WEDA-Deployment-E2E-Test-Scenario.md` / `NeuVector-E2E-Test-Scenario.md`) has the full step-by-step sign-off sheet.

## NeuVector Platform Reference

The edge container ships the full NeuVector management plane (controller, enforcer, and manager). The tables below map each feature area to the official NeuVector documentation and note what applies to this edge deployment.

### Included — Fully Functional

These features work in the edge container. Use the documentation links as the reference for expected behavior during testing.

| Feature | UI Location | What to Verify | Documentation |
|---------|-------------|----------------|---------------|
| Dashboard | Dashboard (landing page) | Risk score cards, security events summary, container count | [Navigating the Console](https://open-docs.neuvector.com/navigation/navigation) |
| Network Activity | Network Activity | Container network map renders, connections visible | [Navigating the Console](https://open-docs.neuvector.com/navigation/navigation) |
| Assets — Nodes | Assets > Nodes | Host node detected with OS and kernel info | [Navigating the Console](https://open-docs.neuvector.com/navigation/navigation) |
| Assets — Containers | Assets > Containers | Running containers listed, including self | [Navigating the Console](https://open-docs.neuvector.com/navigation/navigation) |
| Security Modes | Policy | Discover / Monitor / Protect mode switching | [Modes](https://open-docs.neuvector.com/policy/modes) |
| Groups | Policy > Groups | Default groups created (e.g. `nodes`, `nv.neuvector`) | [Groups](https://open-docs.neuvector.com/policy/groups) |
| Network Rules | Policy > Network Rules | Default rules exist, can create/edit/delete rules | [Network Rules](https://open-docs.neuvector.com/policy/networkrules) |
| Process Profile Rules | Policy > Groups > process rules | Per-group process allow/deny rules | [Process Profile Rules](https://open-docs.neuvector.com/policy/processrules) |
| File Access Rules | Policy > Groups > file rules | Per-group file access monitoring rules — note: NeuVector's own predefined sensitive files (e.g. `/etc/passwd`, `/etc/hosts`, `/etc/resolv.conf`) are alert-only by design and never block-enforced, even with a custom rule on the same path; blocking works on user-configured custom paths | [File Access Rules](https://open-docs.neuvector.com/policy/filerules) |
| Response Rules | Policy > Response Rules | Automated actions on security events | [Response Rules](https://open-docs.neuvector.com/policy/responserules) |
| DLP / WAF Sensors | Policy > DLP/WAF | Data loss prevention and WAF sensor configuration | [DLP & WAF Sensors](https://open-docs.neuvector.com/policy/dlp) |
| Network Threats | Security Risks | Threat signature detection | [Network Threat Signatures](https://open-docs.neuvector.com/policy/threats) |
| Users & Roles | Settings > Users & Roles | Create/edit/delete users, assign roles | [Users and Roles](https://open-docs.neuvector.com/configuration/users) |
| System Configuration | Settings > Configuration | Syslog, webhooks, policy mode defaults, session timeout | [Configuration](https://open-docs.neuvector.com/configuration/console) |
| Notifications & Events | Notifications | Security events, risk reports, event logs | [Reporting & Notifications](https://open-docs.neuvector.com/reporting/reporting) |
| REST API | Port 10443 | All API endpoints — see [Quickstart.md](Quickstart.md#key-api-endpoints) | [REST API & Automation](https://open-docs.neuvector.com/automation/automation) |
| CIS Benchmarks / Compliance | Security Risks > Compliance | `docker_bench_status: "finished"` on `GET /v1/host`, real findings from `GET /v1/compliance/asset` — confirmed live (2026-09-30), works without the CVE scanner component (a separate feature, see below) | [Scanning & Compliance](https://open-docs.neuvector.com/scanning/scanning) |

### Not Available in Edge

These features are removed or not applicable. They may appear in the UI menu but will show empty results or not function. This is expected behavior — do not file bugs for these.

| Feature | Reason | Documentation |
|---------|--------|---------------|
| Vulnerability Scanning (CVE database) | Scanner component removed to reduce image size — confirmed live, `scanners: 0` in `/v1/system/summary`. Does not affect CIS Benchmarks/Compliance, a separate feature — see the Included table above | [Scanning & Compliance](https://open-docs.neuvector.com/scanning/scanning) |
| Registry Scanning | Requires the CVE scanner component above | [Registry Scanning](https://open-docs.neuvector.com/scanning/registry) |
| Admission Control | Kubernetes-only feature, not applicable to Docker | [Admission Controls](https://open-docs.neuvector.com/policy/admission) |
| Multi-Cluster Federation | Single-node deployment only | [Multi-Cluster Management](https://open-docs.neuvector.com/navigation/multicluster) |
| Custom Compliance Checks | Requires scanner component | [Custom Compliance](https://open-docs.neuvector.com/policy/customcompliance) |
| CRD Policies | Kubernetes-only feature | [CRD Definitions](https://open-docs.neuvector.com/policy/usingcrd) |

### Additional Resources

| Topic | Link |
|-------|------|
| Security Policy Overview | [open-docs.neuvector.com/policy/overview](https://open-docs.neuvector.com/policy/overview) |
| Testing NeuVector (official guide) | [open-docs.neuvector.com/testing/testing](https://open-docs.neuvector.com/testing/testing) |
| Allinone Special Use Cases | [open-docs.neuvector.com/special/general](https://open-docs.neuvector.com/special/general) |
| Improve Security Risk Score | [open-docs.neuvector.com/navigation/improve_score](https://open-docs.neuvector.com/navigation/improve_score) |
| CVE Database Sources | [open-docs.neuvector.com/scanning/cve_sources](https://open-docs.neuvector.com/scanning/cve_sources) |

## Limitations

### Functional

| Limitation | Description |
|------------|-------------|
| Single-node only | Multi-node cluster deployment not supported |
| No CVE scanner | Vulnerability scanner (CVE database) not included — CIS Benchmarks/Compliance is a separate feature and does work, see the Included table above |
| No upgrader | In-place upgrades not supported; redeploy with new image |

### Operational

| Limitation | Description |
|------------|-------------|
| Privileged mode | Container requires privileged mode |
| Host PID namespace | Container requires host PID namespace |
| Root access | `docker`/`docker compose` commands typically require sudo/root access |
| One instance per host | The container uses a fixed `hostname: nv` and `CLUSTER_JOIN_ADDR=nv` for its (single-node) cluster identity, and `pid: host` gives it visibility into every process on the machine. Running a second NeuVector container on the *same physical host* — even against a separate `/var/neuvector` path — collides on both, and the second instance's enforcer/hosts/workloads never populate (`system/summary` reports `enforcers: 0` indefinitely). Confirmed live 2026-09-28. Stop or remove any existing NeuVector container on a host before deploying another to it. |

### Compatibility

| Limitation | Description |
|------------|-------------|
| Docker only | Kubernetes deployment not supported |
| Linux only | Windows and macOS not supported as container hosts |

## Release Notes

See [RELEASE_NOTES.md](RELEASE_NOTES.md) for version history and details.

## License

These tools are provided under the Apache 2.0 License.

NeuVector is an open source container security platform by SUSE.

- Project: https://neuvector.com
- Source: https://github.com/neuvector/neuvector
- Documentation: https://open-docs.neuvector.com/
- REST API: https://open-docs.neuvector.com/automation/automation
