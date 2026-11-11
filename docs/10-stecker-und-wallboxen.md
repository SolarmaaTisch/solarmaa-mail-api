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
