# Tests für das Portal

Prüft die Datenbankregeln (`portal/supabase/schema.sql`), den Ablauf Pi ↔ Portal und die Portal-Seite, ohne das echte Supabase-Projekt, Vercel oder einen echten Pi anzusprechen.

```bash
tests/portal/run.sh             # Datenbankregeln + Pi-Ablauf (etwa 20 Sekunden)
tests/portal/run.sh --browser   # zusätzlich die Portal-Seite im Browser, Handy-Format (etwa 1 Minute)
tests/portal/run.sh --hand      # keine Tests: Datenbank und Attrappe für Prüfungen von Hand starten
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
| Neue Datenbank | `stub.sql`, `schema.sql` (zweimal), `hilfen.sql`, `test_stufe1.sql`, `test_stufe3.sql`, `test_dokumente.sql`, `dokumente_nach.sql` | Zugriffsregeln der Stufen 1 und 3 und der Anlagedoku; `schema.sql` ist wiederholt ausführbar und setzt einen verstellten Bucket zurück |
| Bestehende Datenbank | `schema_stufe1.sql`, `upgrade_vor.sql`, `schema.sql`, `upgrade_nach.sql`, `test_stufe3.sql`, `test_dokumente.sql` | Einspielen über den Stand der Stufe 1: Bestand bleibt, Fernwartung ist aus, alter Pi meldet weiter |
| Eingespielte Datenbank | `schema_stufe3.sql`, `upgrade_vor.sql`, `schema.sql` (zweimal), `upgrade_nach.sql`, `dokumente_nach.sql`, `test_stufe3.sql`, `test_dokumente.sql` | Einspielen über den Stand, der im Supabase-Projekt liegt (Stufe 3): Bestand bleibt, Anlagedoku kommt dazu |
| Pi gegen die Attrappe | `fake_supabase.py`, `pi_ablauf.py` | echter Pi-Code: Befehle abholen, ausführen, quittieren, Konfiguration maskiert hochladen; neuer Pi an alter Datenbank |
| Browser (`--browser`) | `browser.js` | Zwei-Faktor einrichten und bestätigen, Konfiguration laden und senden, Update, Anlagedoku (hochladen, Vorschau, herunterladen, Sichtbarkeit, löschen, Ablehnungen), Kunde und fremdes Konto, verspätete Antworten, Abmelden; dazu Überlaufen, Bedienelemente unter 44 px, Skriptfehler, CSP-Verstösse, unerwartete Fehlantworten |

`schema_stufe1.sql` ist der eingefrorene Stand vor Stufe 3, `schema_stufe3.sql` der im Supabase-Projekt eingespielte Stand (Commit 5ad3238); beide werden nicht geändert. Die SQL-Tests melden jede bestandene Prüfung als `ok - …`; die Hilfen dazu (`t.als`, `t.gleich`, `t.scheitert`) stehen in `hilfen.sql`.

## Was die Attrappe nachbildet und was nicht

`fake_supabase.py` steht vor der lokalen Datenbank. **Echt** sind dabei die Regeln aus `schema.sql` (Row Level Security, Spaltenrechte, Funktionen). **Nachgebildet** und am echten Projekt **ungeprüft** sind:

- `stub.sql`: Rollen `anon`, `authenticated`, `service_role`, `auth.users`, `auth.uid()`, `auth.jwt()` (liest `request.jwt.claims`) und die Supabase-Standardrechte (neue Tabellen und Funktionen in `public` sind zunächst für alle drei Rollen offen; erst damit prüfen die Tests, ob die `revoke`-Zeilen wirken).
- Anmeldung: kein echter Supabase-Login. Die Tokens haben die Form eines JWT (`kopf.nutzlast.x`), sind aber nicht signiert.
- Zwei-Faktor in der Form der Supabase-Auth-API: `GET /auth/v1/user` (Feld `factors`), `POST /auth/v1/factors`, `POST /auth/v1/factors/{id}/challenge`, `POST /auth/v1/factors/{id}/verify`, `DELETE /auth/v1/factors/{id}`. Keine echten TOTP-Codes: Der Code ist immer `424242`. Eingerichtete Faktoren liegen nur im Speicher der Attrappe.
- Mini-PostgREST: `select`, Filter `eq`/`gte`, `order`, `limit` und Funktionsaufrufe. Die Zuordnung von Datenbankfehlern zu HTTP-Status folgt der PostgREST-Dokumentation, ist aber kein PostgREST.
- Storage: `stub.sql` legt das Schema `storage` an (`buckets`, `objects` mit den genutzten Spalten wie am Projekt nachgesehen, RLS an, `foldername`/`filename`/`extension`, alle Rechte für die drei Rollen wie bei Supabase). Die Attrappe beantwortet `POST /storage/v1/object/<bucket>/<pfad>` (Rohdaten; Grenze und Typen des Buckets; Eintrag in `storage.objects` mit der Rolle des Kontos, `metadata` mit `size` und `mimetype`), `GET /storage/v1/object/authenticated/<bucket>/<pfad>` und `…/object/<bucket>/<pfad>` (Lesen mit der Rolle des Kontos), `DELETE /storage/v1/object/<bucket>` mit `{"prefixes": […]}` (Liste der gelöschten, auch leer) und weist `…/object/public/…` ab. Fehler als HTTP 400 mit `statusCode` im Text. Die Dateien liegen im Temp-Ordner des Laufs. Pfade, Statuscodes und Antwortformen folgen der Supabase-Dokumentation und sind am echten Projekt **ungeprüft**.
- Die Seite wird mit den Kopfzeilen aus `portal/web/vercel.json` ausgeliefert (CSP), nur `connect-src` ist um die eigene Adresse ergänzt. Vercel selbst ist nicht nachgebildet.

Nicht geprüft werden kann hier: das echte Supabase-Projekt samt Storage, echte TOTP-Codes, Vercel, ein echter Pi mit systemd und `update.sh`.

## Testkonten (nur in der Attrappe)

| Konto | Rolle | Passwort |
|---|---|---|
| `chef@solarmaa.ch` | Solarmaa (wird von den Tests in `admins` eingetragen) | `test-passwort-1` |
| `kunde@example.ch` | Kunde | `test-passwort-1` |
| `fremd@example.ch` | Konto ohne Anlage | `test-passwort-1` |

Zwei-Faktor-Code: `424242`. Das Passwort und der Code gelten nur in `fake_supabase.py` und sind kein Geheimnis.

## Von Hand (für den Tester)

```bash
tests/portal/run.sh --hand
```

startet dieselbe Wegwerf-Datenbank (UTF-8) mit `schema.sql` und die Attrappe, trägt `chef@solarmaa.ch` als Solarmaa-Konto ein und bleibt stehen, bis Strg+C gedrückt wird. Das Skript zeigt die Adresse des Portals, den `psql`-Aufruf und den Befehl für den Pi an.

Im Portal eine Anlage anlegen und den angezeigten Token merken. Der Pi lässt sich auf zwei Arten spielen:

```bash
# a) eine Meldung samt Befehlen pro Aufruf; jeder Aufruf wirkt wie ein Neustart des Dienstes
python3 tests/portal/pi_ablauf.py <Adresse des Portals> --runde /tmp/pi-hand TOKEN

# b) der echte Dienst im Demo-Modus mit der so angelegten Konfiguration (meldet alle 30 bis 60 Sekunden;
#    nach «konfig» beendet er sich, danach von Hand neu starten, wie es sonst systemd tut)
cd pi && python3 -m solarmaa_dash run -c /tmp/pi-hand/config.toml --db /tmp/pi-hand/demo.db --port 8080
```

Zeit zurückstellen (Verfall prüfen), mit dem angezeigten `psql`-Aufruf: `update public.befehle set erstellt = now() - interval '2 hours' where status = 'offen'`.

Wer den Cluster selbst anlegt: `initdb` unbedingt mit `-E UTF8 --locale=C` aufrufen. Ohne das entsteht je nach Umgebung eine Datenbank in SQL_ASCII, und «Konfiguration senden» scheitert mit «unsupported Unicode escape sequence».
