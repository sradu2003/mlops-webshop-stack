#!/usr/bin/env bash
# Install the native system services: nginx, apache2, MariaDB, PostgreSQL,
# PHP and the base web roots.
#
# NOTE: Node.js and Python 3.11 are installed by the dedicated scripts
# 15_install_node.sh and 15_install_python.sh, because the Ubuntu 22.04
# versions of both are too old for this stack.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/mlops-webshop-stack}"

if [[ $EUID -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

apt-get update
apt-get -y install \
  nginx apache2 \
  mariadb-server postgresql postgresql-contrib \
  php php-fpm php-mysql php-pgsql php-cli php-curl php-gd php-xml php-mbstring php-zip \
  libapache2-mod-php \
  phpmyadmin \
  rsync

# --- Apache: free port 80 (nginx owns it) ---------------------------------
a2dissite 000-default || true
a2enmod proxy proxy_http rewrite headers ssl setenvif

# --- Install our configs ----------------------------------------------------
install -m 644 "$REPO_DIR/apache2/ports.conf" /etc/apache2/ports.conf
install -m 644 "$REPO_DIR/apache2/conf-available/security.conf" /etc/apache2/conf-available/security.conf
a2enconf security

for f in "$REPO_DIR"/apache2/sites-available/*.conf; do
  install -m 644 "$f" /etc/apache2/sites-available/
  a2ensite "$(basename "$f" .conf)"
done

# --- nginx ------------------------------------------------------------------
install -m 644 "$REPO_DIR/nginx/nginx.conf" /etc/nginx/nginx.conf
mkdir -p /etc/nginx/snippets /etc/nginx/conf.d
install -m 644 "$REPO_DIR"/nginx/snippets/*.conf /etc/nginx/snippets/
rm -f /etc/nginx/sites-enabled/default
install -m 644 "$REPO_DIR"/nginx/conf.d/*.conf /etc/nginx/conf.d/

# nginx must not try to serve its own default site
rm -f /etc/nginx/sites-enabled/*

# --- Web roots --------------------------------------------------------------
mkdir -p /var/www/html/xxx.com/public \
         /var/www/html/blog.xxx.com/public \
         /var/www/html/docs.xxx.com/public \
         /var/www/html/_default \
         /var/www/html/_acme
chown -R www-data:www-data /var/www/html

# --- MariaDB low-RAM tuning -------------------------------------------------
# The default innodb_buffer_pool_size on a 8 GB VPS is 128M, which is fine,
# but max_connections=151 multiplied by per-connection buffers is what
# actually exhausts RAM. Cap it.
cat > /etc/mysql/mariadb.conf.d/99-low-ram.cnf <<'CNF'
[mysqld]
innodb_buffer_pool_size = 128M
innodb_log_file_size    = 32M
key_buffer_size         16M
max_connections         50
performance_schema      = OFF
skip_name_resolve
CNF

# --- PostgreSQL low-RAM tuning ----------------------------------------------
PGCONF="$(ls /etc/postgresql/*/main/postgresql.conf | head -1)"
if [[ -f "$PGCONF" ]]; then
  cat >> "$PGCONF" <<'PG'

# --- mlops-webshop-stack low-RAM tuning ---
shared_buffers = 128MB
effective_cache_size = 512MB
work_mem = 4MB
maintenance_work_mem = 64MB
max_connections = 50
PG
  # Also enable the pgvector extension if the package is available.
  PGVER="$(ls /etc/postgresql | head -1)"
  if ! apt-get -y install "postgresql-${PGVER}-pgvector" 2>/dev/null; then
    echo "[native] pgvector package not available — install it manually if you need vector search"
  fi
fi

# --- Start & validate --------------------------------------------------------
systemctl restart mariadb
systemctl restart postgresql

# Validate BOTH configs BEFORE restarting, so a broken file does not take
# the site down.
apache2ctl configtest
nginx -t
systemctl restart apache2
systemctl restart nginx

echo "[native] installed"
