# Solarmaa Energie-Dashboard und Portal

## Projekt
Anlageüberwachung der Solarmaa GmbH. Drei Teile in diesem Repository:

| Teil | Ordner | Technik |
|---|---|---|
| Pi-Dienst beim Kunden | `pi/solarmaa_dash/` | Python ≥ 3.11, **nur Standardbibliothek**, systemd; liest Geräte über Modbus TCP und lokale HTTP-APIs (kein RS485), SQLite |
| App auf dem Pi | `app/` | HTML/CSS/JS ohne Build und ohne Fremdbibliotheken |
| Portal im Internet | `portal/web/` (Vercel), `portal/supabase/schema.sql` (Supabase) | statische Seite, spricht direkt mit Supabase Auth + REST; Datenbankstruktur in **einer** wiederholt ausführbaren SQL-Datei |

Der Pi kann Wechselrichter **steuern** (Abregelung bei negativen Preisen, `abregelung.py`) und über das Portal Befehle erhalten (Fernwartung). Fehler haben direkte Folgen: eine gedrosselte 240-kW-Anlage, falsche Daten beim Kunden, verpasste Störungen, fremder Zugriff auf Anlagen im Hausnetz.

Doku: `docs/` (01 bis 13). Bewusste Entscheide und bekannte Lücken: `docs/ENTSCHEIDE.md` – vor jeder Planung und Prüfung lesen.

## Prüfen
- Alle Tests: `python3 -m unittest discover -s tests` (muss vor jedem Commit grün sein)
- Datenbankregeln: `tests/portal/run.sh` (lokale Postgres-Instanz mit Supabase-Attrappe)
- Syntax App/Portal: `node --check app/*.js portal/web/*.js`
- Demo starten: `cd pi && python3 -m solarmaa_dash run -c config.haus-demo.toml --db /tmp/demo.db --port 8080`
- Echte Geräte, das echte Supabase-Projekt und Kundenanlagen sind **nie** Testziel. Was nur simuliert geprüft wurde, wird im Bericht so benannt.

## Branches
- Entwicklung auf Feature-Branches bzw. dem Entwicklungs-Branch.
- **`stabil`** ist die Quelle für Kunden-Pis (`/etc/solarmaa-dash/update-branch`) und für das veröffentlichte Portal. Ein Push auf `stabil` ist eine Auslieferung an echte Anlagen.
- Nie direkt auf `stabil` arbeiten. Auf `stabil` kommt nur ein geprüfter Stand, und nur mit Tins ausdrücklichem OK.
- Änderungen an `portal/supabase/schema.sql` werden erst nach Freigabe im Supabase-Projekt eingespielt, und vor dem zugehörigen Portal-Code.

## Arbeitsweise mit dem Agenten-Team
Für neue Funktionen und für Änderungen an Steuerung, Rechten, Daten, Berechnungen, Schnittstellen oder Update-Weg arbeitest du mit dem Team in `.claude/agents/`:

1. **planer**: erstellt den Plan. Offene Fachfragen gehen an Tin, bevor gebaut wird.
2. **entwickler**: setzt den Plan um, mit Tests.
3. **tester**: führt die Tests aus und prüft die Abläufe im Browser gegen Demo und Attrappen.
4. **pruefer**: bekommt nur den Auftrag und die Änderungen (`git diff`), **nicht** die Überlegungen von Planer oder Entwickler.
5. Befunde «kritisch» oder «hoch» gehen zurück an den entwickler, danach prüfen tester und pruefer erneut. Nach drei Runden ohne Lösung: an Tin melden.

Kleine Text- oder Stiländerungen brauchen das Team nicht. Änderungen an `abregelung.py`, `modbus.py` (Schreiben), `update.sh`, `install.sh`, `auth.py`, `schema.sql` brauchen es **immer**.

## Feste Regeln
- Keine neuen Abhängigkeiten: Pi bleibt bei der Python-Standardbibliothek, App und Portal ohne Fremdbibliotheken. Ausnahmen nur mit Tins OK, dann mit Name, Herausgeber und Verbreitung.
- Keine Skills, MCP-Server oder Skripte aus fremden Repositories installieren oder ausführen.
- Secrets nie in Code, Commits, Logs, Tests oder Antworten. Der öffentliche Supabase-Schlüssel (`sb_publishable_…`) ist kein Secret; alles andere schon.
- Zahlen, Register, Feldnamen und Herstellerangaben nie erfinden. Quelle nennen oder als ungeprüft kennzeichnen (`geprueft = false` in Profilen).
- Texte für Kunden und Doku auf Deutsch (Schweiz, ohne ß).
