# Solarmaa Energie-Dashboard

Live-Anzeige für Kunden-Solaranlagen im Solarmaa-Design. Ein Raspberry Pi auf der Hutschiene im Verteiler liest Wechselrichter, Zähler und Speicher über Modbus TCP (SunSpec) aus und zeigt alles als App im Browser von Handy, Tablet oder PC. Lokal, ohne Cloud.

| Dokument | Inhalt |
|---|---|
| [Konzept](docs/01-konzept.md) | Idee, Funktionen, Design, Aufbau, Ausbaustufen, offene Entscheide |
| [Bauplan](docs/02-bauplan.md) | Prinzipschema, Stückliste, Umgebung, Verdrahtung |
| [Einbauanleitung](docs/03-einbauanleitung.md) | Werkstatt-Vorbereitung, Einbau im Verteiler, Übergabe |
| [Inbetriebnahme](docs/04-inbetriebnahme.md) | Pi-Programm einrichten, Wechselrichter verbinden, Fehlersuche, API |
| [Kurzanleitung Kunde](docs/05-kunden-kurzanleitung.md) | Einseitige Anleitung zum Abgeben |

## Schnellstart (Demo auf dem eigenen Rechner)

Python 3.11 oder neuer, keine weiteren Pakete:

```bash
cd pi
python3 -m solarmaa_dash run --port 8080 --db ./demo.db
```

Ohne Konfigurationsdatei läuft der Demo-Modus. Dann http://localhost:8080 öffnen.

Nur die App ohne Pi ansehen: `app/index.html?demo` im Browser öffnen.

## Auf dem Raspberry Pi

```bash
git clone https://github.com/SolarmaaTisch/solarmaa-mail-api.git
cd solarmaa-mail-api
sudo ./pi/install.sh --hostname solarmaa-muster
```

Danach Konfiguration in `/etc/solarmaa-dash/config.toml` anpassen. Details in der [Inbetriebnahme](docs/04-inbetriebnahme.md).

## Aufbau

```
app/                 Web-App (HTML/CSS/JS, keine Abhängigkeiten)
pi/solarmaa_dash/    Python-Dienst: Modbus/SunSpec, Speicherung, Webserver
pi/install.sh        Installation als systemd-Dienst
pi/config.example.toml
docs/                Konzept, Bauplan, Einbau, Inbetriebnahme
tests/               python3 -m unittest discover -s tests
```
