#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

apt-get update
apt-get install -y ca-certificates curl docker.io docker-compose-v2 ufw
systemctl enable --now docker

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale must be installed and joined to the tailnet before bootstrap." >&2
  echo "See https://tailscale.com/download/linux" >&2
  exit 1
fi

systemctl enable --now tailscaled
if ! tailscale ip -4 >/dev/null 2>&1; then
  echo "This VM is not connected to Tailscale. Run 'sudo tailscale up' first." >&2
  exit 1
fi

ufw allow OpenSSH
# Remove rules created by older versions of this script, then allow shared Nginx
# web traffic only through the Tailscale interface.
ufw --force delete allow 80/tcp >/dev/null 2>&1 || true
ufw --force delete allow 443/tcp >/dev/null 2>&1 || true
ufw --force delete allow 443/udp >/dev/null 2>&1 || true
ufw allow in on tailscale0 to any port 80 proto tcp
ufw allow in on tailscale0 to any port 443 proto tcp
ufw allow in on tailscale0 to any port 443 proto udp
ufw --force enable

docker network inspect openjack-edge >/dev/null 2>&1 || docker network create openjack-edge

install -m 0644 "$repo_root/deploy/airport-wait-times.service" /etc/systemd/system/airport-wait-times.service
systemctl daemon-reload
systemctl enable airport-wait-times.service

echo "Host bootstrap complete. Configure .env and the shared Review Reviews proxy, then start airport-wait-times.service."
