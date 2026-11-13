#!/usr/bin/env bash
# Prüft die Datenbankregeln des Portals und den Ablauf Pi <-> Portal gegen eine lokale
# Wegwerf-Datenbank und eine Supabase-Attrappe. Spricht nie das echte Supabase-Projekt an.
#
#   tests/portal/run.sh             Datenbankregeln + Pi-Ablauf
#   tests/portal/run.sh --browser   zusätzlich die Portal-Seite im Browser (braucht Playwright)
#
# Exit 0 = alles bestanden, 1 = Prüfung fehlgeschlagen, 2 = Voraussetzung fehlt. Siehe README.md.
set -euo pipefail
HIER=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HIER/../.." && pwd)
SCHEMA="$REPO/portal/supabase/schema.sql"
BROWSER=0
case "${1:-}" in
  "") ;;
  --browser) BROWSER=1 ;;
  *) echo "Aufruf: $0 [--browser]"; exit 2 ;;
esac

# ---------------------------------------------------------------- Postgres finden
PGBIN="${PGBIN:-}"
if [[ -z "$PGBIN" ]] && command -v pg_config >/dev/null 2>&1; then PGBIN=$(pg_config --bindir); fi
if [[ -z "$PGBIN" || ! -x "$PGBIN/initdb" ]]; then
  PGBIN=""
  for d in /usr/lib/postgresql/*/bin; do [[ -x "$d/initdb" ]] && PGBIN="$d"; done
fi
if [[ -z "$PGBIN" || ! -x "$PGBIN/initdb" || ! -x "$PGBIN/pg_ctl" || ! -x "$PGBIN/psql" ]]; then
  echo "Postgres nicht gefunden (initdb, pg_ctl, psql). Installieren oder PGBIN=/pfad/zu/bin setzen."; exit 2
fi
command -v python3 >/dev/null 2>&1 || { echo "python3 nicht gefunden."; exit 2; }

ALS=()                      # Postgres läuft nie als root
if [[ $(id -u) -eq 0 ]]; then
  id postgres >/dev/null 2>&1 || { echo "Als root gestartet, aber es gibt keinen Benutzer «postgres». Als normaler Benutzer starten."; exit 2; }
  command -v runuser >/dev/null 2>&1 || { echo "runuser nicht gefunden."; exit 2; }
  ALS=(runuser -u postgres --)
fi

TMP=$(mktemp -d /tmp/smpg.XXXXXX)     # kurz halten: Der Socket-Pfad darf höchstens 107 Bytes lang sein
LOG="$TMP/ausgabe.log"
ATTRAPPEN=()
aufraeumen() {
  local p
  for p in "${ATTRAPPEN[@]:-}"; do [[ -n "$p" ]] && kill "$p" 2>/dev/null || true; done
  if [[ -f "$TMP/db/postmaster.pid" ]]; then
    "${ALS[@]}" "$PGBIN/pg_ctl" -D "$TMP/db" -m immediate -w stop >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP"
}
trap aufraeumen EXIT
mkdir "$TMP/db" "$TMP/sock"
chmod 755 "$TMP" "$TMP/sock"; chmod 700 "$TMP/db"
if [[ ${#ALS[@]} -gt 0 ]]; then chown postgres "$TMP/db" "$TMP/sock"; fi

frei_port() { python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
export PGHOST="$TMP/sock" PGPORT PGUSER=postgres
PGPORT=$(frei_port)
unset PGDATABASE PGPASSWORD PGSERVICE 2>/dev/null || true
export PATH="$PGBIN:$PATH"

echo "==> Wegwerf-Datenbank starten ($("$PGBIN/postgres" --version))"
if ! "${ALS[@]}" "$PGBIN/initdb" -D "$TMP/db" --auth=trust -U postgres -E UTF8 --locale=C >"$TMP/initdb.log" 2>&1; then
  cat "$TMP/initdb.log"; echo "initdb fehlgeschlagen."; exit 2
fi
if ! "${ALS[@]}" "$PGBIN/pg_ctl" -D "$TMP/db" -w -l "$TMP/db/postgres.log" \
     -o "-c listen_addresses='' -c unix_socket_directories='$TMP/sock' -c port=$PGPORT -c fsync=off" start >"$TMP/start.log" 2>&1; then
  cat "$TMP/start.log"; "${ALS[@]}" cat "$TMP/db/postgres.log" 2>/dev/null || true
  echo "Postgres liess sich nicht starten."; exit 2
fi
if [[ $(psql -X -qAt -d postgres -c "select count(*) from pg_available_extensions where name = 'pgcrypto'") != 1 ]]; then
  echo "Die Erweiterung pgcrypto fehlt (Paket postgresql-contrib installieren)."; exit 2
fi

# ---------------------------------------------------------------- Hilfen
PRUEFUNGEN=0
# schritt "Beschreibung" befehl …   – Ausgabe nur bei Fehler zeigen; «ok - »-Zeilen zählen
schritt() {
  local was="$1"; shift
  if ! "$@" >"$LOG" 2>&1; then
    cat "$LOG"; echo; echo "FEHLGESCHLAGEN: $was"; exit 1
  fi
  local n; n=$(grep -c "ok - " "$LOG" || true)
  PRUEFUNGEN=$((PRUEFUNGEN + n))
  printf '    %-58s %s\n' "$was" "$([[ $n -gt 0 ]] && echo "$n Prüfungen" || echo "ok")"
}
sqldatei() { psql -X -q -v ON_ERROR_STOP=1 -d "$1" -f "$2"; }
attrappe() {      # attrappe <datenbank>  -> setzt URL
  local port; port=$(frei_port)
  PGDATABASE="$1" python3 "$HIER/fake_supabase.py" "$REPO/portal/web" "$port" >"$TMP/attrappe-$1.log" 2>&1 &
  ATTRAPPEN+=($!)
  URL="http://127.0.0.1:$port"
  local i
  for i in $(seq 1 50); do
    if python3 -c "import sys, urllib.request; urllib.request.urlopen(sys.argv[1] + '/config.js', timeout=2)" "$URL" 2>/dev/null; then return 0; fi
    sleep 0.2
  done
  cat "$TMP/attrappe-$1.log"; echo "Attrappe startet nicht."; exit 1
}

# ---------------------------------------------------------------- 1. Neue Datenbank
echo "==> Neue Datenbank"
createdb neu
schritt "Supabase-Nachbildung (stub.sql)" sqldatei neu "$HIER/stub.sql"
schritt "schema.sql einspielen" sqldatei neu "$SCHEMA"
schritt "schema.sql ein zweites Mal (wiederholt ausführbar)" sqldatei neu "$SCHEMA"
schritt "Testhilfen" sqldatei neu "$HIER/hilfen.sql"
schritt "Stufe 1: Zugriffsregeln" sqldatei neu "$HIER/test_stufe1.sql"
schritt "Stufe 3: Fernwartung" sqldatei neu "$HIER/test_stufe3.sql"
schritt "schema.sql nach den Tests nochmals" sqldatei neu "$SCHEMA"

# ---------------------------------------------------------------- 2. Upgrade von Stufe 1
echo "==> Bestehende Datenbank (Stufe 1) hochziehen"
createdb upgrade
schritt "Stand Stufe 1 mit Bestand" bash -c "$(declare -f sqldatei); sqldatei upgrade '$HIER/stub.sql' && sqldatei upgrade '$HIER/schema_stufe1.sql' && sqldatei upgrade '$HIER/upgrade_vor.sql'"
schritt "schema.sql darüber einspielen" sqldatei upgrade "$SCHEMA"
schritt "Testhilfen" sqldatei upgrade "$HIER/hilfen.sql"
schritt "Bestand erhalten, Fernwartung aus" sqldatei upgrade "$HIER/upgrade_nach.sql"
schritt "Stufe 3: Fernwartung" sqldatei upgrade "$HIER/test_stufe3.sql"

# ---------------------------------------------------------------- 3. Pi gegen neue und alte Datenbank
if [[ -f "$HIER/pi_ablauf.py" ]]; then
  echo "==> Pi gegen die Attrappe"
  createdb alt
  schritt "Datenbank auf Stand Stufe 1" bash -c "$(declare -f sqldatei); sqldatei alt '$HIER/stub.sql' && sqldatei alt '$HIER/schema_stufe1.sql'"
  attrappe neu
  schritt "Pi-Ablauf: Befehle, Quittungen, Konfiguration" env PGDATABASE=neu python3 "$HIER/pi_ablauf.py" "$URL"
  attrappe alt
  schritt "Neuer Pi an alter Datenbank" env PGDATABASE=alt python3 "$HIER/pi_ablauf.py" "$URL" --alt
fi

# ---------------------------------------------------------------- 4. Portal-Seite im Browser
if [[ $BROWSER -eq 1 ]]; then
  echo "==> Portal-Seite im Browser (Handy-Format)"
  command -v node >/dev/null 2>&1 || { echo "node nicht gefunden."; exit 2; }
  NODE_PATH="${NODE_PATH:-$(npm root -g 2>/dev/null || true)}"; export NODE_PATH
  node -e "require('playwright')" 2>/dev/null || { echo "Playwright nicht gefunden (wird hier nicht installiert). Ohne --browser starten oder Playwright bereitstellen."; exit 2; }
  createdb browser
  schritt "Datenbank für den Browsertest" bash -c "$(declare -f sqldatei); sqldatei browser '$HIER/stub.sql' && sqldatei browser '$SCHEMA' && sqldatei browser '$HIER/hilfen.sql'"
  attrappe browser
  schritt "Abläufe im Portal" env PGDATABASE=browser node "$HIER/browser.js" "$URL" "$REPO"
fi

echo
echo "$PRUEFUNGEN Prüfungen bestanden."
