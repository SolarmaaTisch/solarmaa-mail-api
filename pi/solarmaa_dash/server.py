"""HTTP-Server: liefert die App aus und stellt die JSON-API bereit.

Endpunkte
  GET /api/info                      Anlagen- und Installateurdaten
  GET /api/now                       aktueller Messwert + Tagessummen
  GET /api/stream                    Live-Werte als Server-Sent Events
  GET /api/history?range=day&date=YYYY-MM-DD
  GET /api/history?range=week|month&end=YYYY-MM-DD
  GET /api/history?range=year&year=YYYY
  GET /api/health                    Status für Monitoring
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import mimetypes
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .sources import Snapshot
from .store import Store

log = logging.getLogger("solarmaa_dash")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("font/woff2", ".woff2")


class State:
    """Geteilter Zustand zwischen Poller und HTTP-Threads."""

    def __init__(self, cfg: dict, store: Store, source_name: str):
        self.cfg = cfg
        self.store = store
        self.source_name = source_name
        self.latest: Snapshot | None = None
        self.error: str | None = None
        self.last_ok: float | None = None
        self.cond = threading.Condition()
        self.started = time.time()

    def publish(self, snap: Snapshot | None, error: str | None = None) -> None:
        with self.cond:
            if snap is not None:
                self.latest = snap
                self.last_ok = snap.ts
                self.error = None
            else:
                self.error = error
            self.cond.notify_all()

    def now_payload(self) -> dict:
        interval = float(self.cfg["quelle"]["intervall_s"])
        stale = self.last_ok is None or time.time() - self.last_ok > max(30.0, 6 * interval)
        return {
            "live": None if self.latest is None else self.latest.to_dict(),
            "heute": self.store.today(),
            "status": "fehler" if stale else "ok",
            "fehler": self.error if stale else None,
            "demo": self.source_name == "demo",
        }

    def info_payload(self) -> dict:
        a, i, au = self.cfg["anlage"], self.cfg["installateur"], self.cfg["auswertung"]
        return {
            "anlage": a,
            "installateur": i,
            "auswertung": {k: v for k, v in au.items() if v not in (None, "")},
            "quelle": self.source_name,
            "demo": self.source_name == "demo",
            "gesamt": self.store.totals(),
        }


def history_payload(store: Store, q: dict) -> dict:
    rng = q.get("range", "day")
    today = dt.date.today()

    def parse_date(s: str | None) -> dt.date:
        if not s:
            return today
        return dt.date.fromisoformat(s)

    if rng == "day":
        day = parse_date(q.get("date"))
        return {"range": "day", "date": day.isoformat(),
                "fields": ["ts", "pv_w", "load_w", "grid_w", "battery_w", "soc"],
                "rows": store.day_series(day.isoformat())}
    if rng in ("week", "month"):
        end = parse_date(q.get("end"))
        start = end - dt.timedelta(days=6 if rng == "week" else 29)
        return {"range": rng, "start": start.isoformat(), "end": end.isoformat(),
                "rows": store.days(start.isoformat(), end.isoformat())}
    if rng == "year":
        year = int(q.get("year") or today.year)
        return {"range": "year", "year": year, "rows": store.months(year)}
    raise ValueError("range muss day, week, month oder year sein")


def make_handler(state: State, app_dir: Path):
    app_root = app_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "SolarmaaDash/1.0"

        def log_message(self, fmt, *args):  # leiser als Standard
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _json(self, payload, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/api/now":
                    return self._json(state.now_payload())
                if url.path == "/api/info":
                    return self._json(state.info_payload())
                if url.path == "/api/history":
                    return self._json(history_payload(state.store, q))
                if url.path == "/api/health":
                    p = state.now_payload()
                    return self._json({"status": p["status"], "fehler": p["fehler"],
                                       "uptime_s": int(time.time() - state.started)},
                                      200 if p["status"] == "ok" else 503)
                if url.path == "/api/stream":
                    return self._stream()
                if url.path.startswith("/api/"):
                    return self._json({"fehler": "Unbekannter Endpunkt"}, 404)
                return self._static(url.path)
            except ValueError as exc:
                return self._json({"fehler": str(exc)}, 400)
            except (BrokenPipeError, ConnectionResetError):
                return None

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            last = None
            while True:
                with state.cond:
                    state.cond.wait(timeout=15)
                    snap = state.latest
                try:
                    if snap is not None and snap is not last:
                        data = json.dumps(state.now_payload(), ensure_ascii=False)
                        self.wfile.write(f"event: now\ndata: {data}\n\n".encode())
                        last = snap
                    else:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        def _static(self, path: str) -> None:
            rel = path.lstrip("/") or "index.html"
            target = (app_root / rel).resolve()
            if app_root not in target.parents and target != app_root:
                return self._json({"fehler": "Nicht erlaubt"}, 403)
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file():
                target = app_root / "index.html"  # App kümmert sich um Pfade
            body = target.read_bytes()
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
            self.send_header("Content-Length", str(len(body)))
            cache = "no-cache" if target.suffix in (".html", ".js", ".webmanifest") else "max-age=86400"
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(state: State, app_dir: Path, host: str, port: int) -> Server:
    httpd = Server((host, port), make_handler(state, app_dir))
    threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
    return httpd
