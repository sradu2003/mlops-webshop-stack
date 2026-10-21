#!/usr/bin/env bash
# Create the application database and role in the host PostgreSQL instance,
# and the WordPress database in MariaDB.
#
# The original version sourced /opt/mlops-webshop-stack/.env under `set -u`
# without checking that the file exists, so it aborted with
# "No such file or directory" on a fresh machine.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/mlops-webshop-stack}"
ENV_FILE="${ENV_FILE:-$REPO_DIR/.env}"

if [[ $EUID -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found." >&2
  echo "       Create it first:  cp $REPO_DIR/.env.example $ENV_FILE" >&2
  echo "       then edit it and re-run this script." >&2
  exit 1
fi

# The path is a variable by design: the operator may place .env anywhere.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${POSTGRES_USER:?POSTGRES_USER is not set in $ENV_FILE}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is not set in $ENV_FILE}"
: "${POSTGRES_DB:?POSTGRES_DB is not set in $ENV_FILE}"
: "${MARIADB_DATABASE:?MARIADB_DATABASE is not set in $ENV_FILE}"
: "${MARIADB_USER:?MARIADB_USER is not set in $ENV_FILE}"
: "${MARIADB_PASSWORD:?MARIADB_PASSWORD is not set in $ENV_FILE}"

# --- PostgreSQL -------------------------------------------------------------
echo "[db] creating role and database in postgresql"
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${POSTGRES_USER}') THEN
    CREATE ROLE ${POSTGRES_USER} LOGIN PASSWORD '${POSTGRES_PASSWORD}';
  ELSE
    ALTER ROLE ${POSTGRES_USER} LOGIN PASSWORD '${POSTGRES_PASSWORD}';
  END IF;
END
\$\$;
SQL

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'" | grep -q 1; then
  sudo -u postgres createdb -O "${POSTGRES_USER}" "${POSTGRES_DB}"
  echo "[db] created database ${POSTGRES_DB}"
else
  echo "[db] database ${POSTGRES_DB} already exists"
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${POSTGRES_DB}" <<SQL
GRANT ALL PRIVILEGES ON DATABASE ${POSTGRES_DB} TO ${POSTGRES_USER};
SQL

# pgvector is needed by Open WebUI's RAG store.
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${POSTGRES_DB}" -c "CREATE EXTENSION IF NOT EXISTS vector;" \
  || echo "[db] WARNING: pgvector extension not available (install postgresql-16-pgvector)"

# --- MariaDB ---------------------------------------------------------------
echo "[db] creating database and user in mariadb"
mysql -uroot <<SQL
CREATE DATABASE IF NOT EXISTS \`${MARIADB_DATABASE}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${MARIADB_USER}'@'localhost' IDENTIFIED BY '${MARIADB_PASSWORD}';
ALTER USER '${MARIADB_USER}'@'localhost' IDENTIFIED BY '${MARIADB_PASSWORD}';
GRANT ALL ON \`${MARIADB_DATABASE}\`.* TO '${MARIADB_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL

echo "[db] done"
