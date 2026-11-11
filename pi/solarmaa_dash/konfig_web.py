"""Konfiguration im Browser bearbeiten (nur Rolle admin).

- Geheime Werte (Passwörter, Tokens, API-Keys) werden beim Anzeigen durch ******** ersetzt und
  beim Speichern wieder eingesetzt, wenn sie unverändert sind. So wandern sie nicht unnötig
  durchs Netz.
- Vor dem Speichern wird die neue Konfiguration vollständig geprüft (Geräte, Haus, Abregelung).
  Fehler werden angezeigt, gespeichert wird nur eine gültige Datei.
- Die vorherige Fassung wird gesichert (die letzten 10 unter <Datenordner>/config-sicherung).
- Danach startet der Dienst neu (systemd), damit die Änderung gilt.
"""
from __future__ import annotations

import copy
import os
import re
import signal
import tempfile
import threading
import time
from pathlib import Path

from . import config as config_mod

MASKE = "********"
GEHEIM = re.compile(r'^(\s*(?:passwort|api_key|token|entsoe_token)\s*=\s*)"([^"]*)"(.*)$')
TABELLE = re.compile(r"^\s*\[\[?[^\]]+\]\]?\s*(#.*)?$")


def _geheimnisse(text: str):
    """Liefert (zeilenindex, schluessel, wert) für jede geheime Zeile. Schlüssel = Abschnitt + Nr. + Name."""
    zaehler: dict[str, int] = {}
    abschnitt = ""
    for i, z in enumerate(text.split("\n")):
        if TABELLE.match(z):
            kopf = z.split("#")[0].strip()
            zaehler[kopf] = zaehler.get(kopf, 0) + 1
            abschnitt = f"{kopf}#{zaehler[kopf]}"
            continue
        m = GEHEIM.match(z)
        if m:
            yield i, f"{abschnitt}|{m.group(1).split('=')[0].strip()}", m


def maskieren(text: str) -> str:
    zeilen = text.split("\n")
    for i, _k, m in _geheimnisse(text):
        if m.group(2):
            zeilen[i] = f'{m.group(1)}"{MASKE}"{m.group(3)}'
    return "\n".join(zeilen)


def demaskieren(neu: str, alt: str) -> str:
    alte = {k: m.group(2) for _i, k, m in _geheimnisse(alt)}
    zeilen = neu.split("\n")
    for i, k, m in _geheimnisse(neu):
        if m.group(2) == MASKE:
            if k not in alte:
                raise ValueError(f"Zeile {i + 1}: Platzhalter {MASKE} ohne gespeicherten Wert. Bitte den echten Wert eintragen.")
            zeilen[i] = f'{m.group(1)}"{alte[k]}"{m.group(3)}'
    return "\n".join(zeilen)


def pruefen(text: str) -> dict:
    """Lädt die Konfiguration probeweise und baut alle Teile auf, ohne Geräte anzusprechen."""
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        tmp = fh.name
    try:
        cfg = config_mod.load(tmp)
    finally:
        os.unlink(tmp)
    probe = copy.deepcopy(cfg)
    probe["daten"]["pfad"] = ":memory:"
    from .sources import make_source
    from .store import Store
    make_source(probe)
    store = Store(":memory:")
    if probe["haus"].get("aktiv"):
        from .haus import Haus
        Haus(probe, store)
    if probe["abregelung"].get("modus", "aus") != "aus" or probe["abregelung"].get("wechselrichter"):
        from .abregelung import Abregelung
        from .preise import Preise
        Abregelung(probe, Preise(probe["abregelung"], None), store)
    from .portal import Portal
    Portal(probe)
    return cfg


class KonfigWeb:
    def __init__(self, pfad: str | None, daten_dir: Path | None):
        self.pfad = Path(pfad) if pfad else None
        self.sicherung = (daten_dir / "config-sicherung") if daten_dir else None

    def lesen(self) -> dict:
        if self.pfad is None or not self.pfad.exists():
            raise ValueError("Der Dienst läuft ohne Konfigurationsdatei (Demo). Bearbeiten ist hier nicht möglich.")
        return {"text": maskieren(self.pfad.read_text(encoding="utf-8")), "pfad": str(self.pfad),
                "schreibbar": os.access(self.pfad, os.W_OK)}

    def speichern(self, text: str, von: str) -> None:
        if self.pfad is None:
            raise ValueError("Keine Konfigurationsdatei vorhanden")
        if not os.access(self.pfad, os.W_OK):
            raise ValueError("Die Konfigurationsdatei ist für den Dienst nicht schreibbar. "
                             "Einmal «Update installieren» ausführen, dann erneut versuchen.")
        if len(text) > 200_000:
            raise ValueError("Konfiguration zu gross")
        alt = self.pfad.read_text(encoding="utf-8")
        neu = demaskieren(text.replace("\r\n", "\n"), alt)
        try:
            pruefen(neu)
        except Exception as e:  # noqa: BLE001 – jede Ursache dem Benutzer zeigen
            raise ValueError(f"Konfiguration ungültig, nichts gespeichert: {e}") from e
        if self.sicherung is not None:
            self.sicherung.mkdir(mode=0o750, exist_ok=True)
            (self.sicherung / f"config-{time.strftime('%Y%m%d-%H%M%S')}-{von}.toml").write_text(alt, encoding="utf-8")
            for alt_datei in sorted(self.sicherung.glob("config-*.toml"))[:-10]:
                alt_datei.unlink()
        with open(self.pfad, "w", encoding="utf-8") as fh:   # an Ort und Stelle (Datei gehört root:Dienst 660)
            fh.write(neu)

    @staticmethod
    def neustart(verzoegerung_s: float = 1.5) -> None:
        """Dienst sauber beenden; systemd (Restart=always) startet ihn mit der neuen Konfiguration."""
        threading.Timer(verzoegerung_s, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
