#!/usr/bin/env bash
# Harden the host: SSH, firewall, fail2ban, kernel and audit settings.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

# --- SSH --------------------------------------------------------------------
# Keep key-based root access (for automation) but disable password auth.
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/'  /etc/ssh/sshd_config
sed -i 's/^#\?PermitEmptyPasswords.*/PermitEmptyPasswords no/'   /etc/ssh/sshd_config
sed -i 's/^#\?X11Forwarding.*/X11Forwarding no/'                /etc/ssh/sshd_config
sed -i 's/^#\?MaxAuthTries.*/MaxAuthTries 3/'                   /etc/ssh/sshd_config
sed -i 's/^#\?ClientAliveInterval.*/ClientAliveInterval 300/'  /etc/ssh/sshd_config
systemctl reload ssh

# --- fail2ban ---------------------------------------------------------------
cat > /etc/fail2ban/jail.d/local.conf <<'JL'
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5
banaction = ufw

[sshd]
enabled = true
maxretry = 3
bantime = 4h

[nginx-http-auth]
enabled = true
maxretry = 6
bantime = 1h

[nginx-limit-req]
enabled = true
maxretry = 10
findtime = 60
bantime = 1h
port = http,https
logpath = /var/log/nginx/error.log
JL
systemctl restart fail2ban

# --- Kernel / network hardening ---------------------------------------------
cat > /etc/sysctl.d/99-webshop-hardening.conf <<'SYS'
# Do not respond to ICMP broadcasts (smurf amplification)
net.ipv4.icmp_echo_ignore_broadcasts = 1
# Ignore bogus ICMP
net.ipv4.icmp_ignore_bogus_error_responses = 1
# Do not accept source-routed packets
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
# Do not accept ICMP redirects
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.secure_redirects = 0
# Do not send ICMP redirects
net.ipv4.conf.all.send_redirects = 0
# Protect against SYN flood
net.ipv4.tcp_syncookies = 1
# Log and drop suspicious packets
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
# Use the reverse-path filter in loose mode for asymmetric routing
net.ipv4.conf.all.accept_local = 0
# Do not expose the kernel version in bouncers
kernel.randomize_va_space = 2
# Increase the file descriptor limit for nginx
fs.file-max = 65535
SYS
sysctl --system >/dev/null

# --- Disable core dumps (they can contain secrets) ---------------------------
cat > /etc/security/limits.d/99-webshop.conf <<'LIM'
*               hard    core            0
root            hard    core            0
LIM
sysctl -w fs.suid_dumpable=0 >/dev/null 2>&1 || true

# --- APT: do not install recommended packages automatically -------------------
echo 'APT::Install-Recommends "false";' > /etc/apt/apt.conf.d/99norecommends
echo 'APT::Install-Suggests "false";' >> /etc/apt/apt.conf.d/99norecommends

# --- Docker: restrict the daemon socket --------------------------------------
# Only the deploy user may talk to the Docker daemon.
#
# SUDO_USER is only set when the script is invoked via `sudo`. On a direct
# root login (or `sudo -s bash script`) it is UNSET, and under `set -u`
# `usermod -aG docker "$SUDO_USER"` aborted the whole script with
# "SUDO_USER: unbound variable" — the hardening steps after this point
# (unattended-upgrades) never ran.
if [[ -d /etc/docker ]]; then
  groupadd -f docker
  DEPLOY_USER="${SUDO_USER:-${DEPLOY_USER:-}}"
  if [[ -n "$DEPLOY_USER" ]] && id "$DEPLOY_USER" >/dev/null 2>&1; then
    usermod -aG docker "$DEPLOY_USER"
    echo "[harden] added $DEPLOY_USER to the docker group"
  else
    echo "[harden] no deploy user detected (SUDO_USER unset) — add one manually:" >&2
    echo "          usermod -aG docker <your-user>" >&2
  fi
fi

# --- unattended-upgrades ----------------------------------------------------
apt-get -y install unattended-upgrades
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'AU'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
AU

echo "[harden] done"
