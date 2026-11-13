# Entscheide und bekannte Lücken

Diese Liste lesen Planer und Prüfer vor jeder Arbeit. Ein hier aufgeführter Punkt ist **kein** neuer Befund, solange eine Änderung ihn nicht verschlechtert. Neue Abweichungen kommen nur mit Tins OK auf die Liste.

## Bewusste Entscheide

| Nr. | Entscheid | Grund | Seit |
|---|---|---|---|
| E1 | Ausfallverhalten der Abregelung ist pro Anlage wählbar: `halten` (Standard), `einspeisung`, `aus`. «Halten» lässt die letzte Grenze stehen, bis der Zähler zurück ist oder der Preis positiv wird. | Wunsch von Tin: nicht automatisch freigeben, dafür alarmieren (Lampe, Push, E-Mail). | 11.11.2026 |
| E2 | Tagessummen werden aus der Leistung integriert, nicht aus Zählerständen. | Funktioniert mit jedem Gerät, auch ohne Energiezähler-Register. Für Abrechnung ist smart-me massgebend, nicht der Pi. | 10.11.2026 |
| E3 | Das Anlagen-Dashboard auf dem Pi ist im Kundennetz ohne Anmeldung sichtbar; Wohnungsdaten, Rechnungen und Wartung nur mit Login. | Einfache Bedienung im Einfamilienhaus. | 10.11.2026 |
| E4 | Die App auf dem Pi läuft im Heimnetz über HTTP. Zugriff von aussen nur über das Portal (HTTPS) oder VPN, nie per Portfreigabe. | Kein Zertifikat für lokale Adressen. | 11.11.2026 |
| E5 | Solaranteil pro Wohnung wird live nach Verbrauch verteilt. | Anzeige, keine Abrechnung. | 10.11.2026 |
| E6 | Fernbefehle des Portals (Update, Konfiguration) nur von Solarmaa-Konten mit Zwei-Faktor-Anmeldung; der Pi lehnt sie während einer aktiven Abregelung ab. | Portal ist das lohnendste Angriffsziel. | 13.11.2026 |

## Bekannte Lücken (offen, als Aufgaben geführt)

| Nr. | Lücke | Risiko | Geplant |
|---|---|---|---|
| L1 | Updates sind nicht signiert: Wer auf `stabil` pushen kann, erhält Root-Rechte auf jedem Pi. | hoch | Repository privat, Zwei-Faktor bei GitHub, Branch-Schutz; Signaturprüfung als eigene Aufgabe |
| L2 | Das Portal prüft eingehende Messwerte nicht auf Wertebereich und Plausibilität. | mittel | Stufe 2 |
| L3 | Das Portal zeigt «offline», alarmiert Solarmaa aber nicht, wenn ein Pi ausbleibt. | hoch für den Betrieb | Stufe 2 (bis dahin `lebenszeichen_url`) |
| L4 | Geräte- und Cloud-Passwörter stehen im Klartext in `/etc/solarmaa-dash/config.toml` (Dateirechte 660, root und Dienst). | mittel | offen |
| L5 | Kein Geräteadapter und kein Steuerprofil ist an echter Hardware geprüft (`geprueft = false`). Register des PIKO CI stammen aus Auszügen. | hoch vor dem Feldtest | Feldtest in Stufen: Simulation, ein Wechselrichter, alle |
| L6 | Es gibt nur ein Supabase-Projekt (Produktion). Getestet wird gegen lokale Postgres-Instanz und Attrappe. | mittel | zweites Projekt für Tests |
| L7 | Der Demo-Modus (`[haus] demo = true`) legt Benutzer mit bekanntem Passwort an. | hoch, falls auf Kundenanlage | Installations-Checkliste |
