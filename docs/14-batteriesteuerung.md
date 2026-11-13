# Aktive Batteriesteuerung (Auftrag, Entwurf)

Stand 13.11.2026: **noch nicht umgesetzt.** Dieses Dokument ist der bereinigte Auftrag als Grundlage für den Planer. Gebaut wird erst, wenn die offenen Fragen unten beantwortet sind und die Fernwartung (Portal Stufe 3) ausgeliefert ist. Vorher soll die Abregelung an einer echten PIKO-CI-Anlage geprüft sein: Sie ist der erste Schreibweg zu Geräten (L5 in `ENTSCHEIDE.md`).

## Ziel

Der Pi steuert Batteriespeicher aktiv, statt sie nur auszulesen.

- **Stufe 1 (dieser Auftrag):** Der Pi plant die Batterie über den Tag, gestützt auf drei Grundlagen:
  - **Day-Ahead-Preis:** bei negativen oder sehr tiefen Preisen laden und Einspeisung vermeiden; teure Stunden mit Batteriestrom decken.
  - **PV-Prognose:** die Mittagsspitze in die Batterie nehmen, statt am Vormittag voll zu sein.
  - **Lastprognose:** abendliche Bezugsspitzen glätten.

  Der Eigenverbrauch soll dabei höchstens so viel schlechter werden, wie pro Anlage erlaubt ist (siehe Frage 2).
- **Stufe 2 (später, nur vorbereiten):** Teilnahme an einem Regelenergie-Pool. Die Schnittstelle für externe Leistungsvorgaben wird vorgesehen, aber nicht gebaut.

## Grundsätze aus dem bestehenden System

- **Gerätezugang:** nur Modbus TCP und lokale HTTP-Schnittstellen, kein RS485. Keine neuen Bibliotheken: Der Pi bleibt bei der Python-Standardbibliothek, und die Tests nutzen die vorhandene Modbus-Attrappe (`tests/test_dash.py`, `FakeModbus`).
- **Day-Ahead-Preis:** Die Preise kommen aus dem vorhandenen Modul `preise.py` (ENTSO-E, Zone Schweiz, Auflösung 15 Minuten), das schon die Abregelung nutzt. Ein zweiter Preisabruf entsteht nicht.
- **Berechnung auf dem Pi:** Der Pi berechnet den Fahrplan selbst, weil er die Verlaufsdaten der Anlage lokal hat und auch ohne Internet weiterläuft. Das Portal zeigt Fahrplan und Auswertung und schaltet die Steuerung ein und aus, berechnet aber nichts.
- **Eine Stelle entscheidet:** Abregelung und Batteriesteuerung schreiben an dieselbe Anlage. Bei negativem Preis wird zuerst die Batterie geladen und erst dann die PV gedrosselt. Beide Regler dürfen nicht gegeneinander arbeiten.
- **Vorzeichen:** Im Programm gilt `battery_w` **+ = Entladen** (Kopf von `pi/solarmaa_dash/sources.py` und die Profile). Der ursprüngliche Auftrag hatte «+ = Laden». Für den Sollwert der Treiber wird darum eine eindeutige, eigene Benennung gewählt, zum Beispiel `ladeleistung_w`, und an der Schnittstelle getestet.
- **Rückfall:** Der sichere Zustand einer Batterie ist der herstellereigene Eigenverbrauchsmodus (`freigeben`). Er greift bei Programmstart und -ende, bei Fehlern, bei Verbindungsverlust, bei fehlendem oder abgelaufenem Fahrplan und wenn der Watchdog anschlägt. Bei der Abregelung gilt dagegen E1 («halten»).
- **Eine Verbindung pro Gerät:** Der Steuer-Treiber benutzt die Leseverbindung des Geräteadapters mit, statt eine zweite zu öffnen. SolarEdge erlaubt zum Beispiel nur eine einzige Verbindung.
- **Register:** nur aus der Herstellerdokumentation, mit Quelle im Profil; bis zur Prüfung an der Anlage `geprueft = false`. Die evcc-Vorlagen sind höchstens ein Hinweis; Code wird daraus nicht übernommen.

## Aufbau

1. **Treiber pro Hersteller**, als Steuerprofil wie `profile/kostal_piko_ci.toml`, wo das reicht, sonst als eigenes Modul. Gemeinsame Schnittstelle:
   - `faehigkeiten()`: Netzladen möglich, Zwangsentladung möglich, maximale Lade- und Entladeleistung, kleinster Abstand zwischen Befehlen, eigener Watchdog des Geräts.
   - `zustand()`: liefert die Werte, die der Pi ohnehin liest.
   - `ladeleistung_setzen(w)`, `halten()` (nicht entladen, Laden aus PV erlaubt), `freigeben()` (idempotent).

   Nach jedem Schreibbefehl liest der Treiber den Wert zurück und protokolliert Abweichungen.
2. **Strategie**, herstellerneutral, pro Anlage wählbar:
   - `eigenverbrauch`: nur `freigeben()`. Standard und Rückfall.
   - `plan`: Fahrplan in 15-Minuten-Feldern aus Preis, PV-Prognose und Lastprognose. Die Lastprognose stammt aus dem lokalen Verlauf.
   - `extern`: nur die Schnittstelle für Stufe 2.
3. **Sicherheit:**
   - Jeder Fahrplan hat ein Ablaufdatum, und der Pi hat einen Watchdog im Regelzyklus.
   - Grenzen: Ladezustand min/max pro Anlage, die Leistungsgrenzen aus `faehigkeiten()` und vom Batteriemanagement, dazu eine Rampenbegrenzung.
   - Netzladen ist standardmässig aus und wird pro Anlage freigeschaltet.
   - Die Steuerung braucht zwei Einträge pro Anlage: einen Schalter (Standard aus) und die Einwilligung des Kunden. Fehlt eines davon, gibt es keinen Befehl.
   - Modi wie bei der Abregelung: `aus`, `simulation` (rechnen und protokollieren, nichts schreiben), `test`, `auto`.
   - Jeder Schreibbefehl kommt ins Protokoll: Zeitpunkt, Sollwert, Rückmeldung, Grund. Störungen gehen über die bestehenden Alarme (Lampe, Push, E-Mail, Portal).

## Auswertung im Portal

Pro Tag: Einspeisespitze (15 Minuten), Bezugsspitze, Eigenverbrauch, Kosten bzw. Erlös nach Day-Ahead-Preis.

Der Vergleich mit dem Eigenverbrauchsmodus wird auf dem Pi nachgerechnet: Er simuliert die Batterie im Eigenverbrauchsmodus mit den gemessenen Werten für PV und Verbrauch. Hochgeladen werden nur Tageswerte.

## Hersteller

Reihenfolge nach Frage 3. Hinweise aus dem ursprünglichen Auftrag (alle **ungeprüft**):

| Hersteller | Ansatz |
|---|---|
| Sigenergy | Modbus TCP 502. «Remote EMS» ein, Lade- bzw. Entladeleistung setzen; Remote EMS aus bedeutet `freigeben`. Ein Leseprofil existiert bereits (`profile/sigenergy.toml`, ungeprüft). |
| Victron GX | Modbus TCP am GX-Gerät, ESS-Modi 2 und 3. Bei Modual-Anlagen sind Garantie und eigene Modual-Logik zuerst zu klären, bis dahin nur `simulation`. |
| SolarEdge | Modbus TCP 1502, nur eine Verbindung. Speichersteuerung im Modus «Remote» laut Application Note «Storage Control». |
| Huawei (auch SmartLogger-Gewerbespeicher) | aus Tins Anlagen. Leseprofile vorhanden, ungeprüft. |
| später | SMA, Kostal Plenticore, Fronius GEN24 (SunSpec Model 124), GoodWe. |

Evolium hat keine Schnittstelle und kann nicht gesteuert werden (`docs/09-evolium.md`).

## Tests und Abnahme

- **Strategie:** Unit-Tests mit Tagesprofilen (sonnig, bewölkt, Winter) und Preisprofilen (normal, negativer Mittag, Preisspitze am Abend).
- **Treiber:** Tests gegen die Modbus-Attrappe, mit Verbindungsabbruch, falscher Rückmeldung, abgelaufenem Fahrplan und Neustart mitten im Befehl.
- **Abnahme:**
  - Nach Verbindungsverlust oder Absturz ist die Anlage spätestens nach einem Regelzyklus wieder im Eigenverbrauch.
  - Ohne Schalter und Einwilligung gibt es keinen Befehl.
  - Abregelung und Batterie arbeiten bei negativem Preis nicht gegeneinander.
  - `simulation` liefert nachvollziehbare Fahrpläne.
- **Pilot:** pro Hersteller eine Anlage, eine Woche `simulation`, dann `test` in einem Zeitfenster, dann `auto`.

## Offene Fragen an Tin

1. **Nutzen für den Kunden:** Wie stark gewichten Preis (Day-Ahead) und Netzdienlichkeit (Spitzen)? Gibt es bei deinen Netzbetreibern Einspeisebegrenzungen, Leistungstarife oder dynamische Tarife, die einbezogen werden müssen? (Antwort Tin: Day-Ahead-Preis wird gebraucht.)
2. **Zulässiger Verlust:** Bei falscher Prognose kann Eigenverbrauch verloren gehen. Welcher Verlust ist tragbar, zum Beispiel ein bestimmter Prozentsatz pro Monat?
3. **Hersteller:** Welche Speicher sind am häufigsten verbaut, und wo steht eine Testanlage?
4. **Einwilligung:** Darf sie nur vor Ort gesetzt werden, oder auch über die Fernwartung? E7 erlaubt heute, aus der Ferne alles zu ändern.
5. **PV-Prognose:** Welcher Wetterdienst (Name, Anbieter, Schlüssel ja oder nein)? Der Pi braucht dafür Internet.
6. **Netzladen und Entladen ins Netz bei hohem Preis:** gewünscht? Beides ändert die Abrechnung des Kunden und ist standardmässig aus.

## Nicht Teil dieses Auftrags

Präqualifikation bei Swissgrid, Pool-Anbindung, Vergütung für Kunden, Verträge mit Netzbetreibern, RS485.
