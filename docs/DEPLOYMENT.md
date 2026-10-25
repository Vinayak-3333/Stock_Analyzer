# Deployment

Two paths are supported. Both end up at a **single HTTPS origin** serving the
SPA and API, locked to your Google account — Google OIDC refuses non-HTTPS
redirect URIs and raw IPs, so TLS on a real hostname is mandatory either way.

| | **Part 1** — Funnel on your machine | **Part 2** — GCP + Funnel | **Part 3** — cloud VM + Caddy |
|---|---|---|---|
| Compose file | `funnel.yml` | `funnel.yml` | `cloud.yml` |
| Cost | Free | Free tier | Free tier or ~€4/mo |
| Needs a card | **No** | Yes | Yes |
| TLS | Funnel | Funnel | Caddy + Let's Encrypt |
| Open ports / DNS | None | None | 80, 443 + DNS record |
| Custom domain | No (`*.ts.net`) | No (`*.ts.net`) | **Yes** |
| Uptime | While the machine is awake | Always on | Always on |

**Start with Part 1** — fastest route to a working HTTPS deployment, no cloud
account at all. Its only real limitation is uptime: a sleeping machine takes
the site down *and* freezes the APScheduler timers, so scheduled runs are
best-effort.

**Part 2 is the upgrade** and is the same stack on a host that never sleeps.
Keeping Funnel on the VM means no Caddy, no ACME, no DNS record and no open
firewall ports — only `APP_DOMAIN` changes.

**Part 3 only if you want a custom domain.** That is the one thing Funnel
cannot do, since it serves `*.ts.net` exclusively.

---

# Part 1 — Tailscale Funnel

```
Internet ──TLS──> Tailscale Funnel ──> nginx 127.0.0.1:8080 ──> backend:8000
```

## 1. Install Tailscale and enable the features Funnel needs

Install from <https://tailscale.com/download> and sign in (Google/GitHub — no
card). Then, in the admin console, two things must be on:

- **DNS → MagicDNS**, and **HTTPS Certificates**. Funnel cannot issue a
  certificate without these.
- **Funnel** itself. It is gated by a node attribute; the CLI prints a direct
  enable link the first time you run it, which is the easiest route.

## 2. Find your Funnel hostname

```bash
tailscale status            # the first line is this machine; note its name
```

Your public hostname is `<device>.<tailnet>.ts.net`, e.g.
`desktop-abc.tail1a2b3c.ts.net`. **Get this before step 3** — the backend bakes
it into `OIDC_REDIRECT_URI` at startup.

On Windows the CLI lives at `C:\Program Files\Tailscale\tailscale.exe` if it is
not on your `PATH`.

## 3. Configure

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # SESSION_SECRET
```

Set `APP_DOMAIN` to the `ts.net` hostname, plus `SESSION_SECRET`, the three
`GMAIL_*` and the two `OIDC_CLIENT_*` values. Leave `OIDC_REDIRECT_URI`,
`FRONTEND_URL`, `CORS_ALLOW_ORIGINS` and the cookie flags unset — compose
derives them from `APP_DOMAIN`.

## 4. Register the redirect URI

Google Cloud Console → APIs & Services → Credentials → your OAuth 2.0 Web
client → **Authorized redirect URIs** → Add:

```
https://<device>.<tailnet>.ts.net/auth/callback
```

Exact match — `https`, no trailing slash. Keep the localhost entry for dev.

## 5. Stop the local backend first

If you have been running `uvicorn api:app` directly, stop it. Two backends
means two schedulers and duplicate alert emails from the same account.

## 6. Start

```bash
docker compose -f docker-compose.funnel.yml up -d --build
curl -s http://127.0.0.1:8080/api/health          # {"status":"ok"} before exposing it

tailscale funnel --bg 8080
tailscale funnel status                            # prints the public URL
```

The frontend is bound to **127.0.0.1 only**, so nothing is reachable until
Funnel is explicitly started — and nothing is exposed to the local network even
then.

## 7. Verify

```bash
curl -I  https://<device>.<tailnet>.ts.net/api/health      # 200, valid cert
curl -si https://<device>.<tailnet>.ts.net/auth/me | head -1   # 401 when logged out
```

Then open the URL in a browser, sign in with Google, and confirm the dashboard
loads with Market Pulse populated.

## 8. The client-IP question — already settled

`TRUST_PROXY_HEADERS` defaults to **on** for this path. That was verified on
2026-10-25 rather than assumed, and the reasoning is worth keeping because the
default looks dangerous out of context:

| Condition | `ip=` recorded in `audit.log` |
|---|---|
| trust off | `172.19.0.3` — the nginx container; every caller in one bucket |
| trust on | a real public client IPv6 — Funnel *does* forward the header |
| trust on + forged `X-Forwarded-For` | the **real** address, not the forged one |

nginx appends with `$proxy_add_x_forwarded_for`, so a client-supplied header
would normally end up left-most and attacker-controlled — which is exactly why
the limiter distrusts it by default. Tailscale **overwrites** the header
instead of appending, so the forged value never survives. With the backend
publishing no port, the only path in is Funnel → nginx → backend, which makes
trusting it safe and buys real per-client rate limiting.

Confirm it on your own deployment after a login:

```bash
docker compose -f docker-compose.funnel.yml exec stockradar-backend \
  tail -5 /app/backend/data/audit.log
```

Expect a real client address in `ip=`. If you instead see a Docker-internal
address like `172.x.x.x`, set `TRUST_PROXY_HEADERS=false` and re-check — a
wrong value here costs accuracy in the audit trail and per-IP limiting, not
security.

## Notes

- **History starts empty.** The `backend_data` volume is fresh, so the dashboard
  shows no runs until the first one completes. To carry over your local history,
  with the stack stopped:

  ```bash
  docker run --rm -v stock_analyzer_backend_data:/d -v "$PWD/backend/data":/src \
    alpine sh -c "cp /src/stockradar.db /d/ 2>/dev/null || true"
  ```

- **To stop publishing** without touching the containers: `tailscale funnel off`.
- **DuckDNS is not used on this path.** Keep the record for the cloud route later.

---

# Part 2 — Google Cloud `e2-micro` (always free) + Funnel

The upgrade path from Part 1: identical stack, but on a host that never sleeps.
Running Funnel **on the VM** keeps the whole Caddy/ACME/DNS/firewall layer out
of the picture — nothing from Part 3 is needed.

```
Internet ──TLS──> Funnel (on the VM) ──> nginx 127.0.0.1:8088 ──> backend:8000
```

What actually changes from the laptop deployment: `APP_DOMAIN` becomes the VM's
own `ts.net` name, and you add one more redirect URI. That is it.

## 1. Create the instance

Compute Engine → Create instance.

- **Machine type:** `e2-micro`
- **Region:** `us-west1`, `us-central1` or `us-east1` — **free only in these
  three.** Picking `asia-south1` because it is near you bills at full rate.
  Latency is irrelevant here: the pipeline runs server-side on a schedule and
  you only load the dashboard occasionally.
- **Boot disk:** Ubuntu 22.04/24.04 LTS, **30 GB, Standard persistent disk**.
  New VMs often default to `pd-balanced`, which is **not** in the free tier.
- **Firewall:** leave HTTP/HTTPS **unchecked**. Funnel needs no inbound ports —
  that is the point.

No static IP needed either, for the same reason. Tailscale handles reachability.

## 2. Prepare the box

SSH in from the console (easiest — no key handling), then:

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker

curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # prints a login URL — open it and authorise
```

**Add swap.** 1 GB of RAM is the real constraint, and swap turns a hard OOM
kill into merely-slow:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

## 3. Get the code and find the hostname

```bash
git clone https://github.com/Vinayak-3333/Stock_Analyzer.git
cd Stock_Analyzer
git checkout alt-approach        # the OIDC work is not on master

tailscale status                 # note this machine's name
```

Hostname is `<vm-name>.<tailnet>.ts.net` — same tailnet as the laptop, so the
suffix matches; only the device part differs.

## 4. Register the redirect URI

Google Cloud Console → Credentials → your OAuth client → add:

```
https://<vm-name>.<tailnet>.ts.net/auth/callback
```

Keep the laptop and localhost entries. A client can hold several, and keeping
them means you can fall back without reconfiguring Google.

## 5. Configure

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # SESSION_SECRET
nano .env
```

Set `APP_DOMAIN` to the VM's `ts.net` name, plus `SESSION_SECRET`, the three
`GMAIL_*` and two `OIDC_CLIENT_*`. Then the tuning that 1 GB and two shared
burstable vCPUs require:

```bash
TIER2_WORKERS=4               # 16 is a laptop figure
BULK_PARALLEL_CHUNKS=1
TIER2_DEADLINE_MINUTES=45     # see below
```

**`TIER2_DEADLINE_MINUTES` is the one to get right.** It is a *hard* wall-clock
budget, and a run that exceeds it completes with **partial results** rather
than failing — so a too-low value silently drops symbols rather than erroring.
Tier 2 took 203 s on the laptop; expect several times that here. Start at 45,
then lower it once you have seen a real run's duration in the logs.

You may also reuse the laptop's `.env` wholesale — only `APP_DOMAIN` must
differ. Keeping `SESSION_SECRET` identical is fine and means sessions survive a
move between hosts.

## 6. Start

Two options. **Pulling is strongly preferred on 1 GB**, because `npm ci` plus a
Vite build is the single most likely thing to OOM, and it fails as an opaque
"killed" rather than anything mentioning memory.

**Pull (recommended)** — `e2-micro` is x86_64, so the amd64 CI images fit. They
must first contain the auth code, which master-built images do not:

> GitHub → Actions → "Build Docker Images" → **Run workflow** → *Use workflow
> from:* `alt-approach` → Run.

```bash
# if the packages are private (GitHub's default):
echo <PAT_with_read:packages> | docker login ghcr.io -u <github-username> --password-stdin

docker compose -f docker-compose.funnel.yml -f docker-compose.pull.yml up -d
```

**Build on the VM** (no CI dependency, slower, riskier on 1 GB):

```bash
docker compose -f docker-compose.funnel.yml up -d --build
```

Then publish:

```bash
curl -s http://127.0.0.1:8088/api/health      # {"status":"ok"} before exposing
tailscale funnel --bg 8088
tailscale funnel status
```

## 7. Verify

```bash
curl -I https://<vm-name>.<tailnet>.ts.net/api/health      # 200, valid cert
docker compose -f docker-compose.funnel.yml logs stockradar-backend | head -20
```

Expect `Auth wired (OIDC enabled: True)` and `trust_proxy=on` in the posture
line. Then sign in through a browser and trigger a run — **watch the first one
end to end**, since that is what tells you whether the tuning above is right:

```bash
docker compose -f docker-compose.funnel.yml logs -f stockradar-backend \
  | grep -E "Tier 1:|Tier 2 progress|Tier 2 finished|deadline|Email sent"
```

If you see Tier 2 stopping at the deadline rather than at 400/400, raise
`TIER2_DEADLINE_MINUTES` or lower `DEEP_ANALYSIS_COUNT`.

Watch memory during that first run from a second shell:

```bash
free -h; docker stats --no-stream
```

For reference, idle on the laptop is ~109 MiB for the backend and ~14 MiB for
the frontend. If swap is being consumed heavily during Tier 2, drop
`TIER2_WORKERS` to 2.

## 8. Decommission the laptop deployment

Once the VM is serving, stop the laptop one so two schedulers do not both run
and email you:

```bash
# on the laptop
tailscale funnel --https=443 off
docker compose -f docker-compose.funnel.yml down
```

To carry the run history across, copy `backend/data/stockradar.db` from the
laptop volume to the VM before starting it — otherwise the dashboard begins at
run 1 again.

## Notes

- **The sleep-freeze problem disappears here.** The July nudge fix (`ec4c043`)
  exists because a sleeping laptop froze APScheduler's timers. A VM does not
  sleep, and `TZ=Asia/Kolkata` in the container means the 09:15/15:30 cron
  triggers mean what they say.
- **Free-tier billing still needs watching.** The region, the `pd-standard`
  disk and the single `e2-micro` are the three things that silently cost money
  if you get them wrong. Set a budget alert at ₹100 and you will know early.

---

# Part 3 — Oracle Cloud Always Free

Target: a single always-on VM serving the dashboard and API from **one HTTPS
origin**, locked to your Google account.

```
Internet ──TLS──> Caddy :443 ──> nginx (SPA + /api,/auth) ──> backend :8000
                  stockradar-caddy   stockradar-frontend      stockradar-backend
```

Everything is one origin on purpose. Google OIDC refuses non-HTTPS redirect
URIs and refuses raw IPs, so TLS on a real hostname is mandatory; and keeping
the SPA and API on the same origin means `SameSite=lax` cookies, no CORS, and a
single redirect URI to register. A split frontend (e.g. Vercel) would force
`SameSite=none` and a CORS allow-list for no benefit.

> **Why not the ghcr images?** The CI workflow builds **amd64 only** and only
> triggers on `master`. Oracle's Always Free capacity is Ampere **ARM64**, and
> the OIDC code is on `alt-approach` — so the published images are both the
> wrong architecture and missing auth. `docker-compose.cloud.yml` builds from
> source on the instance, which sidesteps both. Making CI publish multi-arch
> images is a reasonable follow-up, but QEMU-emulated ARM builds of a
> pandas/numpy image are slow, so don't block the first deploy on it.

---

## 1. Provision the instance

Oracle Cloud → Compute → Instances → Create.

- **Shape:** `VM.Standard.A1.Flex` (Ampere ARM). Always Free covers up to
  4 OCPU / 24 GB across all A1 instances. **2 OCPU / 12 GB** is plenty here and
  leaves headroom.
- **Image:** Canonical Ubuntu 22.04 or 24.04 (ARM build).
- **Boot volume:** 50 GB is ample; Always Free allows 200 GB total.
- Save the SSH private key when offered — you cannot retrieve it later.

> **Expect "Out of capacity" on A1.** It is the single most common Always Free
> frustration: ARM capacity in popular regions is often exhausted and creation
> fails. Retrying periodically, or picking a less busy availability domain,
> usually gets there. The AMD `E2.1.Micro` alternative is **1 GB RAM**, which a
> 2,000-symbol pandas run will almost certainly exhaust — don't substitute it.

**Confirm the shape is actually free.** Always Free resources are labelled
"Always Free eligible" in the console. Anything beyond the A1 allowance bills
normally, and an upgraded account will silently charge rather than refuse.

## 2. Open the ports — both layers

This trips nearly everyone: Oracle filters traffic in **two** independent
places and opening only one leaves the port closed.

**a. VCN security list** (Networking → VCN → Subnet → Security List → Add
Ingress Rules), source `0.0.0.0/0`, TCP:

| Port | Why |
|------|-----|
| 80 | Let's Encrypt HTTP-01 challenge, and the HTTPS redirect |
| 443 | the actual site |

Port 22 is already open from the default rules.

**b. The instance's own firewall.** Oracle's Ubuntu images ship with iptables
rules that drop everything except SSH, regardless of the security list:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

Do **not** skip this because the security list looks right — Caddy's
certificate request will fail with an opaque ACME timeout if port 80 is
unreachable.

## 3. Point a hostname at it — DuckDNS, free

Google will not accept the bare IP, so you need a real hostname. **DuckDNS** is
free, takes two minutes, and needs no domain purchase.

Google's rule is that the host's *public suffix* must be recognised. For
`yourname.duckdns.org` that holds either way — if `duckdns.org` is itself a PSL
entry that is the suffix and it is listed; if not, the suffix is `.org`, which
certainly is. What Google rejects is hosts with no valid suffix at all
(`.local`, `.test`, `.internal`, invented TLDs). Homelab setups use DuckDNS
with Google OAuth routinely.

**First, make the IP permanent.** Oracle hands out an *ephemeral* public IP by
default, and it changes when the instance is stopped and started — which would
silently break both DNS and the registered redirect URI. Reserving it is free:

> Instance → Attached VNICs → primary VNIC → IPv4 Addresses → edit the public
> IP → change **Ephemeral** to **Reserved**.

**Then:**

1. Go to <https://www.duckdns.org> and sign in (Google/GitHub — no password).
2. Pick a subdomain, e.g. `stockradar-vk` → gives `stockradar-vk.duckdns.org`.
3. Put the instance's reserved public IP in the **current ip** box and update.
4. Confirm it resolves before going further:

   ```bash
   dig +short stockradar-vk.duckdns.org     # must print your instance IP
   ```

   DNS must be correct *before* Caddy starts, or the Let's Encrypt HTTP-01
   challenge fails and you will burn attempts against the rate limit.

With a reserved IP the record never needs updating. If you ever use an
ephemeral IP instead, keep it current with a cron entry (token is on the
DuckDNS dashboard):

```bash
*/5 * * * * curl -ks "https://www.duckdns.org/update?domains=stockradar-vk&token=<TOKEN>&ip=" >/dev/null
```

**If you later buy a domain,** nothing here changes except `APP_DOMAIN` and
adding the new redirect URI in Google Console.

## 4. Install Docker and get the code

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker

git clone https://github.com/Vinayak-3333/Stock_Analyzer.git
cd Stock_Analyzer
git checkout alt-approach          # the OIDC work is NOT on master
```

## 5. Register the redirect URI with Google

Google Cloud Console → APIs & Services → Credentials → your OAuth 2.0 Web
client → **Authorized redirect URIs** → Add:

```
https://<APP_DOMAIN>/auth/callback
```

e.g. `https://stockradar-vk.duckdns.org/auth/callback`.

It must match scheme, host, port and path **exactly** — no trailing slash, and
`https` not `http`. A mismatch produces `Error 400: redirect_uri_mismatch` at
login, which is by far the most common failure here and is always an
exact-string problem, not a domain problem.

Keep the existing `http://localhost:8000/auth/callback` entry so local
development still works — a client can hold several.

## 6. Configure

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # SESSION_SECRET
nano .env
```

Set `APP_DOMAIN`, `ACME_EMAIL`, `SESSION_SECRET`, the three `GMAIL_*` values and
the two `OIDC_CLIENT_*` values. Leave `OIDC_REDIRECT_URI`, `FRONTEND_URL`,
`CORS_ALLOW_ORIGINS` and the cookie flags unset — `docker-compose.cloud.yml`
derives them from `APP_DOMAIN` so they cannot drift out of sync.

`SESSION_SECRET` must be **stable**. If it changes, every existing session is
silently invalidated and everyone is logged out.

## 7. Build and start

```bash
docker compose -f docker-compose.cloud.yml up -d --build
```

First build takes a while — it compiles nothing (numpy/pandas/duckdb all ship
aarch64 wheels) but does download a lot. Then:

```bash
docker compose -f docker-compose.cloud.yml ps
docker compose -f docker-compose.cloud.yml logs -f caddy      # cert issuance
docker compose -f docker-compose.cloud.yml logs stockradar-backend | head -30
```

The backend log should show, in order:

```
Env loaded from /app/.env
Auth wired (OIDC enabled: True)          <- False means OIDC_CLIENT_* didn't reach the container
Security posture: headers=on hsts=on ratelimit=on(...) docs=disabled trust_proxy=on
Scheduler started - jobs: [... next run at: ... 09:15:00 IST]
```

## 8. Verify

```bash
curl -I  https://<APP_DOMAIN>/api/health     # 200, and a valid certificate
curl -si https://<APP_DOMAIN>/auth/me | head -1   # 401 — correct when logged out
curl -I  http://<APP_DOMAIN>/                # 308 redirect to https
```

Then in a browser: open `https://<APP_DOMAIN>`, expect the login screen, sign
in with Google, and confirm the dashboard loads with the Market Pulse panel
populated. Finally, confirm the hardening survived the proxy chain:

```bash
curl -sI https://<APP_DOMAIN>/api/health | grep -iE 'strict-transport|x-frame|content-security'
```

HSTS should now be present (it is absent locally by design).

## 9. Operating it

```bash
# update to the latest alt-approach
git pull && docker compose -f docker-compose.cloud.yml up -d --build

# audit trail — who triggered runs, failed logins, rate-limit blocks
docker compose -f docker-compose.cloud.yml exec stockradar-backend \
  tail -40 /app/backend/data/audit.log

# back up the lake + runs DB (named volume, survives recreate but not deletion)
docker run --rm -v stock_analyzer_backend_data:/d -v $PWD:/b alpine \
  tar czf /b/backup-$(date +%F).tar.gz -C /d .
```

**A real benefit of moving off the laptop:** the scheduler sleep-freeze problem
disappears. The July nudge fix (`ec4c043`) existed because a sleeping laptop
froze APScheduler's timers; an always-on VM simply does not sleep. The 09:15
and 15:30 IST slots should fire reliably — the container's `TZ` is set to
`Asia/Kolkata` so the cron triggers mean what they say.

## 10. Known gaps

- **Not yet merged to `master`.** This deploys `alt-approach`. Phase 6 of the
  security plan covers the merge; once this deployment is proven, that becomes
  unblocked — see `docs/security/OIDC-SECURITY-REQUIREMENTS.md`.
- **CI publishes amd64-only images from `master`.** Until that changes, the
  instance builds from source. Adding
  `platforms: linux/amd64,linux/arm64` plus `docker/setup-qemu-action` to
  `.github/workflows/docker-images.yml` would let the instance pull instead.
- **No automated backups.** The `backend_data` volume holds the DuckDB lake,
  the `runs` table and the audit log. The tar command above is manual.
