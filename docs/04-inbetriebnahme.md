# Inbetriebnahme und Programmierung des Raspberry Pi

Alle Befehle per SSH auf dem Pi. Das Programm liegt nach der Installation in `/opt/solarmaa-dash`, die Konfiguration in `/etc/solarmaa-dash/config.toml`, die Messwerte in `/var/lib/solarmaa-dash/data.db`.

## 1. Modbus TCP am Wechselrichter aktivieren

Im Webinterface oder in der Inbetriebnahme-App des Herstellers Modbus TCP einschalten. Worauf achten:

- **SunSpec** als Registerformat, bei Auswahl **«int + SF»** (nicht «float»).
- Port 502 (manche Hersteller nutzen einen anderen Port, dann in der Konfiguration eintragen).
- Nur Lesezugriff reicht. Falls es eine Einstellung «Steuerung über Modbus zulassen» gibt: ausgeschaltet lassen.

Menünamen ändern sich mit der Firmware. Pro Gerätetyp, den Solarmaa verbaut, einmal ausprobieren und unten in der Tabelle festhalten.

| Hersteller / Typ | Getestet am | Modbus aktivieren unter | Unit Wechselrichter | Unit Zähler | Bemerkungen |
|---|---|---|---|---|---|
| _noch leer_ | | | | | |

## 2. Feste Adressen im Kundenrouter

Im Router eine DHCP-Reservation für den Pi und für den Wechselrichter anlegen. Sonst ändert sich die Adresse nach einem Router-Neustart und der Pi findet den Wechselrichter nicht mehr.

## 3. Geräte automatisch erkennen

```bash
cd /opt/solarmaa-dash/pi
sudo -u solarmaa-dash python3 -m solarmaa_dash erkennen -c /etc/solarmaa-dash/config.toml --toml /tmp/vorschlag.toml
```

Der Pi sucht im eigenen Netz (/24) nach Shelly, Fronius (Solar API), SunSpec-Geräten (verschiedene Unit-IDs), Huawei, GoodWe und Sigenergy (über die `[erkennung]`-Regeln in den Profilen) sowie nach allen Zählern im smart-me-Konto. Das Ergebnis ist eine Tabelle und ein Vorschlag für die `[[geraet]]`-Blöcke. Dasselbe gibt es im Browser unter Wartung → «Geräte im Netz suchen».

Was die Erkennung **nicht** sicher weiss: wofür ein Shelly misst (Netzanschluss, PV-Abgang oder eine Wohnung). Rollen im Vorschlag prüfen, dann in die Konfiguration übernehmen.

Danach alle Geräte einmal lesen:

```bash
sudo -u solarmaa-dash python3 -m solarmaa_dash geraet-test -c /etc/solarmaa-dash/config.toml
```

Jede Zeile beginnt mit `OK` oder `FEHLER` und zeigt die gelesenen Werte. Profile mit `geprueft = false` hier besonders genau mit der Hersteller-App vergleichen.

## 3b. Einzelnes SunSpec-Gerät scannen

```bash
cd /opt/solarmaa-dash/pi
python3 -m solarmaa_dash scan 192.168.1.50 --unit 1
```

Beispielausgabe:

```
SunSpec-Gerät 192.168.1.50:502, Unit 1
  Modell   1 @ 40004 (Länge  66)  Common (Gerätedaten)
  Modell 103 @ 40072 (Länge  50)  Wechselrichter 3-phasig
  Modell 160 @ 40124 (Länge  48)  MPPT (Strings)
```

Den Zähler suchen: Bei vielen Anlagen hängt er am Wechselrichter und erscheint unter einer anderen Unit-ID am gleichen Host. Nacheinander typische IDs probieren (`--unit 200`, `--unit 240`, `--unit 2`) bis ein Modell 201–204 erscheint. Bei manchen Geräten steht das Zählermodell direkt in der Liste der Unit 1; dann beim Zähler dieselbe Unit eintragen.

Erscheint «Kein SunSpec-Marker gefunden», ist Modbus TCP nicht aktiv, die Unit-ID falsch oder das Gerät unterstützt kein SunSpec (siehe Konzept, Kompatibilität).

## 4. Konfiguration eintragen (alte Kurzform)

Die neue Form mit `[[geraet]]`-Blöcken steht in `pi/config.example.toml` (Beispiele A bis D). Die bisherige Kurzform funktioniert weiterhin:

```bash
sudo nano /etc/solarmaa-dash/config.toml
```

```toml
[quelle]
typ = "sunspec"

[quelle.wechselrichter]
host = "192.168.1.50"
unit = 1

[quelle.zaehler]
host = "192.168.1.50"
unit = 200
netz_vorzeichen = 1
```

## 5. Einmal lesen

```bash
python3 -m solarmaa_dash read -c /etc/solarmaa-dash/config.toml
```

Gibt die aktuellen Werte als JSON aus. `pv_w` muss ungefähr der Anzeige am Wechselrichter entsprechen.

## 6. Vorzeichen des Netzzählers prüfen

In der App gilt: `grid_w` **positiv = Bezug**, **negativ = Einspeisung**.

Prüfen bei Sonne, wenn wenig verbraucht wird: Die Anlage speist ein, `grid_w` muss negativ sein. Ist es positiv, `netz_vorzeichen = -1` setzen. Gegenprobe: Bei Nacht muss `grid_w` positiv sein und `load_w` dem Hausverbrauch entsprechen.

## 7. Dienst neu starten und kontrollieren

```bash
sudo systemctl restart solarmaa-dash
systemctl status solarmaa-dash
journalctl -u solarmaa-dash -f        # laufendes Protokoll, mit Ctrl+C beenden
curl -s localhost/api/health
```

`/api/health` antwortet mit `"status": "ok"`, sobald Werte ankommen.

## 8. App am Kunden-Handy

- Browser öffnen, `http://solarmaa-<name>.local` oder die feste IP eingeben (QR-Code).
- iPhone: Teilen → «Zum Home-Bildschirm». Android/Chrome: Menü → «Zum Startbildschirm hinzufügen».
- Manche Android-Geräte lösen `.local`-Adressen nicht auf. Dann die IP-Adresse verwenden.

## Software aktualisieren

```bash
cd ~/solarmaa-mail-api && git pull && sudo ./pi/install.sh
```

Die Konfiguration bleibt erhalten, die Messwerte auch.

## Neues Gerät ohne Programmierung anbinden

Für einen Wechselrichter mit bekannter Modbus-Registerliste eine Datei in `pi/solarmaa_dash/profile/` anlegen (Vorlage: `huawei_sun2000.toml`). Pro Messwert Register, Typ (`u16`, `i16`, `u32`, `i32`, `f32`) und Faktor eintragen. Ein `[erkennung]`-Abschnitt (Register mit Modellnamen) lässt die automatische Erkennung das Gerät finden. Nach dem Test an einer echten Anlage `geprueft = true` setzen.

## Was das Programm macht

| Datei | Aufgabe |
|---|---|
| `pi/solarmaa_dash/modbus.py` | Modbus-TCP-Client (nur Lesen, Funktion 3), ohne Fremdbibliothek |
| `pi/solarmaa_dash/sunspec.py` | Findet die SunSpec-Modelle eines Geräts, liest Leistung, Zählerstände, Ladezustand |
| `pi/solarmaa_dash/sources.py` | kombiniert alle Geräte zu einem Anlagen-Messwert; Demo-Simulation |
| `pi/solarmaa_dash/geraete/` | Adapter: SunSpec, Fronius, Shelly, smart-me, Modbus-Profil |
| `pi/solarmaa_dash/profile/*.toml` | Registerprofile: Huawei, GoodWe, Sigenergy, smart-me lokal (Vorlage) |
| `pi/solarmaa_dash/erkennung.py` | automatische Geräteerkennung im Netz |
| `pi/solarmaa_dash/haus.py` | Einheiten, Hauszähler, Solaranteil pro Einheit |
| `pi/solarmaa_dash/auth.py` · `rechnungen.py` · `wartung.py` | Login und Rollen, Rechnungsimport, Web-Update |
| `pi/solarmaa_dash/store.py` | SQLite: Minutenwerte, Tagessummen, Eigenverbrauch und Autarkie |
| `pi/solarmaa_dash/server.py` | Webserver: App ausliefern, JSON-API, Live-Stream |
| `pi/solarmaa_dash/__main__.py` | Befehle `run`, `erkennen`, `geraet-test`, `scan`, `read`, `smartme-liste`, `benutzer`, `rechnungen-import` |

API für eigene Auswertungen:

| Endpunkt | Inhalt |
|---|---|
| `GET /api/now` | aktuelle Leistung und heutige Summen |
| `GET /api/info` | Anlage, Installateur, Gesamtproduktion |
| `GET /api/history?range=day&date=2026-11-10` | 5-Minuten-Werte eines Tages |
| `GET /api/history?range=week` · `month` · `year&year=2026` | Tages- bzw. Monatssummen |
| `GET /api/stream` | Live-Werte (Server-Sent Events) |
| `GET /api/health` | `ok` oder `fehler` (HTTP 503) für Überwachung |

Tests auf dem Entwicklungsrechner: `python3 -m unittest discover -s tests`

## Fehlersuche

| Anzeige | Ursache | Lösung |
|---|---|---|
| App: «Anlage antwortet nicht» | Wechselrichter aus (Nacht bei manchen Typen), IP geändert, Modbus deaktiviert | `journalctl -u solarmaa-dash` lesen; IP und `scan` prüfen |
| App lädt gar nicht | Pi aus, falsche Adresse, Handy nicht im Heim-WLAN | Pi-LEDs prüfen; IP statt `.local` versuchen |
| Verbrauch ist negativ oder viel zu hoch | Vorzeichen des Zählers falsch | Schritt 6 |
| Werte springen auf 0 | Wechselrichter liefert «nicht implementiert» | Mit `read` prüfen; Firmware-Update oder Hersteller-Adapter nötig |
| Uhrzeit falsch | Kein Internet für Zeitabgleich | Router-Internet prüfen; `timedatectl` |
| Demo-Band sichtbar | `typ = "demo"` | Schritt 4 |
