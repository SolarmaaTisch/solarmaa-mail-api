#!/usr/bin/env bash
# Verbindet diesen Pi mit dem Solarmaa-Portal: holt das neueste Programm, trägt den Portal-Block
# in die Konfiguration ein und startet den Dienst neu.
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
if [[ -x /usr/local/sbin/solarmaa-update ]]; then
  /usr/local/sbin/solarmaa-update || true
  tail -n 2 /var/log/solarmaa-dash/update.log 2>/dev/null || tail -n 2 /var/lib/solarmaa-dash/update.log 2>/dev/null || true
fi

echo "==> Portal in die Konfiguration eintragen"
cp -p "$CONF" "$CONF.vor-portal"
python3 - "$CONF" "$URL" "$KEY" "$TOKEN" <<'PY'
import re, sys
pfad, url, key, token = sys.argv[1:5]
text = open(pfad, encoding="utf-8").read()
# vorhandenen [portal]-Abschnitt entfernen (bis zur nächsten Tabelle oder zum Dateiende)
text = re.sub(r"(?ms)^\[portal\][^\n]*\n.*?(?=^\[|\Z)", "", text).rstrip("\n")
text += f'\n\n[portal]\nurl = "{url}"\nschluessel = "{key}"\ntoken = "{token}"\n'
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
