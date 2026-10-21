# mlops-webshop-stack

**Version:** v02 · 2026-10-21
**Target:** Ubuntu 22.04 LTS VPS (self-hosted) · macOS M3 Max (local dev) · AWS/GCP
**License:** MIT

A self-hosted, Docker-based MLOps/LLMOps stack for a **co-creation webshop**:
registered users co-author and collaboratively produce multi-modal tutorials,
courses and video; the platform advertises and sells them; and it tracks how
both anonymous visitors and registered members use the material so it can be
monetised.

---

## Table of contents

1. [What this stack is](#1-what-this-stack-is)
2. [Architecture](#2-architecture)
3. [Tech stack](#3-tech-stack)
4. [Repository layout](#4-repository-layout)
5. [Prerequisites](#5-prerequisites)
6. [Local development (macOS M3 Max)](#6-local-development-macos-m3-max)
7. [Production deployment (Ubuntu 22.04 VPS)](#7-production-deployment-ubuntu-2204-vps)
8. [Configuration](#8-configuration)
9. [Operating the stack](#9-operating-the-stack)
10. [Validation & testing](#10-validation--testing)
11. [Monitoring](#11-monitoring)
12. [Backup & restore](#12-backup--restore)
13. [Security model](#13-security-model)
14. [Cloud deployment (AWS / GCP)](#14-cloud-deployment-aws--gcp)
15. [Known limitations](#15-known-limitations)
16. [Roadmap](#16-roadmap)

---

## 1. What this stack is

A production-shaped reference stack for an AI-native content marketplace.

**The product loop it supports:**

```
 Author creates  ──▶  Co-authors contribute  ──▶  Publish
        │                                              │
        ▼                                              ▼
   Multi-modal assets                            Content is listed
   (video/audio/image/doc)                             │
        │                                              ▼
        └──────────────▶  Anonymous + registered usage telemetry ◀─┘
                                                       │
                                                       ▼
                                     Conversion → entitlement → payout
```

**Three capabilities the stack implements end to end:**

| Capability | Where it lives |
|---|---|
| **Co-creation / co-authoring** | `Content`, `Contribution`, `Revision` models; `content.py` router |
| **Multi-modal asset handling** | `Asset` model; `assets.py` router; object-store integration point |
| **Usage telemetry & monetisation** | `UsageEvent` + `AnonymousId` models; `usage.py` router (collect, aggregate). `Entitlement` is modelled but has no route yet |

**Non-goals (deliberately out of scope for this repo):** payment-provider
integration, video transcoding workers, and the frontend application. Those
are integration points — the data model, API contract and telemetry pipeline
are here, ready to be wired up.

---

## 2. Architecture

```text
                          Internet (80/443)
                                 │
                    ┌────────────▼────────────┐
                    │         nginx            │  TLS termination, rate limiting,
                    │   (systemd, 127.0.0.1)   │  security headers, reverse proxy
                    └────────────┬────────────┘
                                 │
   ┌──────────────┬──────────────┼───────────────┬──────────────┬───────────────┐
   │              │              │               │              │               │
┌──▼───┐    ┌────▼────┐   ┌─────▼─────┐   ┌─────▼────┐   ┌─────▼─────┐   ┌────▼────┐
│apache│    │ FastAPI  │   │  Node API  │   │Next.js   │   │ Streamlit │   │  Open   │
│:8081 │    │  :3000   │   │   :8000    │   │  RAG     │   │   :3001   │   │  WebUI  │
│      │    │  (gunicorn)  │(express)  │   │  :8001   │   │           │   │         │
│ WP   │    │  /metrics│   │  /metrics │   │/api/metrics│  │           │   │  :8085  │
└──┬───┘    └────┬─────┘   └─────┬─────┘   └─────┬────┘   └─────┬─────┘   └────┬────┘
   │            │              │               │              │              │
   │      ┌─────▼──────────────▼───────────────▼──────────────┘              │
   │      │                    PostgreSQL :5432                              │
   │      │        (users, content, assets, usage_events)                     │
   │      │                                                                    │
   │      │   ┌─────────────────── Docker (compose) ─────────────────────┐    │
   │      │   │  ollama :11434   postgres (pgvector)   listmonk :9000    │    │
   │      │   │  prometheus :9091   grafana :3030   node-exporter :9100 │    │
   │      │   └──────────────────────────────────────────────────────────┘    │
   │      │                                                                    │
   └──────┴──────────── MariaDB :3306 (WordPress) ─────────────────────────────┘
```

**Traffic model:** every upstream binds to `127.0.0.1`. Only nginx listens on
the public interface, and `ufw` permits just `22`, `80` and `443`.

**Container ↔ host boundary:** host-run services (systemd) reach Dockerised
services over the published loopback ports (`127.0.0.1:11434` for Ollama).
Containerised services reach host-run services via `host.docker.internal`,
mapped with `extra_hosts: ["host.docker.internal:host-gateway"]` — without
this, Prometheus targets are permanently `down` on Linux.

---

## 3. Tech stack

### Runtime & language

| Layer | Technology | Version | Rationale |
|---|---|---|---|
| API framework | FastAPI | 0.115.6 | Async, OpenAPI-native, Pydantic validation |
| ASGI server | Gunicorn + Uvicorn workers | 23.0.0 / 0.34.0 | Production-grade process supervision |
| ORM | SQLAlchemy 2.0 | 2.0.36 | Typed `Mapped[]` models, explicit sessions |
| DB driver | psycopg 3 | 3.2.3 | Native async-capable PostgreSQL driver |
| Validation | Pydantic v2 | 2.10.4 | Strict, fast, excellent error messages |
| Auth | python-jose (JWT HS256) | 3.3.0 | Stateless tokens, no server-side session store |
| Password hashing | stdlib `hashlib.pbkdf2_hmac` | — | 600k iterations, **no native build dep** |
| Secondary API | Express | 4.21.2 | Small, well-understood, fast to start |
| Metrics (Node) | `@prometheus-io/client` | 0.16.1 | Official Prometheus client |
| Frontend | Next.js (pages router) | 14.2.35 | RAG chat UI; **patched** release |
| Dashboard | Streamlit | 1.39.0 | Internal analytics, zero frontend build |
| LLM runtime | Ollama | latest | Local inference, no per-token API cost |
| AI workspace | Open WebUI | `main` | Chat, RAG, model management |

### Data & storage

| Component | Technology | Notes |
|---|---|---|
| Primary database | PostgreSQL 16 | Application + telemetry |
| Vector store | **pgvector** (in the same Postgres) | No extra service, no extra RAM |
| CMS database | MariaDB 10.6 | WordPress |
| Media storage | S3-compatible object store | `Asset.storage_key` points here |
| Email | Listmonk + Postgres | Campaign management |

### Edge, observability & operations

| Component | Technology | Notes |
|---|---|---|
| Reverse proxy | nginx 1.26 | TLS, rate limiting, security headers |
| Metrics | Prometheus 2.x | 15-day retention, recording rules |
| Dashboards | Grafana OSS | Provisioned from `monitoring/grafana/provisioning` |
| Host metrics | node-exporter | `pid: host`, read-only root mount |
| Logs | journald + nginx + Docker json-file | Rotated, `json-file` with `max-size: 10m` |
| Service manager | systemd | Hardened units (`NoNewPrivileges`, `ProtectSystem`) |
| Containers | Docker Engine + Compose v2 | Health-gated `depends_on` |
| Secrets | `.env` files, `chmod 600`, git-ignored | Never in the image or the repo |
| Backups | `pg_dump` + `mysqldump` + volume tar | Restore script included |
| CI | GitHub Actions | Lint, unit **and** integration tests, compose/build validation |

### Why these choices

- **pgvector over a dedicated vector DB.** Qdrant/Milvus would each add a
  container and 200–500 MB of RAM. For a catalogue of tutorials and courses,
  pgvector inside the existing Postgres is the right trade at this scale.
- **PBKDF2 over bcrypt/argon2.** The original scaffold depended on
  `passlib[bcrypt]`, which is unmaintained and requires a native build.
  PBKDF2-SHA256 at 600k iterations is in the standard library — no compiler
  needed on the VPS, and no supply-chain risk from an abandoned package.
- **Gunicorn + Uvicorn workers over bare uvicorn.** `--workers 2` without an
  explicit worker class leaves proxy headers unhandled behind nginx.
- **Ollama over a hosted LLM API.** Self-hosted inference keeps per-request
  cost at zero and content on your own infrastructure — a hard requirement
  for a paid course platform.

---

## 4. Repository layout

```text
mlops-webshop-stack/
├── apps/
│   ├── python-api/            # FastAPI — the platform API
│   │   ├── app/
│   │   │   ├── config.py      # Pydantic settings, fail-fast validation
│   │   │   ├── db.py          # Engine, session factory, init_db()
│   │   │   ├── models.py      # ORM: users, content, assets, usage
│   │   │   ├── schemas.py     # Request/response validation
│   │   │   ├── security.py    # PBKDF2 hashing, JWT, role dependencies
│   │   │   ├── main.py        # App factory, middleware, metrics
│   │   │   └── routers/       # auth, content, assets, usage
│   │   └── tests/             # Unit tests (no DB required)
│   ├── node-api/              # Express API + Prometheus metrics
│   ├── nextjs-rag/            # Next.js RAG chat UI + /api/metrics
│   └── streamlit-app/         # Internal analytics dashboard
├── apache2/                   # Apache vhosts (WordPress, phpMyAdmin)
├── nginx/                     # Front-end proxy: nginx.conf, conf.d, snippets
├── docker/
│   ├── docker-compose.yml     # Production container stack
│   ├── docker-compose.dev.yml # macOS/Docker Desktop override
│   ├── initdb/                # First-boot Postgres init (databases, pgvector)
│   └── .env.example
├── monitoring/
│   ├── prometheus/prometheus.yml
│   ├── prometheus/rules/webshop.yml
│   └── grafana/provisioning/
├── scripts/                   # Provisioning, backup, validation, health check
├── systemd/                   # Hardened service units
├── docs/DEPLOY.md             # Step-by-step deployment
└── .env.example               # Single source of truth for configuration
```

---

## 5. Prerequisites

### VPS (production)

| Item | Requirement |
|---|---|
| OS | Ubuntu 22.04 LTS (x86_64 or arm64) |
| RAM | **8 GB minimum**, 16 GB recommended |
| Disk | 40 GB SSD (media is on object storage, not the VPS) |
| CPU | 2 vCPU minimum; 4 vCPU if running local inference |
| Network | Public IP, inbound 22/80/443 |
| DNS | Apex A record **and** a wildcard `*` A record → the VPS IP |

> **Inference reality check.** Without a GPU, a 0.5B model takes **2–4
> minutes** for a short answer. The RAG service is configured for a 300 s
> upstream timeout for this reason. If that is unacceptable, either add a
> GPU, use a smaller model, or route inference to a hosted API.

### macOS (local development)

| Item | Requirement |
|---|---|
| OS | macOS 14+ on Apple Silicon (M3 Max tested) |
| RAM | 16 GB minimum, 32 GB+ recommended |
| Docker | Docker Desktop with the Compose v2 plugin |
| Tools | `shellcheck`, `yamllint`, `uv`, Node 20+, Python 3.11+ |

---

## 6. Local development (macOS M3 Max)

```bash
# 1. Clone
git clone https://github.com/sradu2003/mlops-webshop-stack.git
cd mlops-webshop-stack

# 2. Configure — the root .env is the single source of truth
cp .env.example .env
cp docker/.env.example docker/.env
python3 - <<'PY'
import pathlib, secrets
p = pathlib.Path(".env")
s = p.read_text()
s = s.replace("JWT_SECRET=change_me_jwt_secret", f"JWT_SECRET={secrets.token_hex(32)}")
s = s.replace("ANON_SALT=change_me_anon_salt", f"ANON_SALT={secrets.token_hex(32)}")
p.write_text(s)
print("secrets generated in .env")
PY
# Still set POSTGRES_PASSWORD, MARIADB_PASSWORD and DOMAIN by hand.

# 3. Validate the whole stack BEFORE running it
bash scripts/validate.sh

# 4. Start the container stack (the dev override is REQUIRED on macOS)
cd docker
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
cd ..

# 5. Pull the model (the first time only — ~400 MB)
docker compose -f docker/docker-compose.yml exec ollama ollama pull qwen2.5:0.5b

# 6. Create the database (the script reads the root .env, and must run as root
#    because it talks to the local postgres/MariaDB system users)
sudo bash scripts/init_databases.sh

# 7. Run the API services locally (they are systemd services in production;
#   in development you start them by hand)
cd apps/python-api
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 3000
# in another shell:
cd apps/node-api    && npm ci && npm start
cd apps/nextjs-rag  && npm ci && npm run dev
cd apps/streamlit-app && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/streamlit run app.py

# 8. Verify
bash scripts/70_healthcheck.sh
```

> The API needs a real `JWT_SECRET` and `ANON_SALT` — the Pydantic
> validator rejects the placeholders, so the process will not start with
> the values from `.env.example`.

### macOS specifics

- **Port 11434 conflict.** A native Ollama usually occupies `11434`. The dev
  override republishes the container on `11435`. Point the host-run RAG
  service at `http://127.0.0.1:11435` in `apps/nextjs-rag/.env`.
- **Docker Desktop filesystem propagation.** `node-exporter` cannot use the
  `rslave` mount mode on macOS; the dev override drops it.
- **Apple Silicon images.** All images used have arm64 builds. `node-exporter`
  needs `pid: host` to see host metrics.

### Running the APIs locally (optional)

```bash
# PostgreSQL for the API
docker run -d --name dev-pg -p 5432:5432 \
  -e POSTGRES_USER=shop -e POSTGRES_PASSWORD=shop -e POSTGRES_DB=shopdb \
  pgvector/pgvector:pg16

cd apps/python-api
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then edit DATABASE_URL / JWT_SECRET
uvicorn app.main:app --reload --port 3000

# Open http://127.0.0.1:3000/api/docs
```

---

## 7. Production deployment (Ubuntu 22.04 VPS)

> Full detail in [`docs/DEPLOY.md`](docs/DEPLOY.md). This is the condensed
> version.

```bash
# 0. Provision: Ubuntu 22.04, 8 GB+ RAM, DNS A + wildcard → this IP
sudo mkdir -p /opt && cd /opt
sudo git clone https://github.com/sradu2003/mlops-webshop-stack.git
cd mlops-webshop-stack
sudo cp .env.example .env && sudo nano .env      # set all secrets
sudo cp docker/.env.example docker/.env && sudo nano docker/.env

# 1. Base system, firewall, fail2ban, swap
sudo bash scripts/00_bootstrap.sh

# 2. TLS (wildcard). The DNS plugin is REQUIRED — `--manual` cannot renew.
#    cloudflare | route53 | duckdns
sudo bash scripts/10_ssl_wildcard.sh example.com admin@example.com cloudflare

# 3. Toolchain
#    Ubuntu 22.04 ships Node 12 and no stable Python 3.11 — both are
#    blockers. These scripts install the correct versions.
sudo bash scripts/15_install_node.sh
sudo bash scripts/15_install_python.sh

# 4. Native services (nginx, apache, MariaDB, PostgreSQL)
sudo bash scripts/20_install_native.sh

# 5. Databases
sudo bash scripts/init_databases.sh

# 6. Application services (systemd)
sudo bash scripts/30_install_apps.sh

# 7. Container stack
sudo bash scripts/40_install_docker_stack.sh

# 8. Harden
sudo bash scripts/50_harden.sh

# 9. Verify — every check must pass
sudo bash scripts/70_healthcheck.sh
```

### Order matters

| # | Script | Why it must come here |
|---|---|---|
| 1 | `00_bootstrap.sh` | ufw must be up before anything binds publicly |
| 2 | `10_ssl_wildcard.sh` | nginx needs `/etc/ssl/shared` to start |
| 3 | `15_install_node.sh` | **before** `30` — Next.js 14 needs Node ≥ 18.17 |
| 4 | `15_install_python.sh` | **before** `30` — jammy has no stable Python 3.11 |
| 5 | `20_install_native.sh` | installs nginx/apache/DBs |
| 6 | `init_databases.sh` | needs the `.env` from step 0 |
| 7 | `30_install_apps.sh` | needs Node + Python + the databases |
| 8 | `40_install_docker_stack.sh` | independent of the systemd services |
| 9 | `50_harden.sh` | **after** everything is installed — it locks down SSH, fail2ban and the service units |
| 10 | `70_healthcheck.sh` | the acceptance gate: exit 0 means the stack is healthy |

> `docs/DEPLOY.md` is the long-form version of this sequence, with the
> DNS-plugin table, the expected output of every script, and a
> troubleshooting matrix for each failure mode.

> **The root `.env` is the single source of truth.**
> `scripts/30_install_apps.sh` renders it into each service's own `.env`
> (`/opt/apps/*/.env`), backing up whatever was there before. You only
> ever edit `/opt/mlops-webshop-stack/.env` — never the per-service files,
> which are overwritten on the next run.

> **The API fails fast on placeholder secrets.** `JWT_SECRET` and
> `ANON_SALT` are validated by Pydantic at import time; a placeholder
> aborts the process rather than letting it serve traffic with a known
> secret. `30_install_apps.sh` refuses to deploy if they are unset and
> generates an `ANON_SALT` if you left the placeholder.

### First-run checklist

```bash
# 1. Create the platform owner account (first registration is auto-promoted)
curl -X POST https://api.example.com/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"<strong-password>","display_name":"You"}'

# 2. Publish something and watch the telemetry pipeline work
TOKEN=$(curl -s -X POST https://api.example.com/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"<strong-password>"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl -X POST https://api.example.com/api/content \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"title":"Intro to MLOps","slug":"intro-to-mlops","price_cents":4900}'

# 3. Record an anonymous usage event
curl -X POST https://api.example.com/api/usage/events \
  -H 'Content-Type: application/json' \
  -d '{"event_type":"page_view","content_id":1}'

# 4. Read the monetisation funnel
curl -s https://api.example.com/api/usage/summary?since_hours=24 | python3 -m json.tool
```

---

## 8. Configuration

### Configuration hierarchy

| File | Consumed by | Committed? |
|---|---|---|
| `.env` | `init_databases.sh` and `30_install_apps.sh` — **the source of truth** | **No** — git-ignored |
| `docker/.env` | `docker/docker-compose.yml` | **No** — git-ignored |
| `/opt/apps/*/.env` | Each systemd unit (`EnvironmentFile=`) — **generated** from the root `.env` | **No** — git-ignored |
| `/opt/jupyter/.env` | The `jupyterlab` unit — generated on first run | **No** |
| `.env.example` | Template for the above | Yes |
| `docker/.env.example` | Template for `docker/.env` | Yes |
| `apps/*/.env.example` | Reference template only — **not** used at deploy time | Yes |

> **Edit the root `.env`, never the per-service `.env`.**
> `30_install_apps.sh` renders the root `.env` into `/opt/apps/*/.env` on
> every run, backing up the previous version to `<file>.bak.<timestamp>`.
> A hand-edit to `/opt/apps/python-api/.env` is lost on the next deploy.

### Secrets you **must** change before going live

| Variable | Where | Generate with |
|---|---|---|
| `JWT_SECRET` | `.env` | `openssl rand -hex 32` |
| `ANON_SALT` | `.env` (auto-generated if left as a placeholder) | `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | `.env` | `openssl rand -base64 24` |
| `MARIADB_PASSWORD` | `.env` | `openssl rand -base64 24` |
| `GRAFANA_ADMIN_PASSWORD` | `docker/.env` | `openssl rand -base64 24` |
| `LISTMONK_DB_PASSWORD` | `docker/.env` | `openssl rand -base64 24` |
| `LISTMONK_ADMIN_PASSWORD` | `docker/.env` | `openssl rand -base64 24` |
| `JUPYTER_TOKEN` | `/opt/jupyter/.env` | auto-generated by `30_install_apps.sh` |

The API **refuses to start** if `JWT_SECRET` or `ANON_SALT` is missing or
still set to a placeholder — this is enforced by a Pydantic validator, not
by convention.

### Key tunables

| Variable | Default | Notes |
|---|---|---|
| `OLLAMA_MEMORY_LIMIT` | `1800m` | Container RAM cap. Raise to `4g` with more models. |
| `OLLAMA_KEEP_ALIVE` | `5m` | How long a model stays resident. `0` = unload immediately. |
| `OLLAMA_MODEL` | `qwen2.5:0.5b` | Swap for a larger model if RAM allows. |
| `OLLAMA_TIMEOUT_MS` | `300000` | Raise for CPU-only inference. |
| `PROMETHEUS_RETENTION` | `15d` | Metrics history. |
| `BACKUP_DIR` | `/var/backups/mlops-webshop` | Backup destination. |
| `RETAIN_DAYS` | `14` | Backup retention. |
| `ALLOWED_HOSTS` | *(empty)* | Host-header validation. Disabled by default — see below. |

> **`ALLOWED_HOSTS` is off by default on purpose.** It enables Starlette's
> `TrustedHostMiddleware`, which returns **400** for any request whose
> `Host` header is not on the list. Prometheus scrapes the API over the
> Docker host gateway, so the request arrives with the container's
> hostname — enabling this without adding that hostname breaks the
> scrape, the `/ready` probe and the health check.
>
> If you enable it, add every name the API is reached by, including the
> host-gateway name Prometheus uses:
>
> ```
> ALLOWED_HOSTS=api.xxx.com,127.0.0.1,localhost,host.docker.internal
> ```

---

## 9. Operating the stack

### Service control

```bash
# systemd (host services)
systemctl status python-api node-api nextjs-rag streamlit-app jupyterlab
systemctl restart python-api
journalctl -u python-api -f --tail=100

# docker (container services)
cd docker
docker compose ps
docker compose logs -f open-webui
docker compose restart ollama
docker compose up -d --remove-orphans
```

### Updating a service

```bash
cd /opt/mlops-webshop-stack
git pull
sudo bash scripts/30_install_apps.sh     # re-syncs + rebuilds
sudo bash scripts/70_healthcheck.sh      # verify
```

The app scripts use `rsync --delete` against `/opt/apps`, so a redeploy
removes stale files while preserving `.env` and `.venv`.

### Scaling a service

```bash
# More API workers (edit the unit, then reload)
sudo systemctl edit python-api
#   [Service]
#   Environment=WEB_CONCURRENCY=4
sudo systemctl daemon-reload && sudo systemctl restart python-api
```

---

## 10. Validation & testing

### Local validation (pre-push)

```bash
bash scripts/validate.sh
```

Runs, in order:

| Check | Tool | What it proves |
|---|---|---|
| Shell syntax | `bash -n` | All 19 scripts parse |
| Lint | `shellcheck -S warning` | No shell defects |
| YAML | `yamllint` | All manifests are well-formed |
| Compose | `docker compose config` | Base **and** dev override resolve |
| Prometheus | `promtool check config` | Config + 10 recording rules are valid |
| nginx | `nginx -t` in a container | The proxy config actually loads |
| Python lint | `pyflakes` | No unused imports or dead references |
| Python unit | `pytest` | 40 unit tests pass (no database) |
| Python integration | `pytest -m integration` | 45 API tests pass against a throwaway Postgres |
| Node | `npm ci` + `next build` | Lockfiles valid, app builds |
| Secrets | grep + `git ls-files` | No credentials committed |

The integration step starts a disposable `pgvector/pgvector:pg16`
container on a random high port (never 5432, so a developer's own
database is untouched) and removes it afterwards. It is skipped, not
failed, when Docker is unavailable.

### Health check (post-deploy)

```bash
sudo bash scripts/70_healthcheck.sh
```

Validates 9 systemd units, 7 TCP ports, 13 HTTP endpoints, 7 container
states, the `listmonk-init` exit code, Prometheus target health, the TLS
certificate chain and expiry, the Jupyter auth token, public-interface
exposure, ufw, and backup freshness.
Exit code `0` = healthy.

### Test suite

Two suites, separated by marker so the default run never needs a database.

```bash
cd apps/python-api
source .venv/bin/activate
export JWT_SECRET=$(openssl rand -hex 32)
export ANON_SALT=$(openssl rand -hex 32)

pytest -q tests/                # 40 unit tests — no database needed
pytest -q tests/ -m integration # 45 integration tests — needs Postgres
```

The default marker filter is `not integration`, so `pytest -q tests/` is
safe on a machine with no Postgres running. The integration suite is
opt-in.

### Integration tests (need Postgres)

`tests/integration/` exercises the real HTTP surface against a real
database. It covers authentication (including the no-user-enumeration
guarantee and the first-account-becomes-`owner` bootstrap),
authorisation (role gates, ownership, co-authoring), input validation,
the content/asset lifecycle, telemetry (anonymous + registered attribution,
the protected raw-event stream) and the operational endpoints.

```bash
docker run -d --name test-pg -p 5432:5432 \
  -e POSTGRES_USER=shop -e POSTGRES_PASSWORD=shop -e POSTGRES_DB=shopdb \
  pgvector/pgvector:pg16

cd apps/python-api
export DATABASE_URL="postgresql+psycopg://shop:shop@127.0.0.1:5432/shopdb"
export JWT_SECRET=$(openssl rand -hex 32)
export ANON_SALT=$(openssl rand -hex 32)
pytest -q tests/ -m integration
```

> The suite **drops and recreates the schema for every test**, so point
> `DATABASE_URL` at a throwaway database. Never at production.

### CI

`.github/workflows/validate.yml` runs the full validation, the Python unit
**and** integration suites (against a `pgvector/pgvector:pg16` service
container), the Node install/build and a Next.js production build on every
push and pull request. The integration suite is a hard gate — a
regression in auth or authorisation fails the build.

---

## 11. Monitoring

### What is collected

Each service exports a **different metric family**, because each uses a
different client library. The recording rules normalise them.

| Source | Scrape path | Metrics |
|---|---|---|
| FastAPI | `/metrics` | `http_requests_total`, `http_request_duration_seconds` (histogram), `http_requests_inprogress` (gauge) — via `prometheus-fastapi-instrumentator` |
| Node API | `/metrics` | `node_api_http_requests_total`, `node_api_http_request_duration_seconds`, `node_api_db_query_duration_seconds`, process metrics |
| Next.js RAG | `/api/metrics` | `rag_http_requests_total`, `rag_http_request_duration_seconds`, `rag_ollama_request_duration_seconds`, `rag_ollama_requests_total` |
| Streamlit | `/_stcore/metrics` | Streamlit's own metrics |
| node-exporter | `/metrics` | Host CPU, memory, disk, filesystem, network |
| Prometheus | — | 15-day retention, 10 recording rules for dashboard queries |

> The RAG service is scraped at **`/api/metrics`**, not `/metrics` — it is
> a Next.js API route, and scraping `/metrics` returns 404 and leaves the
> target permanently `down`.

### Access

| Surface | URL | Access |
|---|---|---|
| Grafana | `https://grafana.example.com` | IP-allowlisted + Grafana login |
| Prometheus | `http://127.0.0.1:9091` | Loopback only — never exposed |
| Metrics (API) | `http://127.0.0.1:3000/metrics` | Loopback only |
| Metrics (RAG) | `http://127.0.0.1:8001/api/metrics` | Loopback only |

`/metrics` is explicitly `deny all`-ed in every public nginx vhost, so
metrics are not scraped from the internet.

### Recording rules

`monitoring/prometheus/rules/webshop.yml` pre-computes the dashboard inputs.
Each rule unions the relevant metric family per service with `or`, so a
single dashboard query works across all three API services:

- `service:http_requests:rate5m` — request rate per service
- `service:http_error_ratio:rate5m` — 5xx ratio
- `service:http_request_duration_seconds:p95` — p95 latency
- `service:http_requests_in_flight` — in-flight requests
- `llm:upstream_request_duration_seconds:p95` — RAG → Ollama latency
- `llm:upstream_requests:rate5m` — LLM call rate, split by outcome
- `llm:upstream_error_ratio:rate5m` — LLM error rate
- `host:memory_used_bytes`, `host:memory_utilisation:ratio` — host memory
- `host:disk_used_ratio:root` — disk usage

---

## 12. Backup & restore

### Schedule

```bash
echo "30 2 * * * root /opt/mlops-webshop-stack/scripts/60_backup.sh" \
  | sudo tee /etc/cron.d/mlops-backup
```

### What is captured

| Artefact | Method | File |
|---|---|---|
| Host PostgreSQL | `pg_dumpall` | `pg_<stamp>.sql.gz` |
| Docker PostgreSQL | `pg_dumpall` in-container | `pg_docker_<stamp>.sql.gz` |
| MariaDB / WordPress | `mysqldump --all-databases` | `mysql_<stamp>.sql.gz` |
| Object store | `OBJECT_STORE_BACKUP_CMD` | `objects_<stamp>.tar.gz` |
| Docker volumes | `tar` via a container | `vol_<vol>_<stamp>.tar.gz` |
| Web roots | `tar` | `www_<stamp>.tar.gz` |
| App configuration | `tar` | `config_<stamp>.tar.gz` |

Every archive is `gzip -t` verified before the run is declared successful.

The **host** PostgreSQL and MariaDB dumps are best-effort: on a host where
those databases do not run natively (macOS, or the databases live in
Docker) the dump fails, and that must not abort the backup of everything
else. A failed host dump is logged as a warning and its (empty) file is
removed, so a partial archive is never left behind to be restored later.
The **Docker** PostgreSQL dump — the one that actually holds the
application and telemetry data — is mandatory and aborts the run on
failure.

### Restore

```bash
# Interactive — picks the newest backup set
sudo bash scripts/65_restore.sh /var/backups/mlops-webshop

# Or a specific set
sudo bash scripts/65_restore.sh /var/backups/mlops-webshop 20261021-023000
```

The restore script **verifies archive integrity before touching anything**
and refuses to run on a corrupt backup.

> **Test the restore.** A backup that has never been restored from is a
> hypothesis, not a backup. Run `65_restore.sh` into a scratch VM quarterly.

### Object store

Media is not in the Docker volumes. Set `OBJECT_STORE_BACKUP_CMD` to a
command that writes a consistent bucket copy to stdout, e.g.:

```bash
export OBJECT_STORE_BACKUP_CMD="mc mirror --overwrite myminio/bucket /tmp/obj && tar -czf - -C /tmp obj && rm -rf /tmp/obj"
```

---

## 13. Security model

### Controls in place

| Layer | Control |
|---|---|
| **Transport** | TLS 1.2/1.3, HSTS (2y + `includeSubDomains`), OCSP stapling, `http2 on` |
| **Headers** | `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy` |
| **Edge** | Per-zone rate limits (auth `5r/m`, API `20r/s`, usage `60r/s`), connection limits |
| **AuthN** | JWT HS256, PBKDF2-SHA256 @600k, constant-time verification, user-enumeration-resistant login |
| **AuthZ** | Role gates (`user`/`author`/`curator`/`admin`/`owner`) + ownership checks on every mutation; unpublished content is invisible to the public |
| **Telemetry** | Raw usage events require `curator`/`admin`/`owner`; the aggregate summary is open (it exposes no identities) |
| **Input** | Pydantic validation: prices ≥ 0, slug pattern, password strength, bounded ratios |
| **Isolation** | All upstreams on `127.0.0.1`; `ufw` allows only 22/80/443 |
| **Host** | SSH key-only, fail2ban + `nginx-limit-req` jail, sysctl hardening, unattended upgrades |
| **Containers** | Non-root where possible, `mem_limit` on every service, health-gated startup, log rotation |
| **Secrets** | `.env` git-ignored, `chmod 600`, never in images or the repo |
| **Blast radius** | `MemoryMax` + `CPUQuota` + `TasksMax` on every systemd unit |

### Public vs. authenticated endpoints

| Endpoint | Auth | Why |
|---|---|---|
| `POST /api/usage/events` | None (by design) | Anonymous visitors must be able to record events |
| `GET /api/usage/summary` | None | Aggregates only — no identities, no per-user data |
| `GET /api/usage/events` | `curator` / `admin` / `owner` | The raw stream carries `user_id`: it exposes who watched what |
| `GET /api/content` | None | Public catalogue; only published items are listed |
| `GET /api/content/{id}`, `/slug/{slug}` | None | Published only — a draft returns 404 to the public |
| `POST /api/assets` | Authenticated + must be able to edit the target content | Prevents a stranger attaching media to another author's work |
| `POST` / `PATCH` / `DELETE /api/content/**` | `user`+ with ownership or role | Co-authoring mutations |

The content-authorisation rules live in one place,
`apps/python-api/app/permissions.py`, so a change to "who may edit a
content item" cannot drift between the content and asset routers.

### Access control on admin surfaces

phpMyAdmin, Listmonk and Grafana are `deny all` by default. To enable one,
uncomment the `allow <CIDR>;` lines in `nginx/conf.d/40_admin.conf` and
reload nginx. **Do not remove them** — a public phpMyAdmin is a full
database dump endpoint.

### JupyterLab

JupyterLab is a remote-code-execution surface. It now requires a token
(generated automatically into `/opt/jupyter/.env`) and is recommended to sit
behind basic auth as well. The health check fails if no token is set.

### Secret rotation

Rotate in the **root `.env`** — the per-service files are regenerated from it
by `30_install_apps.sh` and any edit made directly to them is lost on the
next deploy.

```bash
# Rotate the JWT secret — invalidates all sessions
NEW=$(openssl rand -hex 32)
sudo sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$NEW|" /opt/mlops-webshop-stack/.env

# Rotate the anonymous-identifier salt — de-links historical anonymous data
NEWSALT=$(openssl rand -hex 32)
sudo sed -i "s|^ANON_SALT=.*|ANON_SALT=$NEWSALT|" /opt/mlops-webshop-stack/.env

# Re-render the per-service .env files and restart
sudo bash scripts/30_install_apps.sh
sudo systemctl restart python-api
```

> Rotating `ANON_SALT` de-links historical anonymous visitors from their
> previous identifiers. Rotate `JWT_SECRET` **last** — it invalidates every
> active session, so users must sign in again.

---

## 14. Cloud deployment (AWS / GCP)

### AWS (EC2 + S3)

```bash
# Launch an Ubuntu 22.04 instance, then:
sudo apt-get update && sudo apt-get install -y cloud-init
sudo hostnamectl set-hostname webshop
# attach an IAM role (no static keys) for S3 access

# S3 for media
aws s3 mb s3://my-webshop-media
# give the API role: s3:PutObject, s3:GetObject, s3:DeleteObject on that bucket
```

Set `DATABASE_URL` to an **RDS** endpoint. The app currently creates its
schema with SQLAlchemy's `create_all()` on startup, so pointing it at RDS
works out of the box — but it is not a migration story. Adopt Alembic before
the first schema change (see [Known limitations](#15-known-limitations)):

```bash
# RDS
sudo apt-get install -y postgresql-client
export DATABASE_URL="postgresql+psycopg://$DB_USER:$DB_PASS@$RDS_HOST:5432/$DB_NAME"
# The app creates the tables on first start; verify:
curl -s http://127.0.0.1:3000/ready | python3 -m json.tool
```

### GCP (Compute Engine + Cloud Storage)

```bash
gcloud compute instances create webshop \
  --image-family=ubuntu-2204-lts --image-project=ubuntu-os-cloud \
  --machine-type=e2-standard-4 --boot-disk-size=60GB \
  --scopes=https://www.googleapis.com/auth/devstorage.read_write
```

Use Cloud SQL for PostgreSQL and attach a **service account** (not a JSON
key) for GCS access.

### Managed Postgres vs. VPS Postgres

| | VPS Postgres | RDS / Cloud SQL |
|---|---|---|
| Cost | Included in the VPS | +$15–50/month |
| Uptime | Your responsibility | SLA-backed |
| Backups | `60_backup.sh` | Automated + PITR |
| CPU spikes | Compete with the API | Isolated |
| **Recommendation** | Fine for <10k DAU | **Use for production** |

### Container images

The apps currently run as systemd services. To move to containers, build
from `apps/*/` with a multi-stage Dockerfile pinned to the same base images
as `systemd/`, then add the resulting services to
`docker/docker-compose.yml` (or a new `docker/docker-compose.services.yml`
that the main file includes with `include:`).

> **Note on `next.config.js`.** The Next.js service currently uses the
> default build output because the systemd unit runs `next start`. A
> container image should switch to `output: "standalone"` and start
> `node .next/standalone/server.js` — but the two are mutually exclusive.

---

## 15. Known limitations

Honest list of what is **not** production-ready yet:

| Limitation | Impact | Mitigation |
|---|---|---|
| **WordPress is not installed** | `xxx.com` serves an empty docroot — the Apache vhosts, MariaDB database and web roots exist, but no WordPress core is downloaded | Run `wp core download` in each docroot, or replace the Apache tier with the platform API; see the note below |
| **No payment integration** | Entitlements cannot be granted automatically | The `Entitlement` model and the `CheckoutIn` / `CheckoutOut` schemas are ready, but there is **no checkout route** — add one that creates a PSP session and grants the entitlement from its webhook |
| **No transcoding workers** | Uploaded video is not transcoded to adaptive bitrate | `Asset.status` tracks the state machine; add a worker that consumes `status='uploading'` |
| **No object-store client** | `Asset.storage_key` is a string only | Set `OBJECT_STORE_BACKUP_CMD`; the client is the missing piece |
| **`create_all`, no migrations** | Schema changes need a manual step | Adopt Alembic before the first schema change |
| **Ollama has no Prometheus endpoint** | LLM latency is only visible via the API's own histogram | The RAG service exports `rag_ollama_request_duration_seconds` |
| **CPU-only inference is slow** | 2–4 min per RAG answer | 300 s timeout set; add a GPU or a hosted API |
| **Single-node** | No HA, no horizontal scale | The compose file is stateless except Postgres; add a load balancer and move the DB |
| **TLS is manual-DNS** | Renewal needs the DNS API token | Use a DNS plugin (cloudflare/route53/duckdns) — the script supports it |
| **Open WebUI on `main` tag** | An upstream release can break the stack | Pin a digest for production |

### The WordPress gap

`apache2/`, the MariaDB database and the `/var/www/html/*` web roots are
provisioned, and `xxx.com` routes correctly through nginx → Apache. But
**no WordPress core is ever downloaded**: `20_install_native.sh` installs
the web server and PHP, not the CMS. The apex domain therefore serves an
empty page.

That is a deliberate boundary rather than an oversight — the platform's own
content model (`Content`, `Asset`, `Revision`) is the product, and
WordPress is a parallel CMS that duplicates it. Two ways to close it:

```bash
# Option A — install WordPress into the provisioned docroots
sudo -u www-data wp core download --path=/var/www/html/xxx.com/public
sudo -u www-data wp core install --path=/var/www/html/xxx.com/public \
  --url=https://xxx.com --title="Example" --admin_user=admin --admin_password='<pw>' \
  --admin_email=admin@example.com

# Option B — drop the Apache tier and serve the platform API directly
# (remove nginx/conf.d/10_main.conf and point xxx.com at api_upstream)
```

Until one of these is done, treat `xxx.com` as infrastructure that is
ready but not yet populated.

---

## 16. Roadmap

### Near term (blocks production launch)

- [ ] Alembic migrations + `alembic upgrade head` in the deploy path
- [ ] Payment provider integration (Stripe) → `Entitlement` on webhook, via a new `POST /api/checkout` route
- [ ] Object-store client (MinIO locally, S3/GCS in production) + presigned uploads
- [ ] Transcoding worker (FFmpeg) consuming `Asset.status`
- [ ] Frontend application consuming the API
- [ ] Pin all Docker images to digests

### Medium term

- [ ] Email verification + password reset flow
- [ ] Rate-limit the anonymous event endpoint per pseudonymous ID, not just IP
- [ ] Cohort dashboard: registered vs. anonymous conversion funnel
- [ ] Automated backup verification (restore into a scratch container nightly)
- [ ] `pgvector` embedding pipeline for semantic search over course material

### Long term

- [ ] Multi-region object storage + CDN for video
- [ ] GPU inference node with a routing layer (local vs. hosted fallback)
- [ ] Anomaly detection on usage events (fraudulent streaming detection)
- [ ] Horizontal scaling with a managed Postgres and a load balancer

---

## Contributing

```bash
bash scripts/validate.sh    # must pass before opening a PR
```

---

## License

MIT — see [`LICENSE`](LICENSE).
