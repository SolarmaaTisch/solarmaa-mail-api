# Wartung, Updates und Fernzugriff

## Wartung im Browser

In der Hausansicht (`/haus.html`) sieht ein Benutzer mit Rolle `admin` den Bereich **Wartung**:

- installierte Version und Stand des Codes
- **Update installieren** mit Bestätigung, danach das Update-Protokoll
- Status jedes Anlagen-Geräts und jedes Hauszählers (grün = liefert Daten)
- **Geräte im Netz suchen**: automatische Erkennung, mit Konfigurationsvorschlag zum Kopieren

Für Anlagen ohne Hausansicht reicht dafür `[haus] aktiv = false`: Die Wartung braucht nur einen `admin`-Benutzer.

## Alles im Browser, ohne Kommandozeile

Nach der Installation braucht es keine PowerShell und kein SSH mehr:

1. **Ersteinrichtung:** `http://<pi>/haus.html` öffnen → «Ersteinrichtung: Admin-Konto anlegen». Der Einrichtungs-Code steht am Ende der Installation (einmalig gültig).
2. **Konfiguration:** Wartung → «Konfiguration» → Laden, bearbeiten, «Prüfen und speichern». Passwörter und Tokens erscheinen als `********` und bleiben erhalten, wenn man sie nicht ändert. Ungültige Eingaben werden mit Begründung abgelehnt, die alte Fassung wird gesichert (letzte 10 unter `/var/lib/solarmaa-dash/config-sicherung`), danach startet der Dienst neu.
3. **Benutzer:** Wartung → «Benutzer»: anlegen, Passwort neu setzen, löschen.
4. **Updates, Geräteerkennung, Alarmtest:** ebenfalls in der Wartung.

Während einer Abregelung sind Konfigurationsänderung und Update gesperrt.

**Von fern:** Die Wartungsseite des Pi ist nur im Netz des Kunden erreichbar. Update und Konfiguration gehen zusätzlich über das Solarmaa-Portal, wenn die Fernwartung auf dem Pi eingeschaltet ist (nächster Abschnitt). Für alles andere (Benutzer, Geräteerkennung, Alarmtest, SSH) den Pi mit Tailscale verbinden (siehe unten).

## Fernwartung über das Portal

Ab Version 1.6.0, ausführlich in [13-portal.md](13-portal.md), Kapitel «Fernwartung».

- Einschalten nur vor Ort: in der Konfiguration `[portal] fernwartung = true`. Standard ist aus.
- Im Portal können Solarmaa-Konten mit Zwei-Faktor-Anmeldung dann **Update auslösen** und die **Konfiguration laden, ändern und senden**. Der Pi holt den Befehl bei seiner nächsten Meldung ab (etwa eine Minute), prüft ihn selbst und meldet das Ergebnis.
- Aus der Ferne nicht änderbar: `[portal]`, `[server]`, `[daten]` und der Demo-Modus.
- Während einer Abregelung lehnt der Pi Fernbefehle ab, gleich wie die lokale Wartung.
- Ein Update aus der Ferne läuft über denselben Weg wie der Knopf «Update installieren» (nächster Abschnitt); installiert wird der Stand des Branches in `/etc/solarmaa-dash/update-branch`.
- Der Pi merkt sich laufende Befehle und noch nicht übermittelte Quittungen in `/var/lib/solarmaa-dash/portal-befehle.json`. Jeder Fernbefehl steht im Protokoll des Dienstes (`journalctl -u solarmaa-dash`, Zeilen «Fernwartung: …»); die Wartungsseite zeigt ihn noch nicht an.

## Wie das Update funktioniert

1. `install.sh` legt eine Kopie des Git-Repositories unter `/opt/solarmaa-dash/src` an und merkt sich den Branch in `/etc/solarmaa-dash/update-branch`.
2. Ein Klick auf «Update installieren» legt die Datei `/var/lib/solarmaa-dash/update-anfrage` an. Der Webdienst selbst hat keine Root-Rechte.
3. systemd bemerkt die Datei (`solarmaa-dash-update.path`) und startet `/usr/local/sbin/solarmaa-update` als root.
4. Das Skript holt den neuesten Stand, **führt die Tests aus** und installiert nur, wenn sie bestehen. Sonst bleibt die alte Version aktiv. Alles steht im Update-Protokoll (`/var/log/solarmaa-dash/update.log`). Während einer Abregelung wird kein Update gestartet. Schlägt die Installation fehl, wird die alte Version wiederhergestellt.

Manuell per SSH: `sudo solarmaa-update`

Nächtliche automatische Prüfung (optional): `sudo ./pi/install.sh --auto-update`. Sie läuft um etwa 03:17 Uhr, mit Zufallsverzögerung, damit nicht alle Anlagen gleichzeitig laden.

### Zugang zum Repository

Solange das Repository öffentlich ist, holt der Pi Updates ohne Zugangsdaten. Wird es auf privat gestellt, braucht jeder Pi einen **Deploy-Key mit Nur-Lese-Recht**:

```bash
sudo ssh-keygen -t ed25519 -N "" -f /root/.ssh/solarmaa_deploy
sudo cat /root/.ssh/solarmaa_deploy.pub     # in GitHub: Repository → Settings → Deploy keys → Add (read-only)
printf 'Host github.com\n  IdentityFile /root/.ssh/solarmaa_deploy\n' | sudo tee -a /root/.ssh/config
sudo git -C /opt/solarmaa-dash/src remote set-url origin git@github.com:SolarmaaTisch/solarmaa-mail-api.git
sudo solarmaa-update
```

Ein Deploy-Key pro Pi lässt sich einzeln widerrufen, falls ein Gerät verloren geht.

## Fernzugriff ohne Portfreigabe

Damit Solarmaa vom Büro aus alle Anlagen erreicht (Wartungsseite, SSH), ohne beim Kunden Ports im Router zu öffnen, eignet sich ein Mesh-VPN wie **Tailscale**:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh --hostname solarmaa-muster
```

Danach ist der Pi im Solarmaa-Tailnet unter `http://solarmaa-muster` erreichbar. Wichtig:

- Den Kunden informieren und in den Vertrag aufnehmen (Fernwartungszugang).
- Im Tailscale-Adminbereich die Geräte mit Tags versehen und den Zugriff auf Solarmaa-Konten beschränken.
- Lizenz- und Kostenbedingungen von Tailscale für die Anzahl Geräte prüfen. Alternativen: WireGuard mit eigenem Server, Netbird, ZeroTier.

## Checkliste bei Problemen

| Problem | Prüfen |
|---|---|
| «Web-Update nicht eingerichtet» | `install.sh` mit neuer Version erneut ausführen |
| Update-Protokoll: «Repository nicht erreichbar» | Internet beim Kunden, Deploy-Key |
| Update-Protokoll: «Tests fehlgeschlagen» | Alte Version läuft weiter. Fehler im Protokoll an die Entwicklung |
| Portal: Befehl bleibt auf «wartet auf den Pi» | Pi offline, oder Fernwartung auf dem Pi aus (dann nimmt das Portal gar keine Befehle an). Nach einer Stunde verfällt der Befehl |
| Portal: Befehl bleibt auf «abgeholt, läuft» bzw. «keine Quittung» | Pi hat den Befehl, konnte aber noch nicht antworten (Update läuft, Internet weg, Strom weg). Die Quittung wird nachgeliefert, sobald der Pi wieder meldet. Vor Ort: Update-Protokoll und `portal-befehle.json` ansehen |
| Portal: Befehl «fehler: Abgelehnt …» | Begründung steht dabei: Abregelung aktiv oder Freigabe eines Wechselrichters steht noch aus (später erneut senden; bleibt es dabei, vor Ort die Abregelung prüfen), gesperrter Abschnitt, Konfiguration inzwischen geändert (im Portal neu laden), zu viele Befehle in einer Stunde |
| Portal: «Geheimnis neu eingeben» | Im geänderten Abschnitt steht ein verdecktes Passwort. Den echten Wert statt `********` eintragen |
| Portal zeigt keine Konfiguration | Fernwartung auf dem Pi aus, oder ein geheimer Wert steht in einer Form in der Datei, die nicht verdeckt werden kann (Warnung in `journalctl -u solarmaa-dash`) |
| Gerät rot in der Wartung | Meldung daneben; `python3 -m solarmaa_dash geraet-test` per SSH |
