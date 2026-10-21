# Deploy step-by-step

Production deployment of **mlops-webshop-stack** on a fresh **Ubuntu 22.04**
VPS. This is the authoritative, ordered procedure. The
[README](../README.md) has the condensed version and the architecture notes.

> **Before you start.** Read this once. The order of the steps is load-bearing:
> several scripts depend on the ones before them, and the reasons are given
> per step. Skipping a step produces a stack that looks installed but is
> broken in ways that are hard to diagnose later.

---

## 0. Provision the server

| Item | Requirement |
|---|---|
| OS | Ubuntu 22.04 LTS (x86_64 or arm64) |
| RAM | 8 GB minimum, 16 GB recommended |
| Disk | 40 GB SSD |
| CPU | 2 vCPU minimum, 4 vCPU if running local inference |
| Network | Public IP, inbound 22/80/443 only |

```bash
# Create the non-root deploy user and give it sudo.
adduser --gecos "" --disabled-password deploy
usermod -aG sudo deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
# copy your public key to /home/deploy/.ssh/authorized_keys
```

**DNS** — this is the part people miss. A wildcard certificate needs a
wildcard DNS record:

```bash
# Apex + wildcard A records -> the VPS public IP
#   xxx.com        A  <VPS_IP>
#   *.xxx.com      A  <VPS_IP>
```

Verify propagation **before** continuing:

```bash
dig +short xxx.com
dig +short api.xxx.com     # must resolve too
```

If `api.xxx.com` does not resolve, the TLS step will fail later with a
cryptic certbot error.

---

## 1. Clone and configure

```bash
sudo mkdir -p /opt && cd /opt
sudo git clone https://github.com/sradu2003/mlops-webshop-stack.git
cd mlops-webshop-stack
sudo cp .env.example .env
sudo nano .env
```

The root `.env` is the **single source of truth** for the whole deployment.
`scripts/30_install_apps.sh` renders it into each service's own `.env`, so
you only edit this one file.

### Secrets you must set before continuing

| Variable | Where | Generate with |
|---|---|---|
| `JWT_SECRET` | `.env` | `openssl rand -hex 32` |
| `ANON_SALT` | `.env` | `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | `.env` | `openssl rand -base64 24` |
| `MARIADB_PASSWORD` | `.env` | `openssl rand -base64 24` |

```bash
# Quick way to set them:
sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$(openssl rand -hex 32)|" .env
sed -i "s|^ANON_SALT=.*|ANON_SALT=$(openssl rand -hex 32)|" .env
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')|" .env
sed -i "s|^MARIADB_PASSWORD=.*|MARIADB_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')|" .env
```

Also set `DOMAIN` and `LETSENCRYPT_EMAIL` to your real values, and fix
`DATABASE_URL` so it uses the same password as `POSTGRES_PASSWORD`:

```
DOMAIN=example.com
LETSENCRYPT_EMAIL=admin@example.com
DATABASE_URL=postgresql+psycopg://shop:<YOUR_POSTGRES_PASSWORD>@127.0.0.1:5432/shopdb
```

> **The API refuses to start with a placeholder `JWT_SECRET` or `ANON_SALT`.**
> This is enforced by a Pydantic validator, not by convention — it is a
> deliberate fail-fast so a default secret can never reach production.

Now the Docker stack's own configuration:

```bash
sudo cp docker/.env.example docker/.env
sudo nano docker/.env
```

Set at least `GRAFANA_ADMIN_PASSWORD`, `LISTMONK_DB_PASSWORD` and
`LISTMONK_ADMIN_PASSWORD`.

---

## 2. Bootstrap the base system

```bash
sudo bash scripts/00_bootstrap.sh
```

Installs the base packages, enables `ufw` (allow 22/80/443 only), starts
fail2ban and creates a 2 GB swapfile. **Run this first** — the firewall
should be up before any service binds a public port.

---

## 3. Install the toolchain

```bash
sudo bash scripts/15_install_node.sh
sudo bash scripts/15_install_python.sh
```

**Why this is not optional on Ubuntu 22.04:**

- The distro ships `nodejs` **12.22.9**. Next.js 14 requires **≥ 18.17**
  and refuses to start on 12.x. `15_install_node.sh` installs the upstream
  Node 20 LTS and fails loudly if the result is still too old.
- The archive only carries `python3.11 3.11.0~rc1` — a **release
  candidate**, not a stable build, and `python3.11-venv` is missing on some
  architectures. `15_install_python.sh` installs a stable 3.11 from the
  deadsnakes PPA and aborts if it only got an RC.

Both scripts exit non-zero on failure, so the rest of the deploy stops
rather than producing a half-installed stack.

---

## 4. TLS — wildcard certificate

```bash
sudo bash scripts/10_ssl_wildcard.sh example.com admin@example.com cloudflare
```

The third argument is the **DNS plugin**, and it is required:

| Plugin | Package | Needs |
|---|---|---|
| `cloudflare` | `certbot-dns-cloudflare` | `CLOUDFLARE_API_TOKEN` in `/root/.secrets/cf.ini` |
| `route53` | `certbot-dns-route53` | AWS credentials |
| `duckdns` | `certbot-dns-duckdns` | `DUCKDNS_TOKEN` in `/root/.secrets/duckdns.ini` |
| `manual` | — | **Interactive only — cannot renew. Do not use in production.** |

A wildcard certificate cannot be issued over HTTP-01, so `--manual` is the
only alternative and it cannot be renewed unattended. The script warns and
the `ssl-renew.timer` will fail on every attempt.

The script creates a **self-signed fallback** at `/etc/ssl/selfsigned` and
symlinks it into `/etc/ssl/shared` before issuance, so nginx can start even
if certbot fails. After successful issuance the symlinks are repointed at
the Let's Encrypt certificate. Verify:

```bash
openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt /etc/ssl/shared/fullchain.pem
```

Renewal is wired to a systemd timer:

```bash
systemctl list-timers ssl-renew.timer
```

---

## 5. Native services

```bash
sudo bash scripts/20_install_native.sh
```

Installs and configures nginx, Apache, MariaDB, PostgreSQL, PHP and
phpMyAdmin, applies low-RAM tuning for an 8 GB VPS, and validates **both**
nginx and Apache configs *before* restarting them, so a bad file cannot
take the site down.

---

## 6. Databases

```bash
sudo bash scripts/init_databases.sh
```

Creates the PostgreSQL role and database (and enables `pgvector` if the
package is available) plus the MariaDB database and user for WordPress.

Requires the root `.env` from step 1 — it reads `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `POSTGRES_DB`, `MARIADB_DATABASE`, `MARIADB_USER` and
`MARIADB_PASSWORD` from it and aborts if any are missing.

---

## 7. Application services

```bash
sudo bash scripts/30_install_apps.sh
```

Installs the Python API, Streamlit dashboard, Node API, Next.js RAG service
and JupyterLab as hardened systemd units.

This script:

- verifies the toolchain from step 3 is new enough, and aborts if not;
- `rsync`s the app sources into `/opt/apps`;
- creates a venv / installs dependencies for the Python services;
- **`npm ci` for the Node services** — this requires the committed
  `package-lock.json`; the script fails explicitly if a lockfile is missing;
- **renders the real secrets from the root `.env` into each app's `.env`**,
  backing up any previous version — so the API gets your actual
  `JWT_SECRET`, not the `change_me_jwt_secret` placeholder;
- generates a JupyterLab access token into `/opt/jupyter/.env` if there is
  not one already.

Check the result:

```bash
systemctl status python-api node-api nextjs-rag streamlit-app jupyterlab
```

---

## 8. Docker stack

```bash
sudo bash scripts/40_install_docker_stack.sh
```

Installs Docker Engine + the Compose v2 plugin, creates `docker/.env` from
the template if it is missing, validates the compose file, and brings the
stack up with health-gated startup. It then pulls the Ollama model.

The stack is 7 long-running services plus one one-shot initialiser:

| Service | Port (loopback) | Purpose |
|---|---|---|
| `ollama` | 11434 | Local LLM inference |
| `open-webui` | 8085 | Chat / AI workspace |
| `listmonk-db` | — | Postgres (pgvector) for listmonk + Open WebUI |
| `listmonk` | 9000 | Email campaigns |
| `listmonk-init` | — | One-shot schema install; must exit 0 |
| `prometheus` | 9091 | Metrics |
| `grafana` | 3030 | Dashboards |
| `node-exporter` | — | Host metrics |

> **`listmonk-init` is not optional.** listmonk ships with an empty schema
> and crash-loops forever with *"the database does not appear to be setup.
> Run --install."* The one-shot service runs `yes | ./listmonk --install`
> and `listmonk` waits for it to complete successfully. If you ever wipe
> the volume, the init scripts only run again on an **empty** data
> directory — use `docker compose down -v` to force a clean re-init.

Verify:

```bash
cd /opt/mlops-webshop-stack/docker
docker compose ps
```

Every service should report `healthy`, and `listmonk-init` should show
`Exited (0)`.

---

## 9. Harden the host

```bash
sudo bash scripts/50_harden.sh
```

Disables SSH password auth, configures fail2ban (sshd, nginx-http-auth and
nginx-limit-req jails), applies kernel/network sysctls, disables core
dumps, stops apt from auto-installing recommended packages and enables
unattended security upgrades.

---

## 10. Verify the deployment

```bash
sudo bash scripts/70_healthcheck.sh
```

This is the acceptance test. It checks systemd units, TCP ports, HTTP
endpoints, container states, Prometheus target health, the TLS chain and
expiry, the Jupyter token, that no management port is bound to `0.0.0.0`,
that `ufw` is active, and backup freshness.

**Exit code `0` means healthy.** Every check must pass before you go live.

### Endpoints

| URL | Backend | Public? |
|---|---|---|
| `https://xxx.com` | Apache / WordPress :8081 | Yes |
| `https://blog.xxx.com` | Apache / WordPress :8082 | Yes |
| `https://docs.xxx.com` | Apache / WordPress :8083 | Yes |
| `https://api.xxx.com` | Python FastAPI :3000 | Yes |
| `https://node.xxx.com` | Node API :8000 | Yes |
| `https://app.xxx.com` | Streamlit :3001 | Yes |
| `https://rag.xxx.com` | Next.js RAG :8001 | Yes |
| `https://jupyter.xxx.com` | JupyterLab :8888 | Yes (token) |
| `https://webui.xxx.com` | Open WebUI :8085 | Yes |
| `https://pma.xxx.com` | phpMyAdmin :8084 | **Denied by default** |
| `https://mail.xxx.com` | Listmonk :9000 | **Denied by default** |
| `https://grafana.xxx.com` | Grafana :3030 | **Denied by default** |

The three admin surfaces return 403 until you add an `allow <CIDR>;` line
in `nginx/conf.d/40_admin.conf` and reload nginx. **Do not remove the
`deny all` lines** — a public phpMyAdmin is a full database-dump endpoint.

### Bootstrap the platform owner

The **first** account registered on a fresh deployment is automatically
promoted to `owner`. Do this immediately, before anyone else registers:

```bash
curl -X POST https://api.example.com/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"<strong-password>","display_name":"You"}'
```

Then verify the product loop end to end:

```bash
TOKEN=$(curl -s -X POST https://api.example.com/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"<strong-password>"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

# publish something
curl -X POST https://api.example.com/api/content \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"title":"Intro to MLOps","slug":"intro-to-mlops","price_cents":4900}'

# record an anonymous usage event
curl -X POST https://api.example.com/api/usage/events \
  -H 'Content-Type: application/json' \
  -d '{"event_type":"page_view","content_id":1}'

# read the monetisation funnel
curl -s 'https://api.example.com/api/usage/summary?since_hours=24' | python3 -m json.tool
```

---

## 11. Backups

```bash
echo "30 2 * * * root /opt/mlops-webshop-stack/scripts/60_backup.sh" \
  | sudo tee /etc/cron.d/mlops-backup
```

Captures the host Postgres, the Docker Postgres, MariaDB, the object store,
the Docker volumes, the web roots and the app configuration. Every archive
is `gzip -t` verified before the run is declared successful.

> **Test the restore.** A backup that has never been restored from is a
> hypothesis, not a backup. Run the restore into a scratch VM at least
> quarterly:
>
> ```bash
> sudo bash scripts/65_restore.sh /var/backups/mlops-webshop
> sudo bash scripts/65_restore.sh /var/backups/mlops-webshop 20261021-023000
> ```
>
> The restore script verifies archive integrity **before** touching
> anything and refuses to run on a corrupt backup. It overwrites live data
> and asks for confirmation.

Media is not in the Docker volumes. To back up the object store, set
`OBJECT_STORE_BACKUP_CMD` to a command that writes a consistent bucket copy
to stdout.

---

## 12. Updating

```bash
cd /opt/mlops-webshop-stack
sudo git pull
sudo bash scripts/30_install_apps.sh     # re-syncs, rebuilds, re-renders env
cd docker && sudo docker compose up -d --remove-orphans
sudo bash scripts/70_healthcheck.sh      # verify
```

The app script uses `rsync --delete` against `/opt/apps`, so stale files
are removed while `.env` and `.venv` are preserved. Your existing app
`.env` files are backed up before being overwritten.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `python-api` will not start | `JWT_SECRET` / `ANON_SALT` still a placeholder | Set them in `/opt/mlops-webshop-stack/.env`, re-run `30_install_apps.sh` |
| `listmonk` restart-looping | Schema not installed | `docker compose up -d listmonk-init` and check it exits 0 |
| Prometheus target `nextjs_rag` is down | Wrong metrics path | Should be `/api/metrics` — check `monitoring/prometheus/prometheus.yml` |
| Prometheus targets `down` for host services | `host.docker.internal` not mapped | The compose file sets `extra_hosts: host-gateway`; confirm with `docker compose config` |
| Prometheus target `node_exporter` down with "not a shared or slave mount" | The base compose uses `rslave`, which Docker Desktop rejects | On macOS start with the dev override: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d` |
| `403` on grafana / pma / mail | Admin surfaces denied by default | Add an `allow <CIDR>;` line in `nginx/conf.d/40_admin.conf` |
| RAG answers time out | CPU-only inference is slow | Expected: 2–4 min for a 0.5B model. Raise `OLLAMA_TIMEOUT_MS` or add a GPU |
| `502` from `rag.xxx.com` | `nextjs-rag` unit is down | `journalctl -u nextjs-rag -f` |
| Cert renewal fails | `manual` DNS plugin | Switch to a real DNS plugin (see step 4) |
| Backup says "object store NOT backed up" | `OBJECT_STORE_BACKUP_CMD` unset | Set it (see step 11) |
| Backup says "host postgres dump failed" | No local `postgres` user — expected when Postgres runs in Docker | Harmless: the Docker dump is the one that matters. The warning is informational |
| Restore says "no backups found" | The backup set has a filename the discovery regex rejects | Archives must be named `<kind>_<stamp>.<ext>` with a **two-dot** extension (`.tar.gz`, `.sql.gz`) |
| `next start` refuses to run | `output: "standalone"` in `next.config.js` | It is intentionally not set; the unit runs `next start` |
| A secret change "did not take effect" | You edited `/opt/apps/*/.env` directly | Edit `/opt/mlops-webshop-stack/.env` and re-run `30_install_apps.sh` — the per-service files are regenerated |

### Inspecting a service

```bash
systemctl status python-api
journalctl -u python-api -f --tail=100
cd /opt/mlops-webshop-stack/docker && docker compose logs -f open-webui
curl -s http://127.0.0.1:3000/ready | python3 -m json.tool
curl -s http://127.0.0.1:9091/api/v1/targets | python3 -m json.tool | head -40
```

### Re-running validation locally

Before pushing any change:

```bash
bash scripts/validate.sh
```
