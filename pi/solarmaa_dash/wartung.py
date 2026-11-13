"""Wartung über die Weboberfläche (nur Rolle admin).

Updates laufen nicht im Webdienst selbst (der hat keine Root-Rechte), sondern über eine
systemd-Path-Unit: Der Dienst legt die Datei update-anfrage an, systemd startet daraufhin
solarmaa-dash-update.service (als root), das /usr/local/sbin/solarmaa-update ausführt.
"""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from . import __version__

DATEN = Path("/var/lib/solarmaa-dash")
SRC = Path("/opt/solarmaa-dash/src")
UPDATE_LOG = Path("/var/log/solarmaa-dash/update.log")   # gehört root; der Dienst liest nur


class Wartung:
    def __init__(self, cfg: dict, daten: Path | None = None):
        self.cfg = cfg
        self.daten = daten or Path(cfg["daten"]["pfad"]).parent
        self.erkennung: dict = {"laeuft": False, "funde": None, "fortschritt": None, "fehler": None}
        self.lock = threading.Lock()

    def _git(self, *args) -> str:
        try:
            return subprocess.run(["git", "-c", "safe.directory=*", "-C", str(SRC), *args], capture_output=True, text=True,
                                  timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    def status(self) -> dict:
        log = UPDATE_LOG
        anfrage = self.daten / "update-anfrage"
        return {
            "version": __version__,
            "commit": self._git("rev-parse", "--short", "HEAD") or None,
            "stand": self._git("log", "-1", "--format=%cd", "--date=format:%d.%m.%Y %H:%M") or None,
            "update_wartet": anfrage.exists(),
            "update_log": log.read_text(encoding="utf-8", errors="replace")[-4000:] if log.exists() else "",
            "update_eingerichtet": Path("/etc/systemd/system/solarmaa-dash-update.path").exists(),
            "erkennung": {k: v for k, v in self.erkennung.items()},
        }

    def update_anfordern(self, von: str) -> None:
        if not Path("/etc/systemd/system/solarmaa-dash-update.path").exists():
            raise ValueError("Web-Update ist auf diesem Gerät nicht eingerichtet (install.sh erneut ausführen)")
        (self.daten / "update-anfrage").write_text(f"{von} {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    def update_ergebnis(self, seit: float) -> tuple[bool, str] | None:
        """Ergebnis des letzten Update-Laufs aus dem Protokoll von update.sh, sofern er nach «seit»
        begonnen hat. None = noch kein Lauf seither oder noch nicht fertig."""
        try:
            zeilen = UPDATE_LOG.read_text(encoding="utf-8", errors="replace")[-20000:].split("\n")
        except OSError:
            return None
        start = next((i for i in range(len(zeilen) - 1, -1, -1) if zeilen[i].startswith("=== Update ")), None)
        if start is None:
            return None
        try:        # Kopfzeile: === Update TT.MM.JJJJ HH:MM:SS, Branch … ===
            beginn = time.mktime(time.strptime(zeilen[start][11:30], "%d.%m.%Y %H:%M:%S"))
        except ValueError:
            return None
        if beginn < seit - 5:
            return None
        block = [z.strip() for z in zeilen[start + 1:] if z.strip()]
        for z in block:
            if z.startswith("FEHLER"):
                return False, z
        if block and block[-1].startswith(("OK:", "Bereits aktuell")):
            return True, block[-1]
        if block and block[-1].startswith("Verschoben"):
            return False, block[-1]
        return None

    def erkennung_starten(self) -> None:
        from . import erkennung
        with self.lock:
            if self.erkennung["laeuft"]:
                return
            self.erkennung = {"laeuft": True, "funde": None, "fortschritt": "0", "fehler": None}

        def lauf():
            try:
                funde = erkennung.suchen(None, self.cfg.get("smartme"),
                                         lambda i, n: self.erkennung.update(fortschritt=f"{i}/{n}"))
                self.erkennung.update(funde=funde, vorschlag=erkennung.als_toml(funde))
            except Exception as e:
                self.erkennung.update(fehler=str(e))
            finally:
                self.erkennung["laeuft"] = False

        threading.Thread(target=lauf, name="erkennung", daemon=True).start()
