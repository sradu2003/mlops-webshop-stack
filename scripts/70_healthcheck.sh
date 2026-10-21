#!/usr/bin/env bash
# =============================================================================
# Health check / acceptance test for the whole stack.
#
# Run it on the VPS after a deploy, and in CI before a release:
#   bash scripts/70_healthcheck.sh
#
# Exit code 0 = everything healthy, 1 = at least one check failed.
#
# PLATFORM NOTE
# -------------
# This script is written for the Ubuntu 22.04 VPS, which is where it is
# authoritative. On macOS the systemd, TLS, ufw and backup checks cannot
# pass by construction — the services are not managed by systemd there.
# Run it on macOS to check the container tier, and on the VPS for the
# whole stack. A `Linux-only` marker is printed next to each check that
# cannot apply off the VPS.
# =============================================================================
set -uo pipefail

IS_LINUX=false
if [[ "$(uname -s)" == "Linux" ]]; then
  IS_LINUX=true
fi

REPO_DIR="${REPO_DIR:-/opt/mlops-webshop-stack}"
# The compose project may have been started from a different checkout than
# the one this script lives in. Resolve the compose file from the RUNNING
# containers rather than assuming REPO_DIR, otherwise every container check
# reports "not running" on a machine where the stack is perfectly healthy.
COMPOSE_FILE=""
for cand in "$REPO_DIR/docker/docker-compose.yml" "./docker/docker-compose.yml"; do
  if [[ -f "$cand" ]]; then
    COMPOSE_FILE="$(cd "$(dirname "$cand")" && pwd)/docker-compose.yml"
    break
  fi
done
PASS=0
FAIL=0
WARN=0

green() { printf '\033[32m%s\033[0m' "$1"; }
red()   { printf '\033[31m%s\033[0m' "$1"; }
yellow(){ printf '\033[33m%s\033[0m' "$1"; }

ok()   { PASS=$((PASS+1)); printf '  %s %s\n' "$(green 'PASS')" "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  %s %s\n' "$(red   'FAIL')" "$1"; }
warn() { WARN=$((WARN+1)); printf '  %s %s\n' "$(yellow 'WARN')" "$1"; }

# Check an HTTP endpoint.
http_check() {
  local name="$1" url="$2" expect="${3:-200}"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' -m "${4:-10}" "$url" 2>/dev/null || echo 000)"
  if [[ "$code" == "$expect" ]]; then
    ok "$name ($code)"
  else
    bad "$name — expected $expect, got $code ($url)"
  fi
}

# Check a TCP port is listening.
port_check() {
  local name="$1" host="$2" port="$3"
  if (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null; then
    exec 3<&- 3>&-
    ok "$name (${host}:${port} open)"
  else
    bad "$name — ${host}:${port} not reachable"
  fi
}

echo "=============================================================="
echo " mlops-webshop-stack health check"
echo " $(date -Is)"
echo "=============================================================="

# --- 1. Systemd services ---------------------------------------------------
echo
echo "[systemd services]"
if [[ "$IS_LINUX" != "true" ]]; then
  warn "systemd checks skipped — this is not Linux (run the health check on the VPS)"
else
  for unit in python-api streamlit-app node-api nextjs-rag jupyterlab nginx apache2 postgresql mariadb; do
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
      ok "$unit is active"
    else
      bad "$unit is NOT active"
    fi
  done
fi

# --- 2. Host application ports --------------------------------------------
echo
echo "[host application ports]"
port_check "python-api"   127.0.0.1 3000
port_check "streamlit"    127.0.0.1 3001
port_check "node-api"     127.0.0.1 8000
port_check "nextjs-rag"   127.0.0.1 8001
port_check "jupyterlab"   127.0.0.1 8888
port_check "nginx-http"   127.0.0.1 80
port_check "nginx-https"  127.0.0.1 443

# --- 3. Application health endpoints --------------------------------------
echo
echo "[application endpoints]"
http_check "python-api /health"  "http://127.0.0.1:3000/health"
http_check "python-api /ready"   "http://127.0.0.1:3000/ready"
http_check "python-api /metrics" "http://127.0.0.1:3000/metrics"
http_check "node-api /health"    "http://127.0.0.1:8000/health"
http_check "node-api /metrics"   "http://127.0.0.1:8000/metrics"
http_check "nextjs-rag /api/metrics" "http://127.0.0.1:8001/api/metrics"
http_check "streamlit health"    "http://127.0.0.1:3001/_stcore/health"

# --- 4. Docker services ----------------------------------------------------
echo
echo "[docker services]"
if command -v docker >/dev/null; then
  # `docker compose` needs the project directory: the env file
  # (docker/.env) and the relative volume paths live next to the compose
  # file. Run every compose call from that directory.
  COMPOSE_DIR="$(dirname "${COMPOSE_FILE:-$REPO_DIR/docker/docker-compose.yml}")"
  if [[ ! -d "$COMPOSE_DIR" ]]; then
    warn "compose directory not found: $COMPOSE_DIR — skipping container checks"
    COMPOSE_DIR=""
  fi
  cd "$COMPOSE_DIR" 2>/dev/null || true
  for svc in ollama open-webui listmonk listmonk-db prometheus grafana node-exporter; do
    state="$(docker compose ps --format '{{.Service}} {{.State}} {{.Health}}' \
      2>/dev/null | awk -v s="$svc" '$1==s {print $2, $3}')"
    if [[ -z "$state" ]]; then
      bad "$svc is not running"
    elif [[ "$state" == *"running healthy"* || "$state" == "running " ]]; then
      ok "$svc ($state)"
    else
      bad "$svc ($state)"
    fi
  done

  # listmonk-init must have completed successfully
  init_state="$(docker compose ps -a --format '{{.Service}} {{.Status}}' 2>/dev/null \
    | awk '$1=="listmonk-init" {print $2, $3, $4}')"
  if [[ "$init_state" == *"Exited (0)"* ]]; then
    ok "listmonk-init completed successfully (schema installed)"
  else
    bad "listmonk-init did not complete cleanly — listmonk will crash-loop: $init_state"
  fi

  # Diagnose the most common container failure: the Postgres volume was
  # initialised with an older password, so the initdb scripts never re-ran
  # and every client that authenticates fails. The raw log line is
  # cryptic, so translate it into the actual remedy.
  db_state="$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null \
    | awk '$1=="listmonk-db" {print $2}')"
  if [[ "$db_state" == "running" ]]; then
    if docker compose logs --tail=200 open-webui 2>/dev/null \
         | grep -q "password authentication failed for user"; then
      bad "open-webui cannot authenticate to listmonk-db — the Postgres volume holds an OLD password (initdb only runs on an empty volume). Fix:  docker exec listmonk-db psql -U ${LISTMONK_DB_USER:-shop} -d ${LISTMONK_DB_NAME:-shopdb} -c \"ALTER ROLE ${LISTMONK_DB_USER:-shop} WITH PASSWORD '<new>';\""
    fi
  fi

  http_check "ollama /api/tags"       "http://127.0.0.1:${OLLAMA_HOST_PORT:-11434}/api/tags"
  http_check "open-webui /health"     "http://127.0.0.1:8085/health"
  http_check "grafana /api/health"    "http://127.0.0.1:3030/api/health"
  http_check "prometheus /-/healthy"  "http://127.0.0.1:9091/-/healthy"
  http_check "listmonk"               "http://127.0.0.1:9000/"
else
  warn "docker not installed — skipping container checks"
fi

# --- 5. Prometheus targets -------------------------------------------------
echo
echo "[prometheus targets]"
if command -v curl >/dev/null; then
  targets="$(curl -s -m 5 http://127.0.0.1:9091/api/v1/targets 2>/dev/null)"
  if [[ -n "$targets" ]]; then
    while IFS='|' read -r job health err; do
      job="$(echo "$job" | xargs)"
      health="$(echo "$health" | xargs)"
      err="$(echo "$err" | xargs)"
      if [[ "$health" == "up" ]]; then
        ok "target $job is up"
      else
        # Include the scrape error: "target is down" alone does not tell
        # the operator whether the service is dead or the scrape path in
        # prometheus.yml points at the wrong URL.
        if [[ -n "$err" ]]; then
          bad "target $job is $health — $err"
        else
          bad "target $job is $health"
        fi
      fi
    done < <(echo "$targets" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(0)
for t in d.get('data',{}).get('activeTargets',[]):
    err = (t.get('lastError') or '').replace(chr(10),' ').replace('|', '/')[:160]
    print(t['labels'].get('job','?') + '|' + t.get('health','?') + '|' + err)
" 2>/dev/null)
  else
    warn "could not query prometheus targets"
  fi
fi

# --- 6. TLS ----------------------------------------------------------------
echo
echo "[tls]"
if [[ "$IS_LINUX" != "true" ]]; then
  warn "TLS checks skipped — not Linux (the cert lives in /etc/ssl/shared on the VPS)"
elif [[ -f /etc/ssl/shared/fullchain.pem ]]; then
  if openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt /etc/ssl/shared/fullchain.pem >/dev/null 2>&1; then
    ok "certificate at /etc/ssl/shared/fullchain.pem is trusted"
  else
    bad "certificate at /etc/ssl/shared/fullchain.pem is NOT trusted (self-signed?)"
  fi
  expiry="$(openssl x509 -enddate -noout -in /etc/ssl/shared/fullchain.pem 2>/dev/null | cut -d= -f2)"
  if [[ -n "$expiry" ]]; then
    days=$(( ( $(date -d "$expiry" +%s 2>/dev/null || echo 0) - $(date +%s) ) / 86400 ))
    if (( days > 30 )); then
      ok "certificate expires in ${days} days"
    else
      warn "certificate expires in ${days} days — renew now"
    fi
  fi
else
  warn "no certificate at /etc/ssl/shared/fullchain.pem"
fi

# --- 7. Security checks ----------------------------------------------------
echo
echo "[security]"
if [[ "$IS_LINUX" != "true" ]]; then
  warn "Jupyter token check skipped — not Linux (/opt/jupyter/.env is a VPS artifact)"
elif [[ -f /opt/jupyter/.env ]] && grep -qE "JUPYTER_TOKEN=.+" /opt/jupyter/.env; then
  ok "JupyterLab has an access token set"
else
  bad "JupyterLab has NO access token — it is an unauthenticated RCE hole"
fi

if ss -ltn 2>/dev/null | grep -qE '0\.0\.0\.0:(8888|9090|10000|8084|3000|8000|8001|3001)'; then
  bad "a management port is bound to 0.0.0.0 (externally reachable)"
else
  ok "no management port is bound to 0.0.0.0"
fi

if [[ "$IS_LINUX" != "true" ]]; then
  warn "ufw check skipped — not Linux"
elif ufw status 2>/dev/null | grep -q "Status: active"; then
  ok "ufw is active"
else
  warn "ufw is not active (or not installed)"
fi

# --- 8. Backup freshness ----------------------------------------------------
echo
echo "[backups]"
if [[ "$IS_LINUX" != "true" ]]; then
  warn "backup freshness check skipped — not Linux (/var/backups is a VPS path)"
else
  latest="$(find /var/backups/mlops-webshop -type f -name '*.gz' -mtime -2 2>/dev/null | head -1)"
  if [[ -n "$latest" ]]; then
    ok "a backup exists from the last 2 days"
  else
    bad "no recent backup found in /var/backups/mlops-webshop"
  fi
fi

# --- Summary ----------------------------------------------------------------
echo
echo "=============================================================="
printf ' %s passed, %s failed, %s warnings\n' \
  "$(green "$PASS")" "$(red "$FAIL")" "$(yellow "$WARN")"
echo "=============================================================="

[[ $FAIL -eq 0 ]]
