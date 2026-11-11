#!/usr/bin/env bash
# Solarmaa Dashboard: Update aus dem Git-Repository (wird von systemd als root gestartet).
# Manuell: sudo /usr/local/sbin/solarmaa-update
set -uo pipefail
SRC=/opt/solarmaa-dash/src
LOGDIR=/var/log/solarmaa-dash   # gehört root: der Webdienst kann hier nichts unterschieben
LOG=$LOGDIR/update.log
ZUSTAND=/var/lib/solarmaa-dash/abregelung-zustand.json
install -d -m 755 -o root -g root "$LOGDIR"
BRANCH=$(cat /etc/solarmaa-dash/update-branch 2>/dev/null || echo main)
{
  echo "=== Update $(date '+%d.%m.%Y %H:%M:%S'), Branch $BRANCH ==="
  rm -f /var/lib/solarmaa-dash/update-anfrage
  if grep -q '"begrenzt": true' "$ZUSTAND" 2>/dev/null; then
    echo "Verschoben: Anlage wird gerade abgeregelt. Update nach dem negativen Preisfenster erneut starten."; exit 0
  fi
  if [[ ! -d "$SRC/.git" ]]; then echo "FEHLER: $SRC ist kein Git-Repository"; exit 1; fi
  ALT=$(git -C "$SRC" rev-parse --short HEAD)
  if ! git -C "$SRC" fetch --quiet origin "$BRANCH"; then
    echo "FEHLER: Repository nicht erreichbar (Internet oder Zugangsschlüssel prüfen)"; exit 1
  fi
  NEU=$(git -C "$SRC" rev-parse --short "origin/$BRANCH")
  if [[ "$ALT" == "$NEU" ]]; then echo "Bereits aktuell ($ALT)."; exit 0; fi
  git -C "$SRC" checkout --quiet --force "origin/$BRANCH" || { echo "FEHLER beim Auschecken"; exit 1; }
  # Tests vor der Installation; bei Fehler zurück auf die alte Version
  # Tests laufen ohne Root-Rechte (Dienstbenutzer)
  if ! (cd "$SRC" && runuser -u solarmaa-dash -- env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q); then
    echo "FEHLER: Tests fehlgeschlagen, zurück auf $ALT"
    git -C "$SRC" checkout --quiet --force "$ALT"; exit 1
  fi
  if "$SRC/pi/install.sh" --update; then
    echo "OK: $ALT → $NEU installiert."
  else
    echo "FEHLER bei der Installation, zurück auf $ALT"
    git -C "$SRC" checkout --quiet --force "$ALT" && "$SRC/pi/install.sh" --update && echo "Alte Version $ALT wiederhergestellt."
    exit 1
  fi
} >> "$LOG" 2>&1
TMP=$(mktemp "$LOGDIR/update.XXXXXX") && tail -c 200000 "$LOG" > "$TMP" && chmod 644 "$TMP" && mv "$TMP" "$LOG"
