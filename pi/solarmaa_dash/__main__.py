"""Startpunkt.

  python -m solarmaa_dash run  [-c config.toml]   Dienst starten
  python -m solarmaa_dash scan HOST [--unit N]    SunSpec-Modelle eines Geräts anzeigen
  python -m solarmaa_dash read [-c config.toml]   einen Messwert lesen und ausgeben
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path

from . import config as config_mod
from . import sunspec
from .modbus import ModbusError, ModbusTcpClient
from .server import State, serve
from .sources import make_source
from .store import Store

log = logging.getLogger("solarmaa_dash")
DEFAULT_CONFIG = "/etc/solarmaa-dash/config.toml"


def find_app_dir(cfg: dict) -> Path:
    if cfg["server"].get("app_pfad"):
        return Path(cfg["server"]["app_pfad"])
    here = Path(__file__).resolve()
    for cand in (here.parent.parent.parent / "app", Path("/opt/solarmaa-dash/app")):
        if (cand / "index.html").is_file():
            return cand
    raise SystemExit("App-Verzeichnis nicht gefunden. server.app_pfad in der Konfiguration setzen.")


def load_cfg(path: str | None) -> dict:
    if path is None and Path(DEFAULT_CONFIG).is_file():
        path = DEFAULT_CONFIG
    return config_mod.load(path)


def poll_loop(state: State, source, stop: threading.Event) -> None:
    interval = float(state.cfg["quelle"]["intervall_s"])
    backoff = interval
    last_prune = 0.0
    while not stop.is_set():
        t0 = time.time()
        try:
            snap = source.read()
            state.store.add(snap)
            state.publish(snap)
            backoff = interval
        except Exception as exc:  # Gerät kurz weg, Netzwerk, ...
            msg = str(exc) or exc.__class__.__name__
            if state.error != msg:
                log.warning("Messwert konnte nicht gelesen werden: %s", msg)
            state.publish(None, msg)
            backoff = min(backoff * 2, 60.0)
        if t0 - last_prune > 6 * 3600:
            state.store.prune()
            last_prune = t0
        wait = interval if state.error is None else backoff
        stop.wait(max(0.5, wait - (time.time() - t0)))


def cmd_run(args) -> int:
    cfg = load_cfg(args.config)
    if args.db:
        cfg["daten"]["pfad"] = args.db
    store = Store(cfg["daten"]["pfad"], int(cfg["daten"]["aufbewahrung_tage"]))
    source = make_source(cfg)
    state = State(cfg, store, source.name)
    app_dir = find_app_dir(cfg)
    host, port = cfg["server"]["host"], int(args.port or cfg["server"]["port"])
    httpd = serve(state, app_dir, host, port)
    log.info("Solarmaa Dashboard läuft auf http://%s:%s (Quelle: %s)", host, port, source.name)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        poll_loop(state, source, stop)
    finally:
        store.flush()
        httpd.shutdown()
        log.info("Beendet.")
    return 0


def cmd_scan(args) -> int:
    client = ModbusTcpClient(args.host, args.port, args.unit)
    try:
        models = sunspec.discover(client)
    except ModbusError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    print(f"SunSpec-Gerät {args.host}:{args.port}, Unit {args.unit}")
    for line in sunspec.describe(models):
        print("  " + line)
    return 0


def cmd_read(args) -> int:
    cfg = load_cfg(args.config)
    source = make_source(cfg)
    try:
        snap = source.read()
    except Exception as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(snap.to_dict(), indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="solarmaa_dash")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Dienst starten")
    r.add_argument("-c", "--config")
    r.add_argument("-p", "--port", type=int)
    r.add_argument("--db", help="Pfad der Datenbank (überschreibt die Konfiguration)")
    s = sub.add_parser("scan", help="SunSpec-Modelle eines Geräts anzeigen")
    s.add_argument("host")
    s.add_argument("--port", type=int, default=502)
    s.add_argument("--unit", type=int, default=1)
    rd = sub.add_parser("read", help="einen Messwert lesen")
    rd.add_argument("-c", "--config")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return {"run": cmd_run, "scan": cmd_scan, "read": cmd_read}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
