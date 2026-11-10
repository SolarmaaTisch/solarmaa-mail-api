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
        log = self.daten / "update.log"
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
