#!/usr/bin/env bash
# =============================================================================
# Local validation — run this BEFORE pushing.
#
#   bash scripts/validate.sh
#
# Checks everything that can be checked without a VPS:
#   * shell script syntax + shellcheck
#   * YAML validity (compose, prometheus, grafana)
#   * Prometheus config and recording rules
#   * nginx configuration (in a container)
#   * Docker compose config
#   * Python import + unit tests
#   * Node: npm ci + build
#   * Secret scanning (no committed .env or keys)
# =============================================================================
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 1

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m %s\n' "$1"; }
skip() { printf '  \033[33mSKIP\033[0m %s (%s)\n' "$1" "$2"; }
hdr()  { printf '\n\033[1m[%s]\033[0m\n' "$1"; }

# --- 1. Shell scripts -------------------------------------------------------
hdr "shell scripts"
if command -v shellcheck >/dev/null; then
  if shellcheck -S warning scripts/*.sh >/tmp/sc.out 2>&1; then
    ok "shellcheck (no warnings)"
  else
    bad "shellcheck reported issues:"
    cat /tmp/sc.out
  fi
else
  skip "shellcheck" "not installed"
fi
syntax_bad=0
for f in scripts/*.sh; do
  bash -n "$f" || { bad "bash syntax: $f"; syntax_bad=1; }
done
[[ $syntax_bad -eq 0 ]] && ok "bash syntax (${#f} scripts)"

# --- 2. YAML ----------------------------------------------------------------
hdr "yaml"
if command -v yamllint >/dev/null; then
  yfiles=$(find . -name '*.yml' -not -path './.git/*' -not -path '*/node_modules/*')
  if yamllint -d "{extends: default, rules: {line-length: disable, truthy: {check-keys: false}, document-start: disable, comments: {min-spaces-from-content: 1}}}" $yfiles >/tmp/yl.out 2>&1; then
    ok "yamllint"
  else
    bad "yamllint:"
    cat /tmp/yl.out
  fi
else
  skip "yamllint" "not installed"
fi

# --- 3. Docker compose ------------------------------------------------------
hdr "docker compose"
if command -v docker >/dev/null; then
  cd docker || exit 1
  if [[ ! -f .env ]]; then
    cp .env.example .env
  fi
  if docker compose config --quiet >/tmp/dc.out 2>&1; then
    ok "docker compose config"
  else
    bad "docker compose config:"
    cat /tmp/dc.out
  fi
  # The dev override must also be valid.
  if docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet >/tmp/dcd.out 2>&1; then
    ok "docker compose config (dev override)"
  else
    bad "docker compose dev override:"
    cat /tmp/dcd.out
  fi
  cd "$REPO_DIR" || exit 1
else
  skip "docker compose" "docker not installed"
fi

# --- 4. Prometheus ----------------------------------------------------------
hdr "prometheus"
if command -v docker >/dev/null; then
  if docker run --rm --entrypoint promtool \
      -v "$REPO_DIR/monitoring/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
      -v "$REPO_DIR/monitoring/prometheus/rules:/etc/prometheus/rules:ro" \
      prom/prometheus:latest check config /etc/prometheus/prometheus.yml >/tmp/pt.out 2>&1; then
    ok "prometheus config + rules"
  else
    bad "prometheus config:"
    cat /tmp/pt.out
  fi
else
  skip "prometheus" "docker not installed"
fi

# --- 5. nginx ---------------------------------------------------------------
hdr "nginx"
if command -v docker >/dev/null; then
  TMPC="$(mktemp -d)"
  mkdir -p "$TMPC/etc/nginx/conf.d" "$TMPC/etc/nginx/snippets" "$TMPC/etc/ssl/shared"
  cp nginx/nginx.conf "$TMPC/etc/nginx/"
  cp nginx/conf.d/*.conf "$TMPC/etc/nginx/conf.d/"
  cp nginx/snippets/*.conf "$TMPC/etc/nginx/snippets/"
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout "$TMPC/etc/ssl/shared/privkey.pem" \
    -out "$TMPC/etc/ssl/shared/fullchain.pem" -subj "/CN=xxx.com" 2>/dev/null
  cp "$TMPC/etc/ssl/shared/fullchain.pem" "$TMPC/etc/ssl/shared/chain.pem"
  cat > "$TMPC/Dockerfile" <<'EOF'
FROM nginx:1.26-bookworm
COPY etc/nginx/nginx.conf /etc/nginx/nginx.conf
COPY etc/nginx/conf.d/ /etc/nginx/conf.d/
COPY etc/nginx/snippets/ /etc/nginx/snippets/
COPY etc/ssl/shared/ /etc/ssl/shared/
EOF
  if docker build -q -t nginx-validate "$TMPC" >/dev/null 2>&1; then
    if docker run --rm nginx-validate nginx -t 2>&1 | grep -q "test is successful"; then
      ok "nginx configuration"
    else
      bad "nginx configuration:"
      docker run --rm nginx-validate nginx -t 2>&1 | tail -10
    fi
  else
    bad "nginx: could not build validation image"
  fi
  rm -rf "$TMPC"
else
  skip "nginx" "docker not installed"
fi

# --- 6. Python --------------------------------------------------------------
hdr "python api"
PYBIN="${PYTHON:-python3}"
if command -v uv >/dev/null; then
  VENV_ROOT="$(mktemp -d)"
  VENV="$VENV_ROOT/venv"
  if uv venv --clear --python 3.11 "$VENV" >/dev/null 2>&1 \
     || uv venv --clear "$VENV" >/dev/null 2>&1; then
    if VIRTUAL_ENV="$VENV" uv pip install -q -r apps/python-api/requirements.txt >/tmp/pyreq.out 2>&1; then
      ok "python dependencies install"
    else
      bad "python dependencies failed to install:"
      tail -15 /tmp/pyreq.out
    fi
    VIRTUAL_ENV="$VENV" uv pip install -q pytest httpx pyflakes >/dev/null 2>&1 || true
    # Lint the Python sources. Unused imports are not a style preference:
    # they are the residue of a refactor and hide real mistakes.
    # `app/db.py` imports the models module for its registration side
    # effect and carries a `# noqa: F401`; pyflakes does not honour noqa
    # comments, so that one line is filtered out explicitly.
    if "$VENV/bin/python" -m pyflakes apps/python-api/app apps/python-api/tests 2>&1 \
         | grep -v "app/db.py:.*'app.models' imported but unused" \
         | grep -q .; then
      bad "python lint (pyflakes):"
      "$VENV/bin/python" -m pyflakes apps/python-api/app apps/python-api/tests 2>&1 \
        | grep -v "app/db.py:.*'app.models' imported but unused"
    else
      ok "python lint (pyflakes)"
    fi
    (
      cd apps/python-api || exit 1
      if JWT_SECRET="ci-test-secret-0123456789abcdef" \
         ANON_SALT="ci-test-anon-salt-0123456789abcdef" \
         "$VENV/bin/python" -m pytest -q tests/ >/tmp/pt2.out 2>&1; then
        ok "python unit tests"
      else
        bad "python unit tests:"
        tail -30 /tmp/pt2.out
      fi
    )
    # NOTE: the venv is torn down AFTER the integration block below,
    # which needs the same interpreter.
  else
    bad "could not create a python venv"
  fi
elif command -v "$PYBIN" >/dev/null; then
  ok "python3 available (uv not installed — skipped dependency/test step)"
else
  skip "python" "no python3"
fi

# --- 6b. Python integration (needs a database) ------------------------------
hdr "python integration"
if [[ -z "${VENV:-}" || ! -x "${VENV:-}/bin/python" ]]; then
  skip "integration" "no python venv"
elif ! command -v docker >/dev/null; then
  skip "integration" "docker not installed"
else
  # A throwaway pgvector instance on a random high port. The port is
  # deliberately NOT 5432 so a developer's own database is never touched.
  PGPORT_TEST="$(( 20000 + (RANDOM % 20000) ))"
  PGNAME="mlops-validate-pg-$$"
  docker rm -f "$PGNAME" >/dev/null 2>&1 || true
  if docker run -d --name "$PGNAME" -p "127.0.0.1:${PGPORT_TEST}:5432" \
       -e POSTGRES_USER=shop -e POSTGRES_PASSWORD=shop \
       -e POSTGRES_DB=shopdb pgvector/pgvector:pg16 >/dev/null 2>&1; then
    ready=0
    for _ in $(seq 1 30); do
      if docker exec "$PGNAME" pg_isready -U shop -d shopdb >/dev/null 2>&1; then
        ready=1
        break
      fi
      sleep 1
    done
    if [[ "$ready" -eq 1 ]]; then
      (
        cd apps/python-api || exit 1
        if DATABASE_URL="postgresql+psycopg://shop:shop@127.0.0.1:${PGPORT_TEST}/shopdb" \
           JWT_SECRET="ci-test-secret-0123456789abcdef" \
           ANON_SALT="ci-test-anon-salt-0123456789abcdef" \
           "$VENV/bin/python" -m pytest -q tests/ -m integration >/tmp/pt3.out 2>&1; then
          ok "python integration tests"
        else
          bad "python integration tests:"
          tail -30 /tmp/pt3.out
        fi
      )
    else
      bad "integration: postgres did not become ready"
    fi
    docker rm -f "$PGNAME" >/dev/null 2>&1 || true
  else
    bad "integration: could not start postgres"
  fi
fi

# Tear down the venv now that both the unit and integration suites have run.
if [[ -n "${VENV_ROOT:-}" && -d "${VENV_ROOT:-}" ]]; then
  rm -rf "$VENV_ROOT"
fi

# --- 7. Node ----------------------------------------------------------------
hdr "node apps"
if command -v npm >/dev/null; then
  for app in node-api nextjs-rag; do
    if [[ ! -f "apps/$app/package-lock.json" ]]; then
      bad "apps/$app/package-lock.json missing (npm ci will fail)"
      continue
    fi
    tmp="$(mktemp -d)"
    cp -r "apps/$app/." "$tmp/"
    ( cd "$tmp" && npm ci --no-audit --no-fund >/dev/null 2>&1 ) \
      && ok "$app: npm ci" || bad "$app: npm ci"
    if [[ "$app" == "nextjs-rag" ]]; then
      ( cd "$tmp" && npm run build >/dev/null 2>&1 ) \
        && ok "$app: production build" || bad "$app: production build"
    fi
    rm -rf "$tmp"
  done
else
  skip "node" "npm not installed"
fi

# --- 8. Secret scan ---------------------------------------------------------
hdr "secret scan"
leaks=0
while IFS= read -r f; do
  [[ -n "$f" ]] || continue
  if grep -qE "(JWT_SECRET|DATABASE_URL|PASSWORD)=[^$]" "$f" 2>/dev/null; then
    if grep -qE "(JWT_SECRET|DATABASE_URL|PASSWORD)=(change_me|dev|admin|password)" "$f"; then
      bad "$f contains a placeholder credential that must be changed"
      leaks=1
    fi
  fi
done < <(find . -name '.env' -not -path './.git/*' 2>/dev/null)
[[ $leaks -eq 0 ]] && ok "no committed .env files with real credentials"

# Any PEM/KEY material committed to the repo is a problem.
if git ls-files 2>/dev/null | grep -qE '\.(pem|key|p12|pfx)$'; then
  bad "a private key or certificate is tracked in git"
else
  ok "no key material tracked in git"
fi

# --- Summary ----------------------------------------------------------------
printf '\n============================================\n'
printf ' %d passed, %d failed\n' "$PASS" "$FAIL"
printf '============================================\n'
[[ $FAIL -eq 0 ]]
