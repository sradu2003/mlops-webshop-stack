#!/usr/bin/env bash
# Backup the full stack: PostgreSQL, MariaDB, the object store, the Docker
# volumes, and the web roots.
#
# Every artefact is written as a timestamped, gzip-compressed file under
# $BACKUP_DIR and verified before the run is declared successful. A backup
# that has not been restored from is not a backup — always test the restore
# procedure (scripts/65_restore.sh) at least once per quarter.
#
# Install with:
#   echo "30 2 * * * root /opt/mlops-webshop-stack/scripts/60_backup.sh" \
#     | sudo tee /etc/cron.d/mlops-backup
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/mlops-webshop}"
RETAIN_DAYS="${RETAIN_DAYS:-14}"
REPO_DIR="${REPO_DIR:-/opt/mlops-webshop-stack}"
STAMP="$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

log() { echo "[backup $(date +%T)] $*"; }

# --- Resolve the real Docker volume names -----------------------------------
# Compose prefixes every volume with the project name, e.g. the volume
# declared as `open-webui:` is actually `mlops-webshop_open-webui`.
#
# The previous version looked for the BARE names ("open-webui", "grafana").
# On this host that silently matched an unrelated pre-existing container's
# volume, so the backup captured the wrong data while reporting success.
#
# `docker compose config --volumes` returns the DECLARED names; the actual
# name is `<project>_<declared>`. Both are resolved here from the compose
# project itself so the backup always targets this stack's volumes.
COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-mlops-webshop}"
volume_name() {
  local declared="$1" actual
  actual="$(docker volume ls --format '{{.Name}}' \
    | grep -E "(^|_)${declared}$" \
    | grep -E "^${COMPOSE_PROJECT}_" \
    | head -1 || true)"
  if [[ -z "$actual" ]]; then
    # Fall back to the bare name (a manually created volume).
    actual="$declared"
  fi
  printf '%s' "$actual"
}

# --- 1. PostgreSQL (host instance) -----------------------------------------
log "dumping postgresql"
if command -v pg_dumpall >/dev/null; then
  # The host dump is best-effort: on a machine where the `postgres` system
  # user does not exist (macOS, or a host where Postgres runs in Docker)
  # `sudo -u postgres` fails. That must NOT abort the whole backup — the
  # Docker Postgres dump below covers the databases that actually matter.
  if sudo -u postgres pg_dumpall 2>/dev/null | gzip > "$BACKUP_DIR/pg_${STAMP}.sql.gz"; then
    if gzip -t "$BACKUP_DIR/pg_${STAMP}.sql.gz" 2>/dev/null; then
      log "host postgres dumped"
    else
      log "WARNING: host pg dump is corrupt — removing it"
      rm -f "$BACKUP_DIR/pg_${STAMP}.sql.gz"
    fi
  else
    log "WARNING: host postgres dump failed (no local 'postgres' user?) — skipping"
    rm -f "$BACKUP_DIR/pg_${STAMP}.sql.gz"
  fi
else
  log "pg_dumpall not found — skipping host postgres"
fi

# --- 2. PostgreSQL (docker: listmonk + openwebui) ---------------------------
# The listmonk and Open WebUI databases live in the docker volume, not in the
# host instance. The original backup script missed them entirely.
if docker ps --format '{{.Names}}' | grep -qx 'listmonk-db'; then
  log "dumping docker postgres (listmonk-db)"
  docker exec listmonk-db pg_dumpall -U "${LISTMONK_DB_USER:-shop}" \
    | gzip > "$BACKUP_DIR/pg_docker_${STAMP}.sql.gz"
  gzip -t "$BACKUP_DIR/pg_docker_${STAMP}.sql.gz" || { log "FAIL: docker pg dump corrupt"; exit 1; }
fi

# --- 3. MariaDB / WordPress -------------------------------------------------
log "dumping mariadb"
if command -v mysqldump >/dev/null; then
  # Best-effort for the same reason as the host Postgres dump: a host
  # without a running local MariaDB (macOS, or MariaDB in Docker) must
  # not abort the backup of everything else.
  if mysqldump --all-databases --single-transaction --quick 2>/dev/null \
       | gzip > "$BACKUP_DIR/mysql_${STAMP}.sql.gz"; then
    if gzip -t "$BACKUP_DIR/mysql_${STAMP}.sql.gz" 2>/dev/null; then
      log "mariadb dumped"
    else
      log "WARNING: mysql dump is corrupt — removing it"
      rm -f "$BACKUP_DIR/mysql_${STAMP}.sql.gz"
    fi
  else
    log "WARNING: mariadb dump failed (no local mysqld?) — skipping"
    rm -f "$BACKUP_DIR/mysql_${STAMP}.sql.gz"
  fi
else
  log "mysqldump not found — skipping"
fi

# --- 4. Object store (uploads/media) ---------------------------------------
# MinIO/S3 data is NOT inside the Docker volumes, so it is backed up
# separately. Set OBJECT_STORE_BACKUP_CMD to the command that writes a
# consistent copy of the bucket to stdout.
if [[ -n "${OBJECT_STORE_BACKUP_CMD:-}" ]]; then
  log "dumping object store"
  # NOTE: the archive is a .tar.gz. The previous check first tried
  # "objects_<stamp>.sql.gz" — a path that never exists — so the check
  # always fell through to the second branch and the first `gzip -t`
  # failure was silently discarded.
  if eval "$OBJECT_STORE_BACKUP_CMD" | gzip > "$BACKUP_DIR/objects_${STAMP}.tar.gz"; then
    gzip -t "$BACKUP_DIR/objects_${STAMP}.tar.gz" \
      || { log "FAIL: object store dump corrupt"; exit 1; }
  else
    log "FAIL: object store backup command failed"
    exit 1
  fi
else
  log "OBJECT_STORE_BACKUP_CMD not set — object store NOT backed up"
fi

# --- 5. Docker volumes ------------------------------------------------------
# open-webui (chats, config), ollama (models), grafana (dashboards),
# prometheus (metrics history), listmonk-db (raw cluster files).
#
# The ARCHIVE FILENAME uses the declared volume name (open-webui, grafana) so
# that 65_restore.sh can find it, while the -v mount uses the RESOLVED
# (project-prefixed) name so the right data is actually captured.
if command -v docker >/dev/null; then
  for declared in open-webui grafana; do
    vol="$(volume_name "$declared")"
    if docker volume inspect "$vol" >/dev/null 2>&1; then
      log "archiving volume $declared (resolved: $vol)"
      docker run --rm \
        -v "${vol}:/data:ro" \
        -v "$BACKUP_DIR:/backup" \
        alpine:3.20 \
        tar -czf "/backup/vol_${declared}_${STAMP}.tar.gz" -C /data . \
        || log "WARNING: could not archive volume $vol"
    else
      log "WARNING: volume $vol not found — skipping"
    fi
  done
fi

# --- 6. Application config & web roots --------------------------------------
log "archiving web roots"
tar -czf "$BACKUP_DIR/www_${STAMP}.tar.gz" /var/www/html 2>/dev/null \
  || log "WARNING: could not archive web roots"

log "archiving app configuration"
tar -czf "$BACKUP_DIR/config_${STAMP}.tar.gz" \
  -C / \
  etc/nginx \
  etc/apache2 \
  opt/apps/python-api/.env \
  opt/apps/node-api/.env \
  opt/apps/nextjs-rag/.env \
  opt/apps/streamlit-app/.env \
  2>/dev/null || log "WARNING: could not archive configuration"

# --- 7. Prune ---------------------------------------------------------------
find "$BACKUP_DIR" -type f -mtime "+${RETAIN_DAYS}" -delete
find "$BACKUP_DIR" -type f -name '*.gz' -mtime "+${RETAIN_DAYS}" -delete 2>/dev/null || true

# --- 8. Report --------------------------------------------------------------
log "backup complete:"
du -sh "$BACKUP_DIR"
ls -lh "$BACKUP_DIR" | tail -20
