# Sicherheit und Zugriff von ausserhalb

Stand: Prüfung vom 11.11.2026 (unabhängige Code-Prüfung plus Handy-Test). Die gefundenen Fehler sind behoben und mit Tests abgesichert. Was bewusst offen bleibt, steht unten.

## Ist die Seite von ausserhalb erreichbar?

**Nein.** Der Pi ist nur im Netz des Kunden erreichbar (WLAN/LAN). Das ist Absicht: Die Seite läuft über unverschlüsseltes HTTP.

**Den Pi nie per Portweiterleitung im Router ins Internet stellen.** Passwörter und Sitzungen wären mitlesbar, und über die Wartung lassen sich Updates und (bei aktiver Abregelung) Wechselrichter beeinflussen.

Für Zugriff von unterwegs gibt es zwei saubere Wege:

| Weg | Für wen | Aufwand |
|---|---|---|
| **Tailscale** (VPN, verschlüsselt, keine Portfreigabe) | Solarmaa für Wartung; einzelne Kunden mit der Tailscale-App | klein, siehe [07-wartung-und-updates.md](07-wartung-und-updates.md). Mit `tailscale serve` gibt es dazu HTTPS. |
| **Solarmaa-Portal im Internet** (Pi schickt Daten an einen Solarmaa-Server, Kunden melden sich dort an) | alle Kunden, ohne App-Installation | eigenes Projekt: Server, HTTPS, Konten, Datenschutz. Noch nicht gebaut. |

## Was jeder im Netz des Kunden sieht

- **Ohne Anmeldung:** das Anlagen-Dashboard (Produktion, Verbrauch, Netz, Batterie, Abregelstatus). Auch Gäste im WLAN sehen das. Wer das nicht will, nutzt ein Gäste-WLAN oder verlangt künftig auch fürs Dashboard eine Anmeldung (noch nicht gebaut).
- **Mit Anmeldung:** Wohnungsdaten, Rechnungen, Berechtigte, Wartung, je nach Rolle.
- Die Anmeldung läuft über HTTP: Wer im selben Netz mitschneidet, kann Passwörter und Sitzungen lesen. Darum für `admin` ein eigenes, sonst nirgends verwendetes Passwort setzen.

## Behobene Befunde der Prüfung

**Abregelung (kritisch und hoch)**
- Nach Neustart oder Absturz während einer Abregelung blieben die Wechselrichter begrenzt. Jetzt merkt sich der Pi den Zustand und gibt bei positivem Preis sicher frei, auch nach einem Wechsel auf Modus `aus`.
- Eine fehlgeschlagene Freigabe wurde nie wiederholt. Jetzt bei jedem Schritt, mit Alarm.
- Gemischte 15- und 60-Minuten-Preise: Der Stundenpreis konnte negative Viertelstunden überdecken. Jetzt gewinnt die feinere Auflösung.
- Die Grenze konnte bei Netzbezug weit über die Produktion hinauslaufen, danach dauerte es Minuten, bis sie wieder griff. Jetzt folgt sie der Produktion.
- Fiel ein einzelner PV-Wechselrichter beim Lesen aus, galt die ganze Messung als ausgefallen. Jetzt läuft die Regelung weiter, solange der Netzzähler liefert.
- Zwischengespeicherte Werte (Cloud-Zähler) wurden mehrfach eingerechnet. Jetzt jeder Wert nur einmal. Für die Regelung trotzdem einen lokalen Zähler verwenden (whatwatt), keinen Cloud-Zähler.
- Das Freigaberegister des PIKO CI wurde blind überschrieben. Jetzt prüft der Pi es vor jedem Schreiben und fasst es nicht an, wenn eine andere Steuerung aktiv ist (z. B. «Digital Power Meter»).
- Updates werden nicht mehr gestartet, während abgeregelt wird.

**Server und System**
- Fehlerhafte Anfragen (negative Länge, falsches JSON) konnten Speicher füllen oder Abstürze im Protokoll auslösen. Jetzt sauber abgewiesen, mit Zeitlimit und Obergrenze für Live-Verbindungen.
- Das Update-Protokoll lag in einem Ordner des Webdienstes. Ein gekaperter Webdienst hätte darüber Root-Rechte erlangen können. Jetzt in `/var/log/solarmaa-dash` (gehört root).
- Schlägt die Installation eines Updates fehl, wird die alte Version wiederhergestellt. Die Tests vor dem Update laufen ohne Root-Rechte.
- Datenordner nur noch für den Dienst lesbar. Gesamtwerte schrumpfen nach 800 Tagen nicht mehr. Preis- und Alarm-Hintergrunddienste können nicht mehr still abstürzen. Testfenster werden beim Start geprüft und funktionieren über Mitternacht.

## Bewusst offen: bitte entscheiden

1. **Update-Kette.** Wer in den Update-Branch des GitHub-Repos schreiben kann, bekommt beim nächsten Update Root-Rechte auf jedem Pi, und damit Zugriff auf die Wechselrichter-Steuerung. Empfehlung:
   - Repository auf **privat** stellen, Zwei-Faktor-Anmeldung für das GitHub-Konto.
   - Eigener, geschützter Branch `stabil` für Kundenanlagen (in `/etc/solarmaa-dash/update-branch`), in den nur geprüfte Stände kommen.
   - `--auto-update` bei Anlagen mit Abregelung **nicht** einschalten, Updates dort von Hand auslösen.
2. **Demo-Modus.** `[haus] demo = true` legt Benutzer mit bekanntem Passwort an. Nur für Vorführgeräte, nie beim Kunden.
3. **ntfy-Thema.** Auf dem öffentlichen Server ntfy.sh kann jeder mitlesen, der den Themennamen kennt. Langen, zufälligen Namen wählen oder einen eigenen ntfy-Server mit Zugangsschutz betreiben.
4. **Was der Wechselrichter tut, wenn der Pi ganz ausfällt** (Strom weg während Abregelung), bestimmt der PIKO CI selbst. Mit Kostal klären, ob die Vorgabe dann bestehen bleibt, und das Lebenszeichen (healthchecks.io) einrichten, damit ein Pi-Ausfall auffällt.

## Nicht geprüft

- Installation und Update auf einem echten Raspberry Pi (`install.sh`, systemd-Einheiten).
- Alle Geräte-Adapter gegen echte Hardware.
- Die Register des PIKO CI und des Huawei-Speichers.

## Bedienung am Handy

Geprüft auf iPhone-Grösse, hell und dunkel: kein seitliches Schieben, alle Knöpfe mindestens 44 Pixel hoch, Rechnungen als Karten, Zähler in der Hausskizze mit grosser Tippfläche. Beide Seiten lassen sich zum Startbildschirm hinzufügen. Das Aussehen als App mit eigenem Symbol funktioniert unter HTTP; echter Offline-Betrieb ist nicht vorgesehen, die App braucht die Verbindung zum Pi.
