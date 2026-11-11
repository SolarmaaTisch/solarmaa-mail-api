# Solarmaa Energie-Dashboard

Live-Anzeige für Kunden-Solaranlagen im Solarmaa-Design. Ein Raspberry Pi auf der Hutschiene im Verteiler liest Wechselrichter, Zähler und Speicher aus und zeigt alles als App im Browser von Handy, Tablet oder PC. Lokal, ohne Cloud.

- Geräte: SunSpec (viele Hersteller), Fronius, Huawei, GoodWe, Sigenergy, Shelly, smart-me, whatwatt Go, myStrom, Wallboxen go-e und Easee; Erkennung automatisch
- Mehrfamilienhaus/ZEV: Hausskizze mit anklickbaren Zählern, Login mit Rollen, Rechnungen aus smart-me
- Wartung im Browser: Web-Update mit Testlauf, Gerätestatus
- Abregelung bei negativen Day-Ahead-Preisen (Nulleinspeisung) mit Simulations- und Testmodus

| Dokument | Inhalt |
|---|---|
| [Konzept](docs/01-konzept.md) | Idee, Funktionen, Design, Aufbau, Ausbaustufen, offene Entscheide |
| [Bauplan](docs/02-bauplan.md) | Prinzipschema, Stückliste, Umgebung, Verdrahtung |
| [Einbauanleitung](docs/03-einbauanleitung.md) | Werkstatt-Vorbereitung, Einbau im Verteiler, Übergabe |
| [Inbetriebnahme](docs/04-inbetriebnahme.md) | Pi-Programm einrichten, Wechselrichter verbinden, Fehlersuche, API |
| [Kurzanleitung Kunde](docs/05-kunden-kurzanleitung.md) | Einseitige Anleitung zum Abgeben |
| [Haus und Abrechnung](docs/06-haus-und-abrechnung.md) | Hausskizze, Rollen und Login, smart-me, Rechnungen |
| [Wartung und Updates](docs/07-wartung-und-updates.md) | Web-Update, Fernzugriff, Geräteerkennung |
| [Wechselrichter und Modbus TCP](docs/08-wechselrichter-modbus-tcp.md) | Was jede Marke über Modbus TCP liefert, Ports, Stolpersteine, wann RS485 |
| [Evolium-Speicher](docs/09-evolium.md) | Anzeige über Shelly-Messung, geschätzter Ladezustand, Anfrage an Evolium |
| [Stecker, Wallboxen, whatwatt](docs/10-stecker-und-wallboxen.md) | myStrom, go-e, Easee, whatwatt Go |
| [Sicherheit und Fernzugriff](docs/12-sicherheit-und-fernzugriff.md) | Erreichbarkeit von aussen, Prüfbefunde, offene Entscheide |
| [Abregelung bei negativen Preisen](docs/11-abregelung.md) | Day-Ahead (ENTSO-E), Nulleinspeisung, Feldtest PIKO CI |

## Schnellstart (Demo auf dem eigenen Rechner)

Python 3.11 oder neuer, keine weiteren Pakete:

```bash
cd pi
python3 -m solarmaa_dash run --port 8080 --db ./demo.db
```

Ohne Konfigurationsdatei läuft der Demo-Modus. Dann http://localhost:8080 öffnen.

Mit Beispielhaus (8 Einheiten, Rechnungen, Login `solarmaa` / `verwaltung` / `mieter`, Passwort `demo-passwort`):

```bash
cd pi
python3 -m solarmaa_dash run -c config.haus-demo.toml --db ./haus-demo.db --port 8080
```

Dann http://localhost:8080/haus.html öffnen.

Nur die App ohne Pi ansehen: `app/index.html?demo` bzw. `app/haus.html?demo` im Browser öffnen.

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
