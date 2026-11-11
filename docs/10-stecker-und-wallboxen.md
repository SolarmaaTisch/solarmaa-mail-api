# myStrom-Stecker und Wallboxen (go-e, Easee)

Einzelne Verbraucher erscheinen in der App zusätzlich zum Hausverbrauch:
- eine **Wallbox** als eigener Knoten «Auto» im Energiefluss, mit Status (lädt, verbunden, fertig) und «davon Auto x kWh» in den Tageswerten
- **Stecker** (z. B. Waschmaschine, Boiler) als kleine Anzeigen unter dem Energiefluss

Sie haben die Rolle `verbraucher`. Ihr Verbrauch ist bereits im Hausverbrauch enthalten (gemessen am Netzanschluss) und wird nur aufgeschlüsselt, nicht doppelt gezählt. Fällt ein Verbraucher aus, läuft der Rest normal weiter, die App zeigt dort «–».

## myStrom Switch

Lokal über das Heimnetz, ohne Cloud: `GET http://<ip>/report` liefert Leistung (`power`), Relais-Zustand und, bei neuerer Firmware, Energie seit dem letzten Neustart.

```toml
[[geraet]]
name = "Waschmaschine"
rolle = "verbraucher"
typ = "mystrom"
host = "192.168.1.90"
# token = ""          # nur falls im myStrom-Gerät ein API-Token gesetzt ist
```

**Balkonkraftwerk am myStrom:** Statt `verbraucher` die Rolle `pv` verwenden. Die App zeigt dann die Produktion des Balkonkraftwerks. Ohne Netzzähler fehlen Netz und Hausverbrauch.

**Wohnungszähler im Haus:** Ein myStrom kann auch unter `[[haus.einheit.zaehler]]` als `typ = "mystrom"` stehen, z. B. für ein einzelnes Gerät in einer Einheit.

Hinweis: Der Energiezähler des myStrom beginnt nach jedem Neustart bei null. Die App rechnet Tageswerte deshalb aus der Leistung, nicht aus diesem Zähler.

## go-e Charger (Gemini, HOMEfix)

Lokal über die **HTTP-API v2**. In der go-e-App unter den Einstellungen die lokale HTTP-API v2 aktivieren.

```toml
[[geraet]]
name = "Wallbox"
rolle = "verbraucher"
typ = "goe"
host = "192.168.1.95"
```

Gelesen werden `nrg` (Leistung, Index 11 = gesamt in W), `car` (1 kein Auto, 2 lädt, 3 wartet, 4 fertig, 5 Fehler), `wh` (Energie seit Anstecken) und `eto` (Gesamtenergie). Die Geräteerkennung findet go-e-Wallboxen automatisch, sobald die API aktiv ist.

## Easee (Home, Charge, One)

Easee bietet **keine lokale Schnittstelle**, nur die Easee-Cloud. Der Pi braucht deshalb Internet und ein Easee-Konto mit Zugriff auf die Wallbox (z. B. ein Solarmaa-Servicekonto, dem der Kunde die Wallbox freigibt).

```toml
[easee]
benutzer = "service@solarmaa.ch"
passwort = ""                 # Konfigurationsdatei ist nur für root und den Dienst lesbar

[[geraet]]
name = "Wallbox"
rolle = "verbraucher"
typ = "easee"
charger_id = "EH123456"       # Seriennummer der Wallbox, steht in der Easee-App
```

- Easee begrenzt die Anzahl Anfragen. Der Pi fragt deshalb höchstens alle 30 Sekunden. Bei «zu viele Anfragen» pausiert er 5 Minuten und zeigt so lange den letzten Wert.
- Die Einheiten (kW/kWh) der Cloud-Felder `totalPower`, `sessionEnergy` und `lifetimeEnergy` stammen aus Open-Source-Integrationen. Bei der ersten Anlage mit der Easee-App vergleichen.
- Ein Easee Equalizer misst den Hausanschluss. Er ist ebenfalls nur über die Cloud erreichbar und wird zurzeit nicht ausgelesen.

## Zum Prüfen bei der ersten Anlage

| Gerät | Prüfen |
|---|---|
| myStrom | Leistung in der App = Anzeige in der myStrom-App |
| go-e | Leistung und Status beim Laden; Index 11 in `nrg` = Gesamtleistung |
| Easee | Leistung kW → W richtig umgerechnet; Status passt zur Easee-App |

`python3 -m solarmaa_dash geraet-test` zeigt alle Werte auf einen Blick.

## Quellen

- myStrom: [REST API](https://mystrom.com/wp-content/uploads/REST_API_WSE.txt), [python-mystrom](https://sources.debian.org/src/python-mystrom/2.6.0-1/pymystrom/switch.py)
- go-e: [go-eCharger API v2 (offiziell)](https://github.com/goecharger/go-eCharger-API-v2), Feldbeschreibungen in `API_KEYS_FIRMWARE/apikeys-en.md`, HTTP in `http-en.md`
- Easee: [Easee Developer](https://developer.easee.com/docs/api-command-and-control), [pyeasee](https://github.com/nordicopen/pyeasee), [HomewrightHQ Easee](https://github.com/magnusdunker/homewrighthq-easee)

## whatwatt Go (Smart Meter des Netzbetreibers)

Der whatwatt Go steckt an der Kundenschnittstelle des Smart Meters und liefert Bezug und Einspeisung direkt vom Zähler des Netzbetreibers. Damit braucht es am Netzanschluss **keinen zusätzlichen Zähler** wie einen Shelly.

Voraussetzungen und offene Punkte:
- Welche Dienste (REST, MQTT, Modbus TCP) verfügbar sind, hängt laut whatwatt von Firmware, Edition/Lizenz und Konfiguration ab. Die Lizenz steht in `http://<ip>/api/v1/system` unter `device.license.type`. Liefert `/api/v1/report` nichts, meldet der Pi das in `geraet-test`.
- Unterstützt werden u. a. Zähler von Landis+Gyr, Iskraemeco, Sagemcom, Kamstrup, Ensor, Siemens, Echelon/NES und **smart-me Nimbus** (P1). Gerät im Netz: `whatwatt-XXXXXX.local`.
- Die Kundenschnittstelle muss beim Netzbetreiber freigeschaltet sein (je nach Werk auf Anfrage).
- Die Feldpfade stammen aus der Loxone-Anleitung von whatwatt. Die Einheiten widersprechen sich in den Quellen (W/Wh oder kW/kWh). Standard ist kW/kWh. Bei der ersten Anlage die Werte mit der Anzeige des Smart Meters vergleichen und bei Bedarf umstellen.
- MQTT und Modbus TCP des whatwatt (ebenfalls lizenzpflichtig) nutzt der Pi zurzeit nicht.

```toml
[[geraet]]
name = "Smart Meter (whatwatt)"
rolle = "netz"
typ = "whatwatt"
host = "192.168.1.70"
# passwort = ""               # nur falls das WebUI-Passwort gesetzt ist (Benutzername bleibt leer)
leistung_einheit = "kW"       # "W", falls die App 1000-fach zu hohe Werte zeigt
energie_einheit = "kWh"       # "Wh" entsprechend
```

Im Mehrfamilienhaus kann ein whatwatt auch als Zähler einer Einheit stehen (`[[haus.einheit.zaehler]]`, `typ = "whatwatt"`), z. B. an einem smart-me Nimbus: so liest der Pi den Zähler lokal statt über die smart-me-Cloud. Die Geräteerkennung findet whatwatt über `/api/v1/system` oder den Gerätenamen `whatwatt-…`; sonst von Hand eintragen.

Noch offen: die genaue Struktur von `/api/v1/report` (Seite «Report Objects» der whatwatt-Doku) und damit die Einheiten.

Quellen: [whatwatt Go API-Dokumentation](https://documentation.whatwatt.ch/), [Loxone-Integration](https://documentation.whatwatt.ch/60-integrations/loxone/), [Home-Assistant-Anleitung (PDF)](https://files.whatwatt.ch/whatwatt_Go_HomeAssistant_v1.5.pdf), [Modbus-TCP-Hinweise (clemap)](https://docs.clemap.com/help-center/whatwatt-go), [Eniris: whatwatt](https://docs.eniris.be/en/Controller/Devices/Energy-meter/WhatWatt)
