#!/bin/bash
# Nested/box Docker often leaves iptables-legacy FORWARD DROP, breaking container DNS/TCP.
set -euo pipefail
sudo iptables-legacy -P FORWARD ACCEPT
BR=$(ip -o link show type bridge | awk '/br-/{print $2}' | tr -d ':' | head -1)
if [ -n "${BR:-}" ]; then
  sudo iptables-legacy -C FORWARD -i "$BR" -o "$BR" -j ACCEPT 2>/dev/null || \
    sudo iptables-legacy -I FORWARD 1 -i "$BR" -o "$BR" -j ACCEPT
fi
echo "FORWARD policy set ACCEPT for ${BR:-all}"
