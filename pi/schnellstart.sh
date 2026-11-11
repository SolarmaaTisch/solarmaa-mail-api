#!/usr/bin/env bash
# Solarmaa Energie-Dashboard: Installation mit einem einzigen Befehl auf einem frischen Raspberry Pi OS.
#
#   curl -fsSL https://raw.githubusercontent.com/SolarmaaTisch/solarmaa-mail-api/claude/vibrant-newton-yc8ppy/pi/schnellstart.sh | sudo bash
#
# Holt das Programm von GitHub und führt pi/install.sh aus. Danach läuft die Demo.
set -euo pipefail
BRANCH="${SOLARMAA_BRANCH:-claude/vibrant-newton-yc8ppy}"
REPO="https://github.com/SolarmaaTisch/solarmaa-mail-api.git"
ZIEL=/root/solarmaa-mail-api

[[ $EUID -eq 0 ]] || { echo "Bitte mit sudo ausführen."; exit 1; }
echo "==> Git installieren"
apt-get update -qq && apt-get install -y -qq git >/dev/null
echo "==> Programm holen ($BRANCH)"
if [[ -d "$ZIEL/.git" ]]; then
  git -C "$ZIEL" fetch --quiet origin "$BRANCH" && git -C "$ZIEL" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
else
  git clone --quiet -b "$BRANCH" "$REPO" "$ZIEL"
fi
exec "$ZIEL/pi/install.sh" "$@"
