# Evolium-Speicher anzeigen

## Ausgangslage

Evolium-Speicher (Evolium Technologies, Wallis) sind laut Hersteller AC-gekoppelt: 10 kWh, 230 V AC, 1,8 kW Leistung, mit integriertem Fehlerstrom-Schutz Typ B 25 A / 30 mA. Evolium überwacht die Module selbst und verschickt monatliche Berichte, die Batterie hat ein eigenes Display. Eine öffentlich dokumentierte Datenschnittstelle (API, Modbus) gibt es nach unserer Recherche nicht (Stand 10.11.2026).

## Lösung: von aussen messen

Weil der Speicher an einem normalen 230-V-Abgang hängt, misst ein Shelly mit Richtungserkennung die Leistung in den Speicher und aus dem Speicher. So zeigt der Pi Laden und Entladen live an, unabhängig vom Hersteller.

| Was | Empfehlung |
|---|---|
| Messgerät | Shelly mit bidirektionaler Messung auf der Hutschiene, z. B. Shelly Pro EM-50 mit Stromwandler am Speicher-Abgang. Eignung (Messbereich, Rückspeiserichtung) im Datenblatt prüfen. |
| Ladezustand | wird **geschätzt** (siehe unten) und in der App mit «ca.» angezeigt |
| Wechselrichter, Netz | wie gewohnt über SunSpec, Fronius, Profil oder Shelly |

Einbau des Shelly im Verteiler: Arbeiten durch berechtigte Person nach NIV, nach Herstellerangaben und NIN.

### Konfiguration

```toml
[[geraet]]
name = "Evolium"
rolle = "speicher"
typ = "shelly"
host = "192.168.1.85"
vorzeichen = -1           # Shelly zeigt Laden als Bezug (+). Solarmaa-Konvention: + Entladen
soc_schaetzen = true
kapazitaet_kwh = 10       # nutzbare Kapazität laut Evolium
wirkungsgrad = 0.92       # Annahme pro Richtung, bei Bedarf anpassen
reserve_pct = 0           # Ladezustand, bei dem Evolium aufhört zu entladen, falls bekannt
```

**Vorzeichen prüfen:** Bei Sonne und Überschuss muss die App «lädt» zeigen, abends «entlädt». Sonst `vorzeichen` umkehren.

**Hausverbrauch:** Der Pi rechnet Hausverbrauch = PV + Netz + Speicher (Entladen positiv). Bei einem AC-gekoppelten Speicher mit separater Messung ist das richtig. Der Verbrauch enthält dann das Laden des Speichers nicht.

## Wie der Ladezustand geschätzt wird

- Energiebilanz: Laden × Wirkungsgrad hinein, Entladen ÷ Wirkungsgrad hinaus, bezogen auf `kapazitaet_kwh`.
- **Selbstkalibrierung:**
  - **voll (100 %)**: Die Anlage speist ins Netz ein, aber der Speicher lädt 5 Minuten lang nicht.
  - **leer (`reserve_pct`)**: Das Haus bezieht Strom vom Netz, aber der Speicher entlädt 5 Minuten lang nicht.
- Nach dem Start beginnt die Schätzung bei 50 %, bis sie sich das erste Mal kalibriert hat. Das ist meist nach dem ersten sonnigen Tag der Fall. Der Wert wird gespeichert und übersteht Neustarts.

**Grenzen der Schätzung:**
- Lädt oder entlädt der Speicher aus anderen Gründen nicht (Wartung, Leistungsbegrenzung, Moduletausch), kalibriert sie sich falsch. Beim nächsten echten Voll- oder Leer-Zustand korrigiert sie sich wieder.
- Second-Life-Zellen verlieren mit der Zeit Kapazität, und nach einem Modultausch ändert sie sich. Die Schätzung kennt die aktuelle Kapazität nicht.
- Darum zeigt die App «ca.». Für genaue Werte braucht es Daten von Evolium (siehe unten).

## Bessere Lösung: Daten direkt von Evolium

Evolium überwacht jedes Modul rund um die Uhr. Die Daten existieren also. Fragen an Evolium:

1. Gibt es eine lokale Schnittstelle am Speicher (Modbus TCP, REST/HTTP, MQTT) mit Ladezustand, Leistung und Zustand?
2. Gibt es eine Cloud-API für Installationspartner (pro Anlage oder für alle Anlagen eines Partners)?
3. Welches Messgerät nutzt der Speicher für die Eigenverbrauchsregelung am Netzanschluss? Ist es ein Shelly, kann der Pi denselben Zähler mitlesen, ohne zusätzliche Hardware.
4. Wie viel Kapazität ist nutzbar, und ab welchem Ladezustand hört der Speicher auf zu entladen?

Gibt es eine Schnittstelle, wird daraus ein Profil (Modbus) oder ein kleiner Adapter (HTTP/MQTT). Die Messung mit dem Shelly kann dann entfallen.

### Vorlage für die Anfrage

> Bonjour / Grüezi
>
> Wir installieren als Solarmaa GmbH regelmässig Evolium-Speicher und möchten sie in unserer Kunden-Visualisierung anzeigen (lokal, auf einem Raspberry Pi im Verteiler, nur lesend).
>
> - Bietet der Speicher eine lokale Schnittstelle (Modbus TCP, HTTP/REST oder MQTT) für Ladezustand, Lade-/Entladeleistung und Status? Falls ja: Registerliste bzw. Dokumentation?
> - Gibt es alternativ eine Cloud-API für Installationspartner?
> - Welches Messgerät nutzt der Speicher am Netzanschluss für die Regelung?
> - Nutzbare Kapazität und minimaler Ladezustand?
>
> Besten Dank und freundliche Grüsse
> Solarmaa GmbH

## Quellen

- [Evolium: Produkte](https://www.evolium.ch/en/our-products/)
- [Evolium: Website](https://www.evolium.ch/?lang=en)
- [startupticker: Markteinführung mit OIKEN](https://www.startupticker.ch/en/news/evolium-technologies-lance-la-commercialisation-de-ses-batteries-durables-avec-oiken)
- [pv magazine: Abo-Modell und Garantie](https://www.pv-magazine.com/2026/04/03/swiss-startup-offers-lifetime-guarantee-for-second-life-batteries/)
