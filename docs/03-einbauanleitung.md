# Einbauanleitung

Für Solarmaa-Monteure. Dauer vor Ort mit vorbereitetem Gerät: grob eine Stunde, abhängig vom Verteiler und von der Netzwerkverkabelung.

> **Sicherheit:** Arbeiten im Verteiler nur durch Personen mit entsprechender Berechtigung nach NIV. Die Änderung wie jede Erweiterung der Installation prüfen und dokumentieren (Mess- und Prüfprotokoll, Sicherheitsnachweis nach eurem internen Ablauf).

## A. In der Werkstatt vorbereiten

1. SD-Karte mit **Raspberry Pi OS Lite (64-bit)** beschreiben, mit dem Raspberry Pi Imager. In den Imager-Einstellungen setzen:
   - Hostname: `solarmaa-<kundenname>` (z. B. `solarmaa-muster`)
   - Benutzer und Passwort (im Solarmaa-Passwortmanager ablegen, nicht beim Kunden)
   - SSH aktivieren
   - Zeitzone Europe/Zurich, Tastatur ch
2. Pi mit LAN in der Werkstatt starten und per SSH verbinden: `ssh <benutzer>@solarmaa-muster.local`
3. Software installieren:
   ```bash
   sudo apt-get install -y git
   git clone https://github.com/SolarmaaTisch/solarmaa-mail-api.git
   cd solarmaa-mail-api
   sudo ./pi/install.sh
   ```
   Ist das Repository öffentlich, braucht es dafür kein GitHub-Konto auf dem Pi. Wird es auf privat gestellt, braucht der Pi einen Zugang (siehe [Wartung](07-wartung-und-updates.md)) oder man kopiert den Ordner vom Laptop: `scp -r solarmaa-mail-api <benutzer>@solarmaa-muster.local:~`
4. Konfiguration für den Kunden ausfüllen: `sudo nano /etc/solarmaa-dash/config.toml`
   - `[anlage]`: Name, kWp, Datum der Inbetriebnahme
   - `[installateur]`: Telefon und E-Mail von Solarmaa
   - `[quelle] typ = "demo"` lassen, bis vor Ort alles läuft
5. `sudo systemctl restart solarmaa-dash` und im Browser `http://solarmaa-muster.local` öffnen. Die App muss mit Demo-Band laufen.
6. QR-Code-Etikett drucken mit der Adresse `http://solarmaa-muster.local` (und, sobald bekannt, der festen IP).
7. Pi ins Hutschienengehäuse, Netzteil auf 5.1 V einstellen und kontrollieren, Gerät beschriften.

## B. Vor Ort einbauen

1. **Platz bestimmen:** Verteiler oder Multimediafeld mit LAN-Anschluss in der Nähe, freie Hutschiene, Belüftung.
2. **Fünf Sicherheitsregeln** anwenden: freischalten, gegen Wiedereinschalten sichern, Spannungsfreiheit prüfen, erden und kurzschliessen (wo vorgeschrieben), benachbarte unter Spannung stehende Teile abdecken.
3. **Q1 und T1 setzen** und nach [Bauplan](02-bauplan.md) verdrahten. Kleinspannung und 230 V getrennt führen.
4. **Pi-Gehäuse setzen**, USB-C-Speisung von T1, LAN-Kabel einstecken.
5. **Prüfen:** Isolations- und Schutzleitermessung nach NIN für den geänderten Stromkreis, Ergebnis ins Protokoll.
6. **Einschalten.** Am Pi leuchtet die rote LED (Speisung), die grüne flackert beim Starten.
7. Abdeckung montieren.

## C. Mit der Anlage verbinden

Siehe [04-inbetriebnahme.md](04-inbetriebnahme.md), Schritte 1–8. Kurzfassung:

1. Modbus TCP / SunSpec am Wechselrichter aktivieren.
2. Im Kundenrouter feste Adressen (DHCP-Reservation) für Pi und Wechselrichter.
3. `scan` ausführen, Unit-IDs in die Konfiguration, `typ = "sunspec"`.
4. Vorzeichen des Netzzählers prüfen.
5. App am Handy des Kunden öffnen und zum Startbildschirm hinzufügen.

## D. Übergabe an den Kunden

- QR-Code-Etikett gut sichtbar anbringen (z. B. innen an der Verteilertür oder beim Router).
- Mit dem Kunden die App einmal durchgehen: Energiefluss, Heute, Verlauf. Die [Kurzanleitung](05-kunden-kurzanleitung.md) abgeben.
- Erklären: Die App funktioniert nur im WLAN zu Hause. Der Pi darf nicht ausgeschaltet werden, sonst fehlen Daten.
- Im Kundendossier vermerken: Hostname, IP-Adressen, Unit-IDs, Datum, Monteur.
