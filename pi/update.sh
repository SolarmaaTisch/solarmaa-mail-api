#!/usr/bin/env bash
# Solarmaa Dashboard: Update aus dem Git-Repository (wird von systemd als root gestartet).
# Manuell: sudo /usr/local/sbin/solarmaa-update
set -uo pipefail
SRC=/opt/solarmaa-dash/src
LOG=/var/lib/solarmaa-dash/update.log
BRANCH=$(cat /etc/solarmaa-dash/update-branch 2>/dev/null || echo main)
{
  echo "=== Update $(date '+%d.%m.%Y %H:%M:%S'), Branch $BRANCH ==="
  rm -f /var/lib/solarmaa-dash/update-anfrage
  if [[ ! -d "$SRC/.git" ]]; then echo "FEHLER: $SRC ist kein Git-Repository"; exit 1; fi
  ALT=$(git -C "$SRC" rev-parse --short HEAD)
  if ! git -C "$SRC" fetch --quiet origin "$BRANCH"; then
    echo "FEHLER: Repository nicht erreichbar (Internet oder Zugangsschlüssel prüfen)"; exit 1
  fi
  NEU=$(git -C "$SRC" rev-parse --short "origin/$BRANCH")
  if [[ "$ALT" == "$NEU" ]]; then echo "Bereits aktuell ($ALT)."; exit 0; fi
  git -C "$SRC" checkout --quiet --force "origin/$BRANCH" || { echo "FEHLER beim Auschecken"; exit 1; }
  # Tests vor der Installation; bei Fehler zurück auf die alte Version
  if ! (cd "$SRC" && python3 -m unittest discover -s tests -q); then
    echo "FEHLER: Tests fehlgeschlagen, zurück auf $ALT"
    git -C "$SRC" checkout --quiet --force "$ALT"; exit 1
  fi
  "$SRC/pi/install.sh" --update && echo "OK: $ALT → $NEU installiert."
} >> "$LOG" 2>&1
tail -c 200000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
