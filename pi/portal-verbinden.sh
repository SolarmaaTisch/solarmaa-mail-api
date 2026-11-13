#!/usr/bin/env bash
# Verbindet diesen Pi mit dem Solarmaa-Portal: holt das neueste Programm, trägt den Portal-Block
# in die Konfiguration ein (samt fernwartung = true) und startet den Dienst neu.
#
#   curl -fsSL https://raw.githubusercontent.com/SolarmaaTisch/solarmaa-mail-api/claude/vibrant-newton-yc8ppy/pi/portal-verbinden.sh | sudo bash -s -- TOKEN
set -euo pipefail
TOKEN="${1:-}"
URL="${SOLARMAA_PORTAL_URL:-https://uydqpixvoaznaalhygaw.supabase.co}"
KEY="${SOLARMAA_PORTAL_KEY:-sb_publishable_VDqT93u-o0W7fiRTgQ5DEA_-Kgq9aMf}"
CONF=/etc/solarmaa-dash/config.toml

[[ $EUID -eq 0 ]] || { echo "Bitte mit sudo ausführen."; exit 1; }
[[ "$TOKEN" =~ ^[0-9a-f]{40,64}$ ]] || { echo "Token fehlt oder hat ein falsches Format."; exit 1; }
[[ -f "$CONF" ]] || { echo "Programm ist noch nicht installiert (zuerst schnellstart.sh)."; exit 1; }

echo "==> Neueste Programmversion holen"
# Direkt aus dem Repository aktualisieren (unabhängig von einer älteren Fassung von solarmaa-update).
SRC=/opt/solarmaa-dash/src
BRANCH=$(cat /etc/solarmaa-dash/update-branch 2>/dev/null || echo claude/vibrant-newton-yc8ppy)
Z=/var/lib/solarmaa-dash/abregelung-zustand.json
if [[ -f "$Z" ]]; then echo "    Hinweis: Zustandsdatei vorhanden: $(cat "$Z")"; fi
if grep -q "abregelung.wechselrichter" "$CONF"; then
  echo "    Abregelung ist konfiguriert: Update wird übersprungen, falls gerade abgeregelt wird."
  /usr/local/sbin/solarmaa-update || true
else
  rm -f "$Z"       # ohne konfigurierte Wechselrichter kann nichts begrenzt sein
  if [[ -d "$SRC/.git" ]]; then
    ALT=$(git -C "$SRC" rev-parse --short HEAD)
    git -C "$SRC" fetch --quiet origin "$BRANCH"
    git -C "$SRC" checkout --quiet --force "origin/$BRANCH"
    if "$SRC/pi/install.sh" --update >/dev/null; then
      echo "    Aktualisiert: $ALT → $(git -C "$SRC" rev-parse --short HEAD)"
    else
      echo "    Installation fehlgeschlagen, zurück auf $ALT"; git -C "$SRC" checkout --quiet --force "$ALT"; "$SRC/pi/install.sh" --update >/dev/null
    fi
  else
    echo "    Kein Quellcode unter $SRC gefunden. Bitte zuerst schnellstart.sh ausführen."; exit 1
  fi
fi

echo "==> Portal in die Konfiguration eintragen"
cp -p "$CONF" "$CONF.vor-portal"
python3 - "$CONF" "$URL" "$KEY" "$TOKEN" <<'PY'
import re, sys
pfad, url, key, token = sys.argv[1:5]
text = open(pfad, encoding="utf-8").read()
# vorhandenen [portal]-Abschnitt entfernen (bis zur nächsten Tabelle oder zum Dateiende)
text = re.sub(r"(?ms)^\[portal\][^\n]*\n.*?(?=^\[|\Z)", "", text).rstrip("\n")
# fernwartung = true: Solarmaa darf diese Anlage über das Portal aktualisieren und konfigurieren (E7)
text += f'\n\n[portal]\nurl = "{url}"\nschluessel = "{key}"\ntoken = "{token}"\nfernwartung = true\n'
open(pfad, "w", encoding="utf-8").write(text)
PY
chown root:solarmaa-dash "$CONF"; chmod 660 "$CONF"

echo "==> Dienst neu starten"
systemctl restart solarmaa-dash
sleep 8
if journalctl -u solarmaa-dash -n 30 --no-pager | grep -q "Portal-Verbindung aktiv"; then
  echo "Fertig. Der Pi meldet sich jetzt beim Portal. In etwa einer Minute erscheint er dort als «online»."
else
  echo "Dienst läuft, aber die Portal-Verbindung wurde nicht bestätigt. Letzte Meldungen:"
  journalctl -u solarmaa-dash -n 12 --no-pager
fi
