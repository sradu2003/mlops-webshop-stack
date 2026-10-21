#!/bin/bash
# Enable the pgvector extension in the openwebui database.
# initdb scripts run against $POSTGRES_DB only, so the second database
# needs an explicit connection.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'EOSQL'
    CREATE EXTENSION IF NOT EXISTS vector;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname openwebui <<'EOSQL'
    CREATE EXTENSION IF NOT EXISTS vector;
EOSQL
