# Tests für das Portal

Prüft die Datenbankregeln (`portal/supabase/schema.sql`), den Ablauf Pi ↔ Portal und die Portal-Seite, ohne das echte Supabase-Projekt, Vercel oder einen echten Pi anzusprechen.

```bash
tests/portal/run.sh             # Datenbankregeln + Pi-Ablauf (etwa 20 Sekunden)
tests/portal/run.sh --browser   # zusätzlich die Portal-Seite im Browser, Handy-Format (etwa 1 Minute)
```

Ergebnis: am Schluss «N Prüfungen bestanden» und Exit-Code 0. Bei einem Fehler zeigt das Skript die Ausgabe des betroffenen Schritts und endet mit 1; fehlt eine Voraussetzung, mit 2.

## Voraussetzungen

- PostgreSQL mit der Erweiterung `pgcrypto` (Paket `postgresql-contrib`). Gesucht wird in `PGBIN`, dann über `pg_config`, dann unter `/usr/lib/postgresql/*/bin`. Geprüft mit Version 16.
- `python3` (≥ 3.11), `psql`.
- Nur für `--browser`: `node` und ein bereits installiertes Playwright samt Chromium. Das Skript installiert nichts; fehlt Playwright, bricht es ab, statt den Teil zu überspringen.
- Als root gestartet, läuft Postgres über `runuser` als Benutzer `postgres` (Postgres verweigert root).

Jeder Lauf legt einen frischen Wegwerf-Cluster unter `/tmp/smpg.XXXXXX` an (nur Unix-Socket, kein Netzwerk) und löscht ihn am Ende wieder.

## Was geprüft wird

| Schritt | Dateien | Inhalt |
|---|---|---|
| Neue Datenbank | `stub.sql`, `schema.sql` (zweimal), `hilfen.sql`, `test_stufe1.sql`, `test_stufe3.sql` | Zugriffsregeln der Stufen 1 und 3; `schema.sql` ist wiederholt ausführbar |
| Bestehende Datenbank | `schema_stufe1.sql`, `upgrade_vor.sql`, `schema.sql`, `upgrade_nach.sql`, `test_stufe3.sql` | Einspielen über den Stand der Stufe 1: Bestand bleibt, Fernwartung ist aus, alter Pi meldet weiter |
| Pi gegen die Attrappe | `fake_supabase.py`, `pi_ablauf.py` | echter Pi-Code: Befehle abholen, ausführen, quittieren, Konfiguration maskiert hochladen; neuer Pi an alter Datenbank |
| Browser (`--browser`) | `browser.js` | Zwei-Faktor einrichten und bestätigen, Konfiguration laden und senden, Update, Ablehnungen, Kunde und fremdes Konto, Abmelden; dazu Überlaufen, Bedienelemente unter 44 px, Skriptfehler, CSP-Verstösse, unerwartete Fehlantworten |

`schema_stufe1.sql` ist der eingefrorene Stand vor Stufe 3 und wird nicht geändert. Die SQL-Tests melden jede bestandene Prüfung als `ok - …`; die Hilfen dazu (`t.als`, `t.gleich`, `t.scheitert`) stehen in `hilfen.sql`.

## Was die Attrappe nachbildet und was nicht

`fake_supabase.py` steht vor der lokalen Datenbank. **Echt** sind dabei die Regeln aus `schema.sql` (Row Level Security, Spaltenrechte, Funktionen). **Nachgebildet** und am echten Projekt **ungeprüft** sind:

- `stub.sql`: Rollen `anon`, `authenticated`, `service_role`, `auth.users`, `auth.uid()`, `auth.jwt()` (liest `request.jwt.claims`) und die Supabase-Standardrechte (neue Tabellen und Funktionen in `public` sind zunächst für alle drei Rollen offen; erst damit prüfen die Tests, ob die `revoke`-Zeilen wirken).
- Anmeldung: kein echter Supabase-Login. Die Tokens haben die Form eines JWT (`kopf.nutzlast.x`), sind aber nicht signiert.
- Zwei-Faktor in der Form der Supabase-Auth-API: `GET /auth/v1/user` (Feld `factors`), `POST /auth/v1/factors`, `POST /auth/v1/factors/{id}/challenge`, `POST /auth/v1/factors/{id}/verify`, `DELETE /auth/v1/factors/{id}`. Keine echten TOTP-Codes: Der Code ist immer `424242`. Eingerichtete Faktoren liegen nur im Speicher der Attrappe.
- Mini-PostgREST: `select`, Filter `eq`/`gte`, `order`, `limit` und Funktionsaufrufe. Die Zuordnung von Datenbankfehlern zu HTTP-Status folgt der PostgREST-Dokumentation, ist aber kein PostgREST.
- Die Seite wird mit den Kopfzeilen aus `portal/web/vercel.json` ausgeliefert (CSP), nur `connect-src` ist um die eigene Adresse ergänzt. Vercel selbst ist nicht nachgebildet.

Nicht geprüft werden kann hier: das echte Supabase-Projekt, echte TOTP-Codes, Vercel, ein echter Pi mit systemd und `update.sh`.

## Testkonten (nur in der Attrappe)

| Konto | Rolle | Passwort |
|---|---|---|
| `chef@solarmaa.ch` | Solarmaa (wird von den Tests in `admins` eingetragen) | `test-passwort-1` |
| `kunde@example.ch` | Kunde | `test-passwort-1` |
| `fremd@example.ch` | Konto ohne Anlage | `test-passwort-1` |

Zwei-Faktor-Code: `424242`. Das Passwort und der Code gelten nur in `fake_supabase.py` und sind kein Geheimnis.

## Von Hand (für den Tester)

`run.sh` räumt am Ende alles weg. Für Prüfungen im eigenen Browser Datenbank und Attrappe selbst starten (als normaler Benutzer; als root die drei Postgres-Befehle mit `runuser -u postgres --` und einem Ordner, der `postgres` gehört):

```bash
PGBIN=/usr/lib/postgresql/16/bin; T=$(mktemp -d /tmp/smpg.XXXXXX)
$PGBIN/initdb -D $T/db --auth=trust -U postgres >/dev/null
$PGBIN/pg_ctl -D $T/db -w -o "-c listen_addresses='' -c unix_socket_directories=$T -c port=54329" start
export PGHOST=$T PGPORT=54329 PGUSER=postgres PGDATABASE=hand
createdb hand
for f in tests/portal/stub.sql portal/supabase/schema.sql tests/portal/hilfen.sql; do psql -q -v ON_ERROR_STOP=1 -f $f; done
psql -c "insert into public.admins values ('00000000-0000-0000-0000-000000000001')"
python3 tests/portal/fake_supabase.py portal/web 8090 &      # Portal: http://127.0.0.1:8090/
```

Anlage im Portal anlegen, den angezeigten Token merken. Der Pi lässt sich auf zwei Arten spielen:

```bash
# a) eine Meldung samt Befehlen pro Aufruf; jeder Aufruf wirkt wie ein Neustart des Dienstes
python3 tests/portal/pi_ablauf.py http://127.0.0.1:8090 --runde /tmp/pi-hand TOKEN

# b) der echte Dienst im Demo-Modus (meldet alle 30 bis 60 Sekunden; ein Neustart nach «konfig»
#    beendet den Prozess, danach von Hand neu starten, wie es sonst systemd tut)
cd pi && python3 -m solarmaa_dash run -c /tmp/pi-hand/config.toml --db /tmp/pi-hand/demo.db --port 8080
```

Zeit zurückstellen (Verfall prüfen): `psql -c "update public.befehle set erstellt = now() - interval '2 hours' where status = 'offen'"`.

Aufräumen: Attrappe beenden, `$PGBIN/pg_ctl -D $T/db -m immediate stop`, `rm -rf $T /tmp/pi-hand`.
