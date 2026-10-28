# Kubernetes plan

What the k3s cluster is for, what is done, and what comes next, in order.
Day-by-day detail and the reasoning behind each choice live in
[`docs/DAILY-NOTES.md`](../DAILY-NOTES.md) (DN-26, DN-27, DN-28); this file is
the map.

## Purpose

**A learning lab, decided 2026-10-28.** The live site stays on the `stockradar`
`e2-micro` VM (DN-28): it runs both daily analyses in ~27 min and can be free
after the region move. The cluster costs ~₹2,400/month on an `e2-medium` and is
never free, so it is not a replacement today.

Work that also improves the live app (the scheduler split, a shared run lock)
is worth doing either way. Work that only matters for running in production on
Kubernetes (Postgres, cutover) waits for an explicit decision to go that way.

Ground rules (DN-26, [k8s lab isolation]):
- The live VM, its scheduler and its runs are never touched by k8s work.
- No project-wide GCP changes (firewall, network, IAM); scope everything to
  the lab VM.
- The lab runs no scheduled analysis and has no Gmail secret, so it can never
  email.
- Reachable over the tailnet only (`tailscale serve`), never Funnel.

## Current state

| | |
|---|---|
| VM | `stockradar-k8s`, `e2-medium`, `asia-south1-c`, project `stock-radar-512711`. **Stop it when not in use** — only the disk is billed while stopped |
| Cluster | k3s single node, Traefik ingress, `local-path` storage |
| URL | `https://stockradar-k8s.tail7679a0.ts.net` (tailnet only) |
| Manifests | `deploy/k8s/lab/` — `kubectl apply -k deploy/k8s/lab` |
| laptop | `kubectl` context `stockradar-k8s` |
| Images | pinned to a commit SHA in `kustomization.yaml` (`images:`) |

Request path:

```
tailnet --HTTPS--> tailscale serve (VM) --> Traefik :80 --> frontend (nginx)
        --/api/, /auth/--> backend Service :8000
```

nginx forwards only `/api/` and `/auth/` to the backend. Any other backend
path is reachable inside the cluster and nowhere else — step 2 relies on this.

## Done

| Date | What | Ref |
|---|---|---|
| 2026-10-27 | Lab VM + k3s + Tailscale; Kustomize manifests (backend 1 replica `Recreate` on a 5 Gi PVC, frontend 2 replicas, ingress) | DN-26, `7731acd` |
| 2026-10-27 | Lenient probes so a CPU-bound run isn't killed mid-way | `cff2b5f` |
| 2026-10-27 | Full analysis on the lab: 7m40s, peak 325 Mi | DN-28 |
| 2026-10-28 | `SCHEDULER_ENABLED` flag replaces the lab's APScheduler monkeypatch | `672b285` |
| 2026-10-28 | CI builds `alt-approach` as `:<sha>` only; `:latest` stays `master`-only. Lab pinned to a SHA | `672b285`, `23a37a0` |

## Next steps

### 1. Scheduler out of the API process → CronJob

Goal: the API pods serve requests only; a Kubernetes CronJob decides when the
analysis runs. That is the precondition for ever running more than one API
replica, and on the lab it replaces "no schedule at all" with a schedule that
is visible in `kubectl get cronjobs`.

Design:
- **Internal trigger route** on the backend, e.g. `POST /internal/run`,
  authorised by a shared token (`INTERNAL_TRIGGER_TOKEN`, from a Secret),
  compared in constant time. Not under `/api/`, so nginx never forwards it —
  unreachable from outside on both the lab and the live VM. Disabled entirely
  when the token is unset (the default), so the live VM gains no new surface.
  It calls the same `analysis_job`, with its own audit-log entry.
- Why not reuse `/api/trigger`: it needs a browser session and a CSRF token by
  design (SR-1), and it is rate-limited for humans.
- Why not run the analysis in the CronJob pod itself: the backend holds the
  DuckDB lake open as its only writer, so a second process can't write it.
  That changes only with step 4.
- **CronJob** `stockradar-analysis`: `09:15` and `15:30` `Asia/Kolkata`
  (`timeZone:` field), Mon–Fri, `concurrencyPolicy: Forbid`,
  `startingDeadlineSeconds` ~4h (matching today's misfire grace), a tiny
  `curl` image posting to `http://stockradar-backend:8000/internal/run`.
- **Lab email stays off**: the internal route must honour an `email=false`
  parameter, and the lab still has no Gmail secret as a backstop.

Checks before calling it done: the route returns 404 through the public URL;
401 without the token inside the cluster; a manual
`kubectl create job --from=cronjob/stockradar-analysis` produces a saved run.

### 2. Shared run lock

Today "a run is in progress" is a Python global (`_running` in `api.py`).
It is lost on restart, and a CronJob firing while someone clicks *Run* can
get past it. The 2026-07-11 overlap incident (runs 44/45) is the precedent.

Design: a lock row in the database (owner, started-at, heartbeat), taken
atomically before a run and refreshed while it runs; a run whose heartbeat is
older than a few minutes is treated as dead. This replaces the 90-minute
`ANALYSIS_STALE_MINUTES` guess with liveness, which was the deferred fix from
July. Helps the live VM as much as the lab.

### 3. Lab hardening

- **Client IP through three proxies** (tailscale serve → Traefik → nginx):
  verify how `X-Forwarded-For` arrives, the way DN-20 verified Funnel, then
  set `TRUST_PROXY_HEADERS=true`. Until then every request shares one
  rate-limit bucket.
- **Tailscale key expiry** for `stockradar-k8s` (expires 2027-04-24): disable
  in the Tailscale admin console. The user's action.
- **Resources from measurements**: requests/limits from the measured peak
  (~345 Mi) instead of the current 2.5 Gi guess.
- **Pull time**: the backend image is ~894 MB and took 5 min to pull on the
  lab. Allow for it in rollouts, or slim the image.

### 4. Shared state → Postgres *(production path only)*

`runs` (SQLite) and the DuckDB lake are single-node, single-writer files on a
`local-path` volume. Moving the shared tables to Postgres is the only way to
more than one backend replica, and the largest piece of work here. Not
started; needs the production decision first.

### 5. Cutover *(production path only)*

Copy `runs` and the lake from the live VM; add the Gmail secret; enable the
CronJob with email on; move Funnel and the OAuth redirect URI; run both side
by side for a few days; retire the old VM. Must also fit the budget decision
due before the trial ends (~2027-01-23).

## Open decisions

- **Learning lab or future production?** Decided *lab* for now (2026-10-28).
  Revisit before 2027-01-23 alongside the region move.
- Where the CronJob lives on the live VM, if ever: Docker Compose has no
  CronJob, so the live VM keeps `SCHEDULER_ENABLED=true` until a cutover.

[k8s lab isolation]: ../DAILY-NOTES.md#dn-26
