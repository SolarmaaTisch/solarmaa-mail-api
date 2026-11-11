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

**Von fern:** Die Wartung ist nur im Netz des Kunden erreichbar. Für den Zugriff aus dem Büro den Pi mit Tailscale verbinden (siehe unten); dann funktioniert alles oben genauso von überall.

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
| Gerät rot in der Wartung | Meldung daneben; `python3 -m solarmaa_dash geraet-test` per SSH |
