#!/usr/bin/env bash
# Install Docker Engine + Compose plugin and bring up the Dockerised
# services (Open WebUI, Ollama, Listmonk, Prometheus, Grafana, node-exporter).
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/mlops-webshop-stack}"
DOCKER_DIR="$REPO_DIR/docker"

if [[ $EUID -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

# --- Docker Engine ----------------------------------------------------------
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi
docker --version
docker compose version

# --- Environment ------------------------------------------------------------
# The compose file reads docker/.env. The ORIGINAL script copied
# ../.env.example, which does not exist at the repository root, so the
# script aborted with "cp: ../.env.example: No such file or directory"
# BEFORE `docker compose up` was ever reached.
cd "$DOCKER_DIR"
if [[ ! -f .env ]]; then
  if [[ -f "$DOCKER_DIR/.env.example" ]]; then
    cp "$DOCKER_DIR/.env.example" .env
  elif [[ -f "$REPO_DIR/.env.example" ]]; then
    cp "$REPO_DIR/.env.example" .env
  else
    echo "ERROR: no .env.example found — cannot configure the compose stack" >&2
    exit 1
  fi
  chmod 600 .env
  echo "[docker] created docker/.env from the template — REVIEW IT before going live"
fi

# --- Validate before starting ----------------------------------------------
docker compose config --quiet || {
  echo "ERROR: compose configuration is invalid" >&2
  exit 1
}

# --- Start ------------------------------------------------------------------
docker compose up -d

# --- Wait for health --------------------------------------------------------
echo "[docker] waiting for services to report healthy..."
deadline=$((SECONDS + 300))
while (( SECONDS < deadline )); do
  unhealthy="$(docker compose ps --format '{{.Service}} {{.Health}}' \
    | awk '$2 != "healthy" && $2 != "" {print $1}')"
  if [[ -z "$unhealthy" ]]; then
    echo "[docker] all services healthy"
    break
  fi
  echo "[docker] waiting on: $unhealthy"
  sleep 10
done

docker compose ps

# --- Pull the model ---------------------------------------------------------
MODEL="${OLLAMA_MODEL:-qwen2.5:0.5b}"
echo "[docker] pulling model $MODEL"
docker compose exec -T ollama ollama pull "$MODEL" || {
  echo "[docker] WARNING: model pull failed — the RAG service will not answer" >&2
}

echo "[docker] up"
