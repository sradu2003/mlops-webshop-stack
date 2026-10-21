#!/usr/bin/env bash
# Restore the stack from a backup taken by scripts/60_backup.sh.
#
# Usage:
#   bash scripts/65_restore.sh /var/backups/mlops-webshop [TIMESTAMP]
#
# With no TIMESTAMP the most recent backup in the directory is used.
#
# WARNING: this overwrites the live database and web roots. Take a fresh
# backup first, and run it on a stopped stack (or accept the downtime).
set -euo pipefail

# REPO_DIR MUST be defined here. The original version referenced $REPO_DIR
# without ever setting it, so under `set -u` the script aborted with
# "REPO_DIR: unbound variable" on its very first use — meaning a backup
# could be taken but never restored.
REPO_DIR="${REPO_DIR:-/opt/mlops-webshop-stack}"

BACKUP_DIR="${1:?usage: 65_restore.sh <backup-dir> [timestamp]}"
BACKUP_DIR="${BACKUP_DIR%/}"
STAMP="${2:-}"

log() { echo "[restore $(date +%T)] $*"; }

if [[ ! -d "$BACKUP_DIR" ]]; then
  echo "backup directory not found: $BACKUP_DIR" >&2
  exit 1
fi

# --- Pick the backup set ----------------------------------------------------
if [[ -z "$STAMP" ]]; then
  # The timestamp is the LAST underscore-separated field of every filename
  # 60_backup.sh writes:
  #
  #   pg_<stamp>.sql.gz            mysql_<stamp>.sql.gz
  #   pg_docker_<stamp>.sql.gz     objects_<stamp>.tar.gz
  #   www_<stamp>.tar.gz           config_<stamp>.tar.gz
  #   vol_<volume>_<stamp>.tar.gz
  #
  # The previous regex only matched the first four, so a backup set whose
  # only artefacts were `pg_docker_` or `vol_<name>_` was invisible and the
  # script reported "no backups found" even though the files were there.
  #
  # The extension part MUST allow dots: the real filenames end in
  # `.tar.gz` and `.sql.gz`, i.e. TWO dots. A pattern like
  # `\.[A-Za-z0-9]+$` cannot match `.tar.gz` and silently rejects every
  # archive — which is exactly what happened.
  #
  # A glob + regex is used instead of `ls | grep` so that filenames with
  # spaces or unusual characters are handled safely.
  shopt -s nullglob
  for f in "$BACKUP_DIR"/*; do
    [[ -e "$f" ]] || continue
    base="$(basename "$f")"
    if [[ "$base" =~ _([0-9]{8}-[0-9]{6})\.[A-Za-z0-9.]+$ ]]; then
      candidate="${BASH_REMATCH[1]}"
      if [[ -z "$STAMP" || "$candidate" > "$STAMP" ]]; then
        STAMP="$candidate"
      fi
    fi
  done
  shopt -u nullglob
  if [[ -z "$STAMP" ]]; then
    echo "no backups found in $BACKUP_DIR" >&2
    exit 1
  fi
  log "using latest backup set: $STAMP"
fi

# --- Preflight: verify every archive before touching anything ---------------
for f in "$BACKUP_DIR"/*"_${STAMP}".sql.gz; do
  [[ -e "$f" ]] || continue
  gzip -t "$f" || { log "FAIL: $f is corrupt — refusing to restore"; exit 1; }
done
for f in "$BACKUP_DIR"/*"_${STAMP}".tar.gz; do
  [[ -e "$f" ]] || continue
  gzip -t "$f" || { log "FAIL: $f is corrupt — refusing to restore"; exit 1; }
done
log "all archives verified"

read -r -p "This will OVERWRITE the live data. Continue? [y/N] " ans
[[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted"; exit 1; }

# --- Stop the stack ---------------------------------------------------------
log "stopping services"
systemctl stop python-api streamlit-app node-api nextjs-rag jupyterlab 2>/dev/null || true
cd "$REPO_DIR/docker" 2>/dev/null && docker compose stop 2>/dev/null || true

# --- PostgreSQL (host) ------------------------------------------------------
if [[ -f "$BACKUP_DIR/pg_${STAMP}.sql.gz" ]]; then
  log "restoring host postgresql"
  gunzip -c "$BACKUP_DIR/pg_${STAMP}.sql.gz" | sudo -u postgres psql
fi

# --- PostgreSQL (docker) ----------------------------------------------------
if [[ -f "$BACKUP_DIR/pg_docker_${STAMP}.sql.gz" ]]; then
  log "restoring docker postgresql"
  docker compose -f "$REPO_DIR/docker/docker-compose.yml" up -d listmonk-db
  sleep 15
  gunzip -c "$BACKUP_DIR/pg_docker_${STAMP}.sql.gz" \
    | docker exec -i listmonk-db psql -U "${LISTMONK_DB_USER:-shop}" -d postgres
fi

# --- MariaDB ----------------------------------------------------------------
if [[ -f "$BACKUP_DIR/mysql_${STAMP}.sql.gz" ]]; then
  log "restoring mariadb"
  gunzip -c "$BACKUP_DIR/mysql_${STAMP}.sql.gz" | mysql
fi

# --- Volumes ----------------------------------------------------------------
# The archive is named after the DECLARED volume (vol_open-webui_<stamp>.tar.gz)
# but must be restored into the RESOLVED, project-prefixed volume that Compose
# actually uses. Resolving it the same way 60_backup.sh does keeps the two
# scripts in agreement.
COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-mlops-webshop}"
resolve_volume() {
  local declared="$1" actual
  actual="$(docker volume ls --format '{{.Name}}' \
    | grep -E "(^|_)${declared}$" \
    | grep -E "^${COMPOSE_PROJECT}_" \
    | head -1 || true)"
  printf '%s' "${actual:-$declared}"
}

for declared in open-webui grafana; do
  archive="$BACKUP_DIR/vol_${declared}_${STAMP}.tar.gz"
  [[ -f "$archive" ]] || continue
  vol="$(resolve_volume "$declared")"
  log "restoring volume $declared (resolved: $vol)"
  docker volume create "$vol" >/dev/null 2>&1 || true
  docker run --rm \
    -v "${vol}:/data" \
    -v "$BACKUP_DIR:/backup:ro" \
    alpine:3.20 \
    sh -c "rm -rf /data/* && tar -xzf /backup/$(basename "$archive") -C /data" \
    || log "WARNING: could not restore volume $vol"
done

# --- Web roots & config -----------------------------------------------------
if [[ -f "$BACKUP_DIR/www_${STAMP}.tar.gz" ]]; then
  log "restoring web roots"
  tar -xzf "$BACKUP_DIR/www_${STAMP}.tar.gz" -C /
  chown -R www-data:www-data /var/www/html
fi

if [[ -f "$BACKUP_DIR/config_${STAMP}.tar.gz" ]]; then
  log "restoring configuration"
  tar -xzf "$BACKUP_DIR/config_${STAMP}.tar.gz" -C /
fi

# --- Restart ----------------------------------------------------------------
log "starting services"
cd "$REPO_DIR/docker" 2>/dev/null && docker compose up -d 2>/dev/null || true
systemctl start python-api streamlit-app node-api nextjs-rag jupyterlab 2>/dev/null || true

log "restore complete — verify with:"
log "  curl -fsS http://127.0.0.1:3000/ready"
log "  docker compose -f $REPO_DIR/docker/docker-compose.yml ps"
