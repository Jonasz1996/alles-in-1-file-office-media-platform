#!/usr/bin/env bash
# Hardware-inventaris voor fase 0. Draai dit op elke Proxmox-host (of de VM erop):
#   sudo ./hardware-inventaris.sh > inventaris-$(hostname).txt
# Het script leest alleen en wijzigt niets.
set -uo pipefail

section() { printf '\n## %s\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

echo "# Inventaris $(hostname) — $(date -Iseconds)"

section "Systeem"
uname -srm
# shellcheck source=/dev/null
[ -r /etc/os-release ] && . /etc/os-release && echo "OS: ${PRETTY_NAME:-onbekend}"
have pveversion && pveversion

section "CPU"
lscpu | grep -E '^(Model name|Socket\(s\)|Core\(s\) per socket|Thread\(s\) per core|CPU\(s\)|Flags)' \
  | sed -E 's/^(Flags:).*/\1 (ingekort)/'
grep -qw avx2 /proc/cpuinfo && echo "AVX2: ja" || echo "AVX2: nee"

section "Geheugen"
free -h

section "Disks"
lsblk -d -o NAME,SIZE,ROTA,TRAN,MODEL
have zpool && zpool list 2>/dev/null

section "Netwerk"
have ip && ip -br link
for nic in /sys/class/net/*; do
  name=$(basename "$nic")
  [ "$name" = lo ] && continue
  speed=$(cat "$nic/speed" 2>/dev/null || echo "?")
  echo "$name: ${speed} Mb/s"
done
have ip && ip -br addr

section "GPU en transcoding"
have lspci && lspci -nn | grep -Ei 'vga|3d|display' || echo "lspci niet gevonden"
ls /dev/dri 2>/dev/null && echo "/dev/dri aanwezig (VAAPI/QSV mogelijk)" || echo "geen /dev/dri"
have vainfo && vainfo 2>/dev/null | grep -E 'Driver version|VAProfile(H264|HEVC|AV1)' | head -20
have nvidia-smi && nvidia-smi --query-gpu=name,memory.total --format=csv
exit 0
