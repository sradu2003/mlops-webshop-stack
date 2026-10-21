#!/usr/bin/env bash
# Issue a Let's Encrypt wildcard certificate via DNS-01 and centralise it at
# /etc/ssl/shared so that nginx can use one certificate for every vhost.
#
# Usage:
#   10_ssl_wildcard.sh <domain> <email> [dns-plugin]
#
# The DNS plugin is REQUIRED for a wildcard certificate and is also what
# makes unattended renewal possible. Supported values depend on the
# certbot plugin packages installed by this script:
#
#   cloudflare  -> certbot-dns-cloudflare   (needs CLOUDFLARE_API_TOKEN)
#   route53     -> certbot-dns-route53      (needs AWS credentials)
#   duckdns     -> certbot-dns-duckdns      (needs DUCKDNS_TOKEN)
#   manual      -> no plugin; INTERACTIVE ONLY, CANNOT auto-renew
#
# Examples:
#   bash scripts/10_ssl_wildcard.sh example.com admin@example.com cloudflare
#   bash scripts/10_ssl_wildcard.sh example.com admin@example.com route53
set -euo pipefail

DOMAIN="${1:?domain required}"
EMAIL="${2:?email required}"
DNS_PLUGIN="${3:-cloudflare}"

REPO_DIR="${REPO_DIR:-/opt/mlops-webshop-stack}"
SSL_DIR=/etc/ssl/shared
SELF_DIR=/etc/ssl/selfsigned

export DEBIAN_FRONTEND=noninteractive

# --- Preflight --------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

# Verify the requested plugin is available BEFORE touching the web server,
# so a typo does not leave nginx pointing at a self-signed cert.
declare -A PLUGIN_PKGS=(
  [cloudflare]="certbot-dns-cloudflare"
  [route53]="certbot-dns-route53"
  [duckdns]="certbot-dns-duckdns"
)
declare -A PLUGIN_ARGS=(
  [cloudflare]="--dns-cloudflare --dns-cloudflare-credentials /root/.secrets/cf.ini"
  [route53]="--dns-route53"
  [duckdns]="--dns-duckdns --dns-duckdns-token /root/.secrets/duckdns.ini"
)

if [[ "$DNS_PLUGIN" == "manual" ]]; then
  echo "WARNING: --manual cannot renew automatically." >&2
  echo "         The ssl-renew timer will fail every time. Use a DNS plugin" >&2
  echo "         (cloudflare | route53 | duckdns) for unattended renewal." >&2
  PLUGIN_ARG="--manual --preferred-challenges dns"
else
  PKG="${PLUGIN_PKGS[$DNS_PLUGIN]:-}"
  if [[ -z "$PKG" ]]; then
    echo "unknown dns plugin '$DNS_PLUGIN'. Valid: ${!PLUGIN_PKGS[*]} manual" >&2
    exit 1
  fi
  apt-get -y install "$PKG"
  PLUGIN_ARG="${PLUGIN_ARGS[$DNS_PLUGIN]}"
fi

# --- Install certbot --------------------------------------------------------
apt-get -y install certbot

# --- Self-signed fallback so nginx can start before issuance completes ------
mkdir -p "$SSL_DIR" "$SELF_DIR"
if [[ ! -f "$SELF_DIR/fullchain.pem" ]]; then
  openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -keyout "$SELF_DIR/privkey.pem" \
    -out    "$SELF_DIR/fullchain.pem" \
    -subj "/CN=${DOMAIN}" \
    -addext "subjectAltName=DNS:${DOMAIN},DNS:*.${DOMAIN}"
  cp "$SELF_DIR/fullchain.pem" "$SELF_DIR/chain.pem"
fi

for f in fullchain.pem privkey.pem chain.pem; do
  [[ -e "$SSL_DIR/$f" ]] || ln -sf "$SELF_DIR/$f" "$SSL_DIR/$f"
done

# --- Issue the certificate --------------------------------------------------
# shellcheck disable=SC2086
certbot certonly $PLUGIN_ARG \
  --agree-tos -m "$EMAIL" --no-eff-email \
  --non-interactive \
  -d "$DOMAIN" -d "*.$DOMAIN"

LE=/etc/letsencrypt/live/$DOMAIN
ln -sf "$LE/fullchain.pem" "$SSL_DIR/fullchain.pem"
ln -sf "$LE/privkey.pem"   "$SSL_DIR/privkey.pem"
ln -sf "$LE/chain.pem"     "$SSL_DIR/chain.pem"

# --- Renewal hook -----------------------------------------------------------
cat > /usr/local/sbin/ssl-renew.sh <<'HK'
#!/usr/bin/env bash
set -euo pipefail
certbot renew --quiet
systemctl reload nginx || true
systemctl reload apache2 || true
HK
chmod +x /usr/local/sbin/ssl-renew.sh

install -m 644 "$REPO_DIR/systemd/ssl-renew.service" /etc/systemd/system/
install -m 644 "$REPO_DIR/systemd/ssl-renew.timer"   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ssl-renew.timer

# --- Verify the certificate actually chains to a trusted root -------------
if openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt "$LE/fullchain.pem" >/dev/null 2>&1; then
  echo "[ssl] certificate issued and trusted by the system CA bundle"
else
  echo "[ssl] WARNING: certificate does not verify against the system CA bundle" >&2
fi

echo "[ssl] cert centralised at $SSL_DIR (dns plugin: $DNS_PLUGIN)"
