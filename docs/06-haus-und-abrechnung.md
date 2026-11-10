# Haus, Zähler und Abrechnung (ZEV / Mieterstrom)

Für Mehrfamilienhäuser und Gewerbebauten mit mehreren Einheiten. Der Pi zeigt eine schematische Skizze des Hauses. Jede Einheit und jeder Zähler ist anklickbar und zeigt:

- Live-Verbrauch und den Anteil Solarstrom
- Zähler mit Seriennummer und Zählerstand (Bezug, Rückspeisung)
- Strombezug pro Monat, aufgeteilt in Solar- und Netzstrom
- Rechnungen aus der smart-me-Abrechnung mit Status (offen, überfällig, gemahnt, bezahlt)
- **Wer Zugriff hat** (nur für Verwaltung und Solarmaa sichtbar)

Aufruf: `http://<pi-adresse>/haus.html`, oder unten im Anlagen-Dashboard «Hausansicht öffnen».

## Rollen

| Rolle | Wer | Sieht | Darf |
|---|---|---|---|
| `admin` | Solarmaa | alle Einheiten, Berechtigte, Rechnungen, Wartung | Rechnungen importieren, Status setzen, Updates, Geräteerkennung |
| `verwaltung` | Liegenschaftsverwaltung, Eigentümer | alle Einheiten, Berechtigte, Rechnungen | Rechnungen importieren, Status setzen |
| `mieter` | Mieterpartei | nur die eigene Einheit; andere Einheiten erscheinen in der Skizze als «kein Zugriff» | nichts ändern |

Ohne Anmeldung sieht man nur das Anlagen-Dashboard (Produktion, Gesamtverbrauch), keine Daten einzelner Einheiten.

Benutzer anlegen (auf dem Pi, per SSH):

```bash
cd /opt/solarmaa-dash/pi
sudo -u solarmaa-dash python3 -m solarmaa_dash benutzer add solarmaa --rolle admin -c /etc/solarmaa-dash/config.toml
sudo -u solarmaa-dash python3 -m solarmaa_dash benutzer add immo-meier --rolle verwaltung --anzeigename "Immo Meier AG" -c /etc/solarmaa-dash/config.toml
sudo -u solarmaa-dash python3 -m solarmaa_dash benutzer add eg-links --rolle mieter --einheit eg-links --anzeigename "Familie Muster" -c /etc/solarmaa-dash/config.toml
sudo -u solarmaa-dash python3 -m solarmaa_dash benutzer list -c /etc/solarmaa-dash/config.toml
```

Passwörter: mindestens 10 Zeichen. Bei Mieterwechsel den alten Benutzer löschen (`benutzer del NAME`) und einen neuen anlegen.

> **Sicherheit:** Die Seite läuft im Heimnetz über HTTP. Passwörter gehen dabei unverschlüsselt durchs WLAN des Hauses. Für Version 1 im eigenen Netz vertretbar, für Zugriff von aussen (Stufe 3) braucht es HTTPS, z. B. über Tailscale oder einen Solarmaa-Server.

## Einheiten und Zähler eintragen

In `/etc/solarmaa-dash/config.toml`:

```toml
[haus]
aktiv = true
name = "MFH Moserstrasse 12"
geschosse = ["UG", "EG", "1. OG", "2. OG"]     # von unten nach oben

[[haus.einheit]]
id = "eg-links"                 # kurz, ohne Leerzeichen; auch für Mieter-Logins
name = "Wohnung EG links"
geschoss = "EG"
typ = "wohnung"                 # wohnung | gewerbe | allgemein | technik
smartme_ordner = "EG links"     # Name der Abrechnungseinheit in smart-me

  [[haus.einheit.zaehler]]
  name = "Wohnungszähler"
  typ = "smartme_cloud"
  seriennummer = "7100200"
```

Ein Zähler kann jeder unterstützte Typ sein: smart-me (Cloud), Shelly, SunSpec-Zähler oder ein Modbus-Profil. Die Seriennummern der smart-me-Zähler zeigt `python3 -m solarmaa_dash smartme-liste` oder die Geräteerkennung.

Zum Ausprobieren gibt es `pi/config.haus-demo.toml`: ein Beispielhaus mit acht Einheiten, simulierten Zählern und Beispielrechnungen.

## smart-me anbinden

**Cloud-API (Standard):** Im smart-me-Portal einen API-Key erstellen und in `[smartme] api_key` eintragen. Der Pi fragt alle Zähler des Kontos mit einem einzigen Aufruf ab (`GET /Devices`, zwischengespeichert, Standard 30 s). Laut smart-me brauchen gewerbliche Nutzung und höhere Abfrageraten die Professional-Lizenz.

**Lokal über Modbus TCP:** smart-me-Zähler können ihre Messwerte auch direkt im Netz liefern, ohne Umweg über die Cloud. Laut smart-me muss Modbus TCP dafür pro Zähler im Portal aktiviert werden (Professional-Abo). Die Registerliste konnte beim Erstellen nicht abgerufen werden. Sie gehört in `pi/solarmaa_dash/profile/smartme_lokal.toml` (Vorlage liegt dort). Bis dahin die Cloud-API nutzen.

Die Feldnamen der Cloud-API (`ActivePower`, `CounterReadingImport`, `Serial`, …) stammen aus Beispielen der Community und nicht aus der offiziellen API-Referenz. Bei der ersten Anlage mit `smartme-liste` prüfen.

## Rechnungen

smart-me erstellt die ZEV-Rechnungen im Webportal. Eine Schnittstelle zum Abholen von Rechnungen ist nicht dokumentiert. Deshalb funktioniert es so:

1. In smart-me die Rechnungen der Periode als CSV exportieren.
2. In der Hausansicht unter «Rechnungen» auf «CSV aus smart-me importieren» klicken (oder `python3 -m solarmaa_dash rechnungen-import datei.csv`).
3. Der Pi erkennt die Spalten (Rechnungsnummer, Einheit, Empfänger, Periode, Betrag, Status, Fälligkeit) an üblichen Namen. Weicht der Export ab, die Spaltennamen in `[haus.rechnungen_csv]` eintragen.
4. Die Einheit wird über `id`, `name` oder `smartme_ordner` zugeordnet. Nicht zuordenbare Zeilen meldet der Import.
5. Zahlungseingänge in der Hausansicht mit «Bezahlt» markieren. Ein erneuter Import ohne Status-Spalte überschreibt «bezahlt» nicht.

**Offener Punkt mit smart-me:** Fragen, ob es eine API für Rechnungen und Zahlungsstatus gibt oder geben wird. Dann lässt sich Schritt 1–2 automatisieren.

## Solaranteil pro Einheit

Der Pi verteilt den im Haus selbst genutzten Solarstrom im Takt der Abfrage anteilig auf die Einheiten: Wer gerade mehr verbraucht, erhält mehr Solarstrom. Das ergibt eine gute Live-Anzeige. **Massgebend für die Rechnung ist die Abrechnung in smart-me** (15-Minuten-Werte, Tarife, Verteilschlüssel). Die App weist darauf hin.
