#!/usr/bin/env bash
# Solarmaa Energie-Dashboard: Installation auf Raspberry Pi OS Lite (Bookworm oder neuer)
#
#   sudo ./pi/install.sh                    installieren oder aktualisieren
#   sudo ./pi/install.sh --hostname solarmaa-muster
#   sudo ./pi/install.sh --auto-update         zusätzlich nächtliche Update-Prüfung
#
# Der Quellcode liegt danach als Git-Repository in /opt/solarmaa-dash/src. Updates über die
# Weboberfläche (Wartung) oder mit «sudo solarmaa-update» holen den neuesten Stand von dort.
#
# Die Konfiguration /etc/solarmaa-dash/config.toml wird nur beim ersten Mal angelegt
# und bei Updates nie überschrieben.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET=/opt/solarmaa-dash
CONF_DIR=/etc/solarmaa-dash
USER_NAME=solarmaa-dash
NEW_HOSTNAME=""
AUTO_UPDATE=0
IS_UPDATE=0
INTER_URL="https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hostname) NEW_HOSTNAME="$2"; shift 2 ;;
    --auto-update) AUTO_UPDATE=1; shift ;;
    --update) IS_UPDATE=1; shift ;;
    *) echo "Unbekannte Option: $1" >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }

PYV=$(python3 -c 'import sys; print(sys.version_info >= (3, 11))')
[[ "$PYV" == "True" ]] || { echo "Python 3.11 oder neuer nötig (Raspberry Pi OS Bookworm)." >&2; exit 1; }

if [[ $IS_UPDATE -eq 0 ]]; then
  echo "==> Pakete"
  apt-get update -qq
  apt-get install -y -qq avahi-daemon unzip curl git >/dev/null
fi

echo "==> Benutzer und Verzeichnisse"
id -u "$USER_NAME" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
install -d -m 755 "$TARGET" "$CONF_DIR"
install -d -m 750 -o "$USER_NAME" -g "$USER_NAME" /var/lib/solarmaa-dash

if [[ "$REPO_DIR" != "$TARGET/src" ]]; then
  echo "==> Quellcode nach $TARGET/src (für Web-Updates)"
  if [[ -d "$REPO_DIR/.git" ]]; then
    rm -rf "$TARGET/src.new"
    git clone --quiet "$REPO_DIR" "$TARGET/src.new"
    ORIGIN=$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)
    [[ -n "$ORIGIN" ]] && git -C "$TARGET/src.new" remote set-url origin "$ORIGIN"
    BR=$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)
    [[ -f "$CONF_DIR/update-branch" ]] || echo "$BR" > "$CONF_DIR/update-branch"
    rm -rf "$TARGET/src"; mv "$TARGET/src.new" "$TARGET/src"
  else
    echo "    Kein Git-Repository: Web-Updates sind erst nach einer Installation aus Git möglich."
  fi
fi

echo "==> Programm kopieren nach $TARGET"
rm -rf "$TARGET/pi" "$TARGET/app.new"
cp -r "$REPO_DIR/pi" "$TARGET/pi"
cp -r "$REPO_DIR/app" "$TARGET/app.new"
if [[ -f "$TARGET/app/fonts/InterVariable.woff2" ]]; then
  mkdir -p "$TARGET/app.new/fonts"
  cp "$TARGET/app/fonts/InterVariable.woff2" "$TARGET/app.new/fonts/"
fi
rm -rf "$TARGET/app"; mv "$TARGET/app.new" "$TARGET/app"
find "$TARGET" -name '__pycache__' -prune -exec rm -rf {} +

if [[ ! -f "$TARGET/app/fonts/InterVariable.woff2" ]]; then
  echo "==> Schrift Inter laden (optional)"
  TMP=$(mktemp -d)
  if curl -fsSL "$INTER_URL" -o "$TMP/inter.zip" && unzip -q -j "$TMP/inter.zip" 'web/InterVariable.woff2' -d "$TMP"; then
    mkdir -p "$TARGET/app/fonts"; cp "$TMP/InterVariable.woff2" "$TARGET/app/fonts/"
  else
    echo "    Kein Internet oder Datei nicht gefunden: App nutzt die Systemschrift."
  fi
  rm -rf "$TMP"
fi

if [[ ! -f "$CONF_DIR/config.toml" ]]; then
  echo "==> Beispiel-Konfiguration anlegen ($CONF_DIR/config.toml)"
  install -m 640 -g "$USER_NAME" "$REPO_DIR/pi/config.example.toml" "$CONF_DIR/config.toml"
fi
# Konfiguration kann Passwörter/API-Keys enthalten: nur root und der Dienst dürfen lesen
chown root:"$USER_NAME" "$CONF_DIR/config.toml"; chmod 640 "$CONF_DIR/config.toml"

if [[ -n "$NEW_HOSTNAME" ]]; then
  echo "==> Hostname: $NEW_HOSTNAME (erreichbar als http://$NEW_HOSTNAME.local)"
  hostnamectl set-hostname "$NEW_HOSTNAME"
  sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$NEW_HOSTNAME/" /etc/hosts
fi

echo "==> Robustheit: Hardware-Watchdog und Log-Grösse begrenzen"
mkdir -p /etc/systemd/system.conf.d /etc/systemd/journald.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15s\n' > /etc/systemd/system.conf.d/solarmaa-watchdog.conf
printf '[Journal]\nSystemMaxUse=50M\n' > /etc/systemd/journald.conf.d/solarmaa.conf

echo "==> Dienst und Web-Update einrichten"
install -m 644 "$REPO_DIR/pi/systemd/solarmaa-dash.service" /etc/systemd/system/solarmaa-dash.service
install -m 755 "$REPO_DIR/pi/update.sh" /usr/local/sbin/solarmaa-update
for u in solarmaa-dash-update.path solarmaa-dash-update.service solarmaa-dash-update.timer; do
  install -m 644 "$REPO_DIR/pi/systemd/$u" "/etc/systemd/system/$u"
done
systemctl daemon-reload
systemctl enable --now solarmaa-dash-update.path >/dev/null
[[ $AUTO_UPDATE -eq 1 ]] && systemctl enable --now solarmaa-dash-update.timer >/dev/null
systemctl enable solarmaa-dash >/dev/null
systemctl restart solarmaa-dash
sleep 3
systemctl --no-pager --lines=5 status solarmaa-dash || true

IP=$(hostname -I | awk '{print $1}')
echo
echo "Fertig. Dashboard: http://$(hostname).local  oder  http://$IP"
echo "Konfiguration:  sudo nano $CONF_DIR/config.toml  danach  sudo systemctl restart solarmaa-dash"
