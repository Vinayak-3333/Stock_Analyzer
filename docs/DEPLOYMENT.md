# Deployment

Two paths are supported. Both end up at a **single HTTPS origin** serving the
SPA and API, locked to your Google account — Google OIDC refuses non-HTTPS
redirect URIs and raw IPs, so TLS on a real hostname is mandatory either way.

| | **Tailscale Funnel** (`docker-compose.funnel.yml`) | **Cloud VM** (`docker-compose.cloud.yml`) |
|---|---|---|
| Cost | Free | Free tier or ~€4/mo |
| Needs a card | **No** | Yes |
| TLS | Funnel handles it | Caddy + Let's Encrypt |
| Open ports / DNS | None | 80, 443 + a DNS record |
| Works behind CGNAT | Yes | N/A |
| Uptime | Only while the machine is awake | Always on |

Funnel is the fastest way to a working HTTPS deployment and needs no cloud
account at all. Its one real limitation is uptime: a sleeping machine takes the
site down *and* freezes the APScheduler timers, so scheduled runs are
best-effort. Move to a cloud VM when that matters.

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

## 8. Afterwards — check whether the client IP survives

`TRUST_PROXY_HEADERS` is left **off** here, because whether Funnel forwards a
client `X-Forwarded-For` is unverified for this project, and trusting the
header when the proxy does not set it reliably is worse than not trusting it.
After a login:

```bash
docker compose -f docker-compose.funnel.yml exec stockradar-backend \
  tail -5 /app/backend/data/audit.log
```

If `ip=` shows a real public client address, set `TRUST_PROXY_HEADERS=true` in
`.env` and recreate — anonymous callers then get per-IP rate-limit buckets
instead of sharing one. If it shows a loopback or Tailscale address, leave it
off; the per-identity limit still applies once logged in, which is what matters
for a single-user deployment.

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

# Part 2 — Oracle Cloud Always Free

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
