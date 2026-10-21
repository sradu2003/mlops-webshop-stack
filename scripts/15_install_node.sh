#!/usr/bin/env bash
# Install Node.js 20 LTS on Ubuntu 22.04.
#
# WHY THIS SCRIPT EXISTS
# ----------------------
# Ubuntu 22.04 (jammy) ships `nodejs` 12.22.9 from its own archive. Next.js
# 14 requires Node >= 18.17.0 and refuses to start on 12.x. Installing
# `nodejs` from the distro therefore produces a broken Next.js deployment.
#
# The Nodesource setup script installs the current LTS from the official
# upstream repository and takes precedence over the distro package.
#
# Verified against: Node 20.18.x (LTS 'iron')
set -euo pipefail

NODE_MAJOR="${NODE_MAJOR:-20}"

if [[ $EUID -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

apt-get update
apt-get -y install ca-certificates curl gnupg

# Nodesource signing key + repository
curl -fsSL "https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key" \
  | gpg --dearmor -o /usr/share/keyrings/nodesource.gpg

cat > /etc/apt/sources.list.d/nodesource.list <<EOF
deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main
EOF

apt-get update
apt-get -y install nodejs

installed="$(node --version)"
echo "[node] installed $installed"

# Fail fast: a Node older than the Next.js requirement must abort the deploy.
NODE_MAJOR_ACTUAL="${installed#v}"
NODE_MAJOR_ACTUAL="${NODE_MAJOR_ACTUAL%%.*}"
if (( NODE_MAJOR_ACTUAL < 18 )); then
  echo "[node] ERROR: Node $installed is too old; Next.js 14 requires >= 18.17.0" >&2
  exit 1
fi

# npm must be able to run lifecycle scripts for native modules
npm --version
echo "[node] ok"
