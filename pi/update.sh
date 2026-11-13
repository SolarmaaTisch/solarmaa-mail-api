#!/usr/bin/env bash
# Solarmaa Dashboard: Update aus dem Git-Repository (wird von systemd als root gestartet).
# Manuell: sudo /usr/local/sbin/solarmaa-update
set -uo pipefail
SRC=/opt/solarmaa-dash/src
LOGDIR=/var/log/solarmaa-dash   # gehört root: der Webdienst kann hier nichts unterschieben
LOG=$LOGDIR/update.log
install -d -m 755 -o root -g root "$LOGDIR"
BRANCH=$(cat /etc/solarmaa-dash/update-branch 2>/dev/null || echo main)
{
  echo "=== Update $(date '+%d.%m.%Y %H:%M:%S'), Branch $BRANCH ==="
  rm -f /var/lib/solarmaa-dash/update-anfrage
  # Während einer laufenden Abregelung nicht neu starten. Massgebend ist der laufende Dienst
  # (nicht eine Datei, die veraltet sein kann). Mit --erzwingen wird trotzdem aktualisiert.
  if [[ "${1:-}" != "--erzwingen" ]] && python3 - <<'PY'
import json, sys, tomllib, urllib.request
try:
    port = tomllib.load(open("/etc/solarmaa-dash/config.toml", "rb")).get("server", {}).get("port", 80)
    d = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/now", timeout=5))
    a = d.get("abregelung") or {}
    sys.exit(0 if a.get("aktiv") and a.get("modus") in ("auto", "test") else 1)
except Exception:
    sys.exit(1)      # Dienst nicht erreichbar: Update darf laufen
PY
  then
    echo "Verschoben: Anlage wird gerade abgeregelt. Später erneut starten (oder: sudo solarmaa-update --erzwingen)."; exit 0
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
