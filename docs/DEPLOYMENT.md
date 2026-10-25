# Deployment — Oracle Cloud Always Free

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

## 3. Point a hostname at it

Google will not accept the bare IP. You need a real hostname with a valid
public TLD resolving to the instance's public IP.

- **Free:** a dynamic-DNS subdomain such as `yourname.duckdns.org`. The TLD is
  `.org`, which satisfies Google's public-suffix rule on a plain reading —
  but **verify before building on it**: paste the redirect URI into Google
  Cloud Console (step 5); it validates on save and rejects immediately if
  unhappy. 30 seconds, and it beats guessing.
- **Paid, no ambiguity:** any domain you own, with an A record to the IP.

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

It must match scheme, host, port and path **exactly**. Keep the existing
`http://localhost:8000/auth/callback` entry so local development still works —
a client can hold several.

This is also the moment the DuckDNS question answers itself: if the console
accepts the URI, the hostname is fine.

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
