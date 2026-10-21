#!/usr/bin/env bash
# Base system bootstrap for Ubuntu 22.04
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root"; exit 1; }

apt-get update
apt-get -y upgrade
apt-get -y install \
  curl wget gnupg lsb-release ca-certificates software-properties-common \
  ufw fail2ban htop tmux git unzip jq vim \
  build-essential

# Firewall
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# fail2ban defaults
systemctl enable --now fail2ban

# Swap (helps stay under 6 GB)
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

echo "[bootstrap] done"
