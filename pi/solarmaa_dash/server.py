"""HTTP-Server: liefert die App aus und stellt die JSON-API bereit.

Öffentlich (im Heimnetz)
  GET  /api/info, /api/now, /api/stream, /api/history, /api/health
Mit Login (Cookie sm_sitzung)
  POST /api/login {benutzer, passwort}        POST /api/logout        GET /api/me
  GET  /api/haus                              Hausskizze: Einheiten, Zähler, Anlage
  GET  /api/einheit?id=...                    Details einer Einheit (Rechte je Rolle)
  GET  /api/rechnungen                        alle Rechnungen, nur admin/verwaltung
  POST /api/rechnungen/import   (CSV)         nur admin/verwaltung
  POST /api/rechnungen/status {nr, status}    nur admin/verwaltung
  GET  /api/benutzer                          nur admin/verwaltung
  GET  /api/wartung                           nur admin: Version, Update-Protokoll, Geräte, Erkennung
  POST /api/wartung/update                    nur admin: Update aus dem Repository anstossen
  POST /api/wartung/erkennen                  nur admin: Geräte im Netz suchen

POST-Anfragen brauchen den Header ``X-Solarmaa: 1`` (Schutz gegen fremde Webseiten).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import mimetypes
import socket
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .auth import COOKIE, SITZUNG_S, darf_alles, darf_einheit
from .sources import Snapshot
from .store import Store

log = logging.getLogger("solarmaa_dash")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("font/woff2", ".woff2")
MAX_BODY = 2 * 1024 * 1024
MAX_STREAMS = 40


class State:
    """Geteilter Zustand zwischen Poller und HTTP-Threads."""

    def __init__(self, cfg: dict, store: Store, source_name: str, source=None,
                 haus=None, auth=None, rechnungen=None, wartung=None):
        self.cfg = cfg
        self.store = store
        self.source_name = source_name
        self.source = source
        self.haus = haus
        self.auth = auth
        self.rechnungen = rechnungen
        self.wartung = wartung
        self.portal = None
        self.konfig = None           # KonfigWeb
        self.code_datei = None       # Einmal-Code für die Ersteinrichtung
        self.abregelung = None
        from .benachrichtigung import KeineAlarme
        self.alarme = KeineAlarme()
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
            "abregelung": self.abregelung_kurz(),
            "stoerung": any(a["stufe"] == "kritisch" for a in self.alarme.status()["offen"]),
        }

    def abregelung_kurz(self) -> dict | None:
        if self.abregelung is None:
            return None
        st = self.abregelung.status()
        return {k: st[k] for k in ("modus", "aktiv", "grund", "preis", "naechster_negativ")}

    def geraete_status(self) -> list[dict]:
        if self.source is None or not hasattr(self.source, "geraete"):
            return []
        return [{"name": g.get("name", g["typ"]), "rolle": g["rolle"], "typ": g["typ"],
                 "ok": self.source.status.get(g.get("name", g["typ"])) == "ok"} for g, _ in self.source.geraete]

    def info_payload(self) -> dict:
        a, i, au = self.cfg["anlage"], self.cfg["installateur"], self.cfg["auswertung"]
        return {
            "anlage": a,
            "installateur": i,
            "auswertung": {k: v for k, v in au.items() if v not in (None, "")},
            "quelle": self.source_name,
            "demo": self.source_name == "demo",
            "gesamt": self.store.totals(),
            "geraete": self.geraete_status(),
            "haus": {"aktiv": self.haus is not None, "name": self.haus.name if self.haus else None},
        }

    # -- Haus -------------------------------------------------------------
    def haus_payload(self, u: dict) -> dict:
        h = self.haus
        offen = self.rechnungen.offen_summe() if self.rechnungen else {}
        einheiten = []
        for e in h.einheiten:
            zugriff = darf_einheit(u, e["id"])
            d = {"id": e["id"], "name": e["name"], "geschoss": e["geschoss"], "typ": e["typ"],
                 "zugriff": zugriff, "anzahl_zaehler": len(e["zaehler"])}
            if zugriff:
                w = h.einheit_w(e)
                d.update({"w": None if w is None else round(w, 1),
                          "solar_w": round(getattr(h, "solar_verteilung", {}).get(e["id"], 0.0), 1),
                          "zaehler": [{"id": z, "name": h.zaehler[z]["name"],
                                       "ok": h.zaehler[z]["fehler"] is None and h.zaehler[z]["letzte"] is not None}
                                      for z in e["zaehler"]],
                          "offen_chf": offen.get(e["id"], 0.0)})
            einheiten.append(d)
        l = self.latest
        return {"name": h.name, "geschosse": h.geschosse, "demo": h.demo or self.source_name == "demo",
                "anlage": {"pv_w": l and l.pv_w, "grid_w": l and l.grid_w, "battery_w": l and l.battery_w,
                           "soc": l and l.soc, "soc_geschaetzt": bool(l and l.soc_geschaetzt),
                           "load_w": l and l.load_w,
                           "kwp": self.cfg["anlage"]["kwp"], "geraete": self.geraete_status()},
                "einheiten": einheiten, "ich": u}

    def einheit_payload(self, u: dict, eid: str) -> dict:
        h = self.haus
        e = h.einheit(eid)
        jahr = dt.date.today().year
        out = {"id": e["id"], "name": e["name"], "geschoss": e["geschoss"], "typ": e["typ"],
               "flaeche_m2": e.get("flaeche_m2"),
               "zaehler": [h.zaehler_info(z) for z in e["zaehler"]],
               "heute": h.heute(eid),
               "monate": h.monate(eid, jahr - 1) + h.monate(eid, jahr),
               "rechnungen": self.rechnungen.liste(eid) if self.rechnungen else []}
        if darf_alles(u):
            out["berechtigte"] = self.auth.berechtigte(eid)
        return out


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


class HttpFehler(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status = status


def make_handler(state: State, app_dir: Path):
    app_root = app_dir.resolve()
    stream_lock, stream_zahl = threading.Lock(), [0]

    class Handler(BaseHTTPRequestHandler):
        server_version = "SolarmaaDash/1.4"
        timeout = 30   # hängende Verbindungen nach 30 s schliessen

        def log_message(self, fmt, *args):  # leiser als Standard
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _json(self, payload, status: int = 200, cookie: str | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)

        # -- Hilfen -------------------------------------------------------
        def _token(self) -> str | None:
            raw = self.headers.get("Cookie")
            if not raw:
                return None
            c = SimpleCookie()
            try:
                c.load(raw)
            except Exception:
                return None
            return c[COOKIE].value if COOKIE in c else None

        def _user(self, pflicht: bool = True) -> dict | None:
            u = state.auth.benutzer(self._token()) if state.auth else None
            if pflicht and u is None:
                raise HttpFehler(401, "Bitte anmelden")
            return u

        def _admin(self) -> dict:
            u = self._user()
            if u["rolle"] != "admin" or state.wartung is None:
                raise HttpFehler(403, "Nur für Solarmaa (Rolle admin)")
            return u

        def _json_body(self) -> dict:
            d = json.loads(self._body() or b"{}")
            if not isinstance(d, dict):
                raise HttpFehler(400, "Ungültige Anfrage")
            return d

        def _need_haus(self):
            if state.haus is None:
                raise HttpFehler(404, "Für diese Anlage ist keine Hausansicht eingerichtet")

        def _body(self) -> bytes:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise HttpFehler(400, "Ungültige Anfrage") from None
            if n < 0:
                raise HttpFehler(400, "Ungültige Anfrage")
            if n > MAX_BODY:
                raise HttpFehler(413, "Datei zu gross (max. 2 MB)")
            return self.rfile.read(n) if n else b""

        # -- GET ----------------------------------------------------------
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
                if url.path == "/api/einrichtung":
                    return self._json({"offen": bool(state.auth and state.auth.einrichtung_offen())})
                if url.path == "/api/wartung/konfig":
                    self._admin()
                    return self._json(state.konfig.lesen())
                if url.path == "/api/me":
                    return self._json(self._user())
                if url.path == "/api/haus":
                    self._need_haus()
                    return self._json(state.haus_payload(self._user()))
                if url.path == "/api/einheit":
                    self._need_haus()
                    u = self._user()
                    eid = q.get("id", "")
                    if state.haus.einheit(eid) is None:
                        raise HttpFehler(404, "Einheit nicht gefunden")
                    if not darf_einheit(u, eid):
                        raise HttpFehler(403, "Keine Berechtigung für diese Einheit")
                    return self._json(state.einheit_payload(u, eid))
                if url.path == "/api/rechnungen":
                    self._need_haus()
                    if not darf_alles(self._user()):
                        raise HttpFehler(403, "Nur für Verwaltung und Solarmaa")
                    namen = {e["id"]: e["name"] for e in state.haus.einheiten}
                    return self._json([{**r, "einheit_name": namen.get(r["einheit"], r["einheit"])}
                                       for r in state.rechnungen.liste()])
                if url.path == "/api/benutzer":
                    if not darf_alles(self._user()):
                        raise HttpFehler(403, "Nur für Verwaltung und Solarmaa")
                    return self._json(state.auth.liste())
                if url.path == "/api/wartung":
                    self._admin()
                    abr = None
                    if state.abregelung:
                        jetzt = time.time()
                        with state.store.lock:
                            protokoll = [dict(zip(("ts", "ereignis", "preis", "grenze_w", "netz_w"), r)) for r in
                                         state.store.db.execute("SELECT * FROM abregel_log ORDER BY ts DESC LIMIT 50")]
                        abr = {**state.abregelung.status(), "protokoll": protokoll,
                               "preise": state.abregelung.preise.zeitreihe(jetzt - 3 * 3600, jetzt + 36 * 3600)}
                    return self._json({**state.wartung.status(), "abregelung": abr, "alarme": state.alarme.status(),
                                       "benutzer": state.auth.liste(),
                                       "portal": state.portal.status() if state.portal else None,
                                       "einheiten": [{"id": e["id"], "name": e["name"]} for e in state.haus.einheiten]
                                       if state.haus else [],
                                       "geraete": state.geraete_status(),
                                       "geraete_fehler": getattr(state.source, "status", {}),
                                       "haus_zaehler": [{**state.haus.zaehler_info(z), "name": f'{state.haus.einheit(zz["einheit"])["name"]}: {zz["name"]}'}
                                                        for z, zz in state.haus.zaehler.items()] if state.haus else []})
                if url.path.startswith("/api/"):
                    return self._json({"fehler": "Unbekannter Endpunkt"}, 404)
                return self._static(url.path)
            except HttpFehler as e:
                return self._json({"fehler": str(e)}, e.status)
            except ValueError as exc:
                return self._json({"fehler": str(exc)}, 400)
            except (BrokenPipeError, ConnectionResetError):
                return None

        # -- POST ---------------------------------------------------------
        def do_POST(self):  # noqa: N802
            url = urlparse(self.path)
            try:
                if self.headers.get("X-Solarmaa") != "1":
                    raise HttpFehler(403, "Anfrage abgelehnt")
                if state.auth is None:
                    raise HttpFehler(404, "Anmeldung ist nicht eingerichtet")
                if url.path == "/api/login":
                    d = self._json_body()
                    ip = self.client_address[0]
                    if state.auth.gesperrt(ip):
                        raise HttpFehler(429, "Zu viele Fehlversuche. Bitte in 15 Minuten erneut versuchen.")
                    tok = state.auth.login(str(d.get("benutzer", "")).strip(), str(d.get("passwort", "")), ip)
                    if not tok:
                        raise HttpFehler(401, "Benutzername oder Passwort falsch")
                    cookie = f"{COOKIE}={tok}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SITZUNG_S}"
                    return self._json(state.auth.benutzer(tok), cookie=cookie)
                if url.path == "/api/einrichtung":
                    d = self._json_body()
                    ip = self.client_address[0]
                    if state.auth.gesperrt(ip):
                        raise HttpFehler(429, "Zu viele Fehlversuche. Bitte in 15 Minuten erneut versuchen.")
                    try:
                        state.auth.einrichten(str(d.get("code", "")), state.code_datei,
                                              str(d.get("benutzer", "")).strip(), str(d.get("passwort", "")))
                    except ValueError:
                        with state.auth.flock:
                            state.auth.fails.setdefault(ip, []).append(time.time())
                        raise
                    return self._json({"ok": True})
                if url.path == "/api/wartung/konfig":
                    u = self._admin()
                    if state.abregelung and state.abregelung.aktiv:
                        raise HttpFehler(409, "Gerade wird abgeregelt. Konfiguration bitte danach ändern.")
                    state.konfig.speichern(str(self._json_body().get("text", "")), u["name"])
                    state.konfig.neustart()
                    return self._json({"ok": True, "hinweis": "Gespeichert. Der Dienst startet neu."})
                if url.path == "/api/benutzer":
                    u = self._admin()
                    d = self._json_body()
                    name = str(d.get("name", "")).strip()
                    if d.get("aktion") == "loeschen":
                        if name == u["name"]:
                            raise HttpFehler(400, "Das eigene Konto kann nicht gelöscht werden")
                        if not state.auth.loeschen(name):
                            raise HttpFehler(404, "Benutzer nicht gefunden")
                        return self._json({"ok": True})
                    rolle = str(d.get("rolle", "mieter"))
                    einheit = str(d.get("einheit", "")) or None
                    if rolle == "mieter" and (state.haus is None or state.haus.einheit(einheit or "") is None):
                        raise HttpFehler(400, "Für Mieter bitte eine vorhandene Einheit wählen")
                    if name == u["name"] and rolle != "admin":
                        raise HttpFehler(400, "Die eigene Rolle kann nicht herabgestuft werden")
                    state.auth.anlegen(name, str(d.get("passwort", "")), rolle, einheit, str(d.get("anzeigename", "")))
                    return self._json({"ok": True})
                if url.path == "/api/logout":
                    state.auth.logout(self._token())
                    return self._json({"ok": True}, cookie=f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
                if url.path == "/api/wartung/update":
                    u = self._admin()
                    if state.abregelung and state.abregelung.aktiv:
                        raise HttpFehler(409, "Gerade wird abgeregelt. Update bitte nach dem negativen Preisfenster starten.")
                    state.wartung.update_anfordern(u["name"])
                    return self._json({"ok": True, "hinweis": "Update gestartet. Der Dienst startet danach neu."})
                if url.path == "/api/wartung/alarmtest":
                    self._admin()
                    state.alarme.test()
                    return self._json({"ok": True})
                if url.path == "/api/wartung/erkennen":
                    self._admin()
                    state.wartung.erkennung_starten()
                    return self._json({"ok": True})
                u = self._user()
                if not darf_alles(u):
                    raise HttpFehler(403, "Nur für Verwaltung und Solarmaa")
                if state.rechnungen is None:
                    raise HttpFehler(404, "Für diese Anlage ist keine Hausansicht eingerichtet")
                if url.path == "/api/rechnungen/import":
                    text = self._body().decode("utf-8-sig", errors="replace")
                    return self._json(state.rechnungen.importieren(text, von=u["name"]))
                if url.path == "/api/rechnungen/status":
                    d = self._json_body()
                    if not state.rechnungen.status_setzen(str(d.get("nr", "")), str(d.get("status", "")), u["name"]):
                        raise HttpFehler(404, "Rechnung nicht gefunden")
                    return self._json({"ok": True})
                raise HttpFehler(404, "Unbekannter Endpunkt")
            except HttpFehler as e:
                return self._json({"fehler": str(e)}, e.status)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json({"fehler": str(exc)}, 400)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return None

        def _stream(self) -> None:
            with stream_lock:
                if stream_zahl[0] >= MAX_STREAMS:
                    return self._json({"fehler": "Zu viele gleichzeitige Verbindungen"}, 503)
                stream_zahl[0] += 1
            try:
                self._stream_lauf()
            finally:
                with stream_lock:
                    stream_zahl[0] -= 1

        def _stream_lauf(self) -> None:
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
            cache = "no-cache" if target.suffix in (".html", ".js", ".css", ".webmanifest") else "max-age=86400"
            self.send_header("Cache-Control", cache)
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class DualStackServer(Server):
    """Lauscht auf IPv4 und IPv6 zugleich (Namen wie pi.local lösen oft nach IPv6 auf)."""
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def serve(state: State, app_dir: Path, host: str, port: int) -> Server:
    if host in ("0.0.0.0", "::", ""):
        try:
            httpd = DualStackServer(("::", port), make_handler(state, app_dir))
        except OSError:   # IPv6 auf dem System abgeschaltet
            httpd = Server(("0.0.0.0", port), make_handler(state, app_dir))
    else:
        httpd = Server((host, port), make_handler(state, app_dir))
    threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
    return httpd
