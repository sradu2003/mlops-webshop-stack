-- Runs once, on first initialisation of the postgres data volume.
-- Creates the additional databases used by the stack.
-- NOTE: initdb scripts only run when the data directory is empty. For an
-- existing volume, run the equivalent statements manually:
--   docker compose exec listmonk-db psql -U shop -d shopdb -c 'CREATE DATABASE openwebui OWNER shop;'
--
-- `CREATE DATABASE` cannot run inside a DO block, so the guard is done with a
-- psql conditional instead (SELECT ... \gexec).

SELECT 'CREATE DATABASE openwebui OWNER shop'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'openwebui')
\gexec
