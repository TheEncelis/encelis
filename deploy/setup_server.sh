#!/usr/bin/env bash
# One-time server preparation for a fresh Ubuntu 24.04. Run as root.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq python3 python3-venv python3-pip nginx certbot python3-certbot-nginx ufw sqlite3 unattended-upgrades

# Dedicated, unprivileged app user (no login shell).
id encelis >/dev/null 2>&1 || useradd --system --home /srv/encelis --shell /usr/sbin/nologin --gid www-data encelis
mkdir -p /srv/encelis
chown encelis:www-data /srv/encelis

# Firewall: SSH + HTTP/HTTPS only.
ufw allow OpenSSH
ufw allow 'Nginx Full'
# Keep existing remote-desktop access working if xrdp is installed on this host.
if systemctl is-active --quiet xrdp; then ufw allow 3389/tcp comment 'xrdp (pre-existing)'; fi
ufw --force enable

# Automatic security updates.
dpkg-reconfigure -f noninteractive unattended-upgrades

systemctl enable --now nginx
rm -f /etc/nginx/sites-enabled/default
echo "SETUP-DONE"
