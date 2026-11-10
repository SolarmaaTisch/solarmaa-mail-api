"""Startpunkt.

  python -m solarmaa_dash run  [-c config.toml]         Dienst starten
  python -m solarmaa_dash geraet-test [-c config.toml]  alle Geräte und Zähler einmal lesen
  python -m solarmaa_dash erkennen [--toml vorschlag.toml]  Geräte im Netz automatisch finden
  python -m solarmaa_dash scan HOST [--unit N]          SunSpec-Modelle eines Geräts anzeigen
  python -m solarmaa_dash smartme-liste [-c ...]        alle Zähler im smart-me-Konto anzeigen
  python -m solarmaa_dash benutzer add NAME --rolle mieter --einheit eg-links
  python -m solarmaa_dash benutzer list | del NAME
  python -m solarmaa_dash rechnungen-import DATEI.csv   smart-me-Export einlesen
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path

from . import config as config_mod
from . import sunspec
from .auth import ROLLEN, Auth
from .haus import Haus
from .modbus import ModbusError, ModbusTcpClient
from .rechnungen import Rechnungen
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


def open_all(cfg: dict, db: str | None = None):
    if db:
        cfg["daten"]["pfad"] = db
    store = Store(cfg["daten"]["pfad"], int(cfg["daten"]["aufbewahrung_tage"]))
    haus = Haus(cfg, store) if cfg["haus"].get("aktiv") else None
    auth = Auth(store)
    rechnungen = Rechnungen(store, haus, cfg["haus"].get("rechnungen_csv")) if haus else None
    return store, haus, auth, rechnungen


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


def haus_loop(state: State, stop: threading.Event) -> None:
    while not stop.is_set():
        t0 = time.time()
        try:
            state.haus.tick(state.latest)
        except Exception:
            log.exception("Fehler beim Lesen der Hauszähler")
        stop.wait(max(1.0, state.haus.intervall - (time.time() - t0)))


def cmd_run(args) -> int:
    cfg = load_cfg(args.config)
    store, haus, auth, rechnungen = open_all(cfg, args.db)
    source = make_source(cfg)
    from .wartung import Wartung
    state = State(cfg, store, source.name, source, haus, auth, rechnungen, Wartung(cfg))
    if haus and haus.demo:
        haus.demo_verlauf()
        rechnungen.demo_fuellen()
        if not auth.liste():
            for name, rolle, ein in (("solarmaa", "admin", None), ("verwaltung", "verwaltung", None),
                                     ("mieter", "mieter", haus.einheiten[1]["id"])):
                auth.anlegen(name, "demo-passwort", rolle, ein, anzeigename=f"Demo {rolle}")
            log.info("Demo-Benutzer angelegt: solarmaa / verwaltung / mieter, Passwort demo-passwort")
    app_dir = find_app_dir(cfg)
    host, port = cfg["server"]["host"], int(args.port or cfg["server"]["port"])
    httpd = serve(state, app_dir, host, port)
    log.info("Solarmaa Dashboard läuft auf http://%s:%s (Quelle: %s%s)", host, port, source.name,
             f", Haus: {haus.name} mit {len(haus.zaehler)} Zählern" if haus else "")

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if haus:
        threading.Thread(target=haus_loop, args=(state, stop), name="haus", daemon=True).start()
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


def cmd_geraet_test(args) -> int:
    from .geraete import erstelle
    from .sources import geraete_aus_config
    cfg = load_cfg(args.config)
    fehler = 0
    gruppen = [("Anlage", g.get("name", g["typ"]), g, g["rolle"]) for g in
               (geraete_aus_config(cfg) if cfg["geraet"] or cfg["quelle"]["typ"] == "sunspec" else [])]
    for e in cfg["haus"].get("einheit", []) if cfg["haus"].get("aktiv") and not cfg["haus"].get("demo") else []:
        for z in e.get("zaehler", []):
            gruppen.append((f"Einheit {e['id']}", z.get("name", z.get("id", "?")), z, "einheit"))
    if not gruppen:
        print("Keine echten Geräte konfiguriert (Demo-Modus).")
        return 0
    for gruppe, name, g, rolle in gruppen:
        g = {**g, "_smartme": cfg.get("smartme", {})}
        try:
            m = erstelle(g, rolle).lesen()
            werte = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in vars(m).items()
                     if v not in (None, {}, [])}
            print(f"OK     {gruppe:<18} {name:<24} {g['typ']:<14} {werte}")
        except Exception as e:
            fehler += 1
            print(f"FEHLER {gruppe:<18} {name:<24} {g.get('typ', '?'):<14} {e}")
    return 1 if fehler else 0


def cmd_smartme(args) -> int:
    from .geraete.smartme import konto, zu_messung
    cfg = load_cfg(args.config)
    try:
        geraete = konto(cfg["smartme"]).geraete()
    except Exception as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    print(f"{len(geraete)} Zähler im smart-me-Konto:")
    for d in geraete:
        m = zu_messung(d)
        print(f"  {str(m.extra.get('serial')):<14} {str(m.extra.get('name'))[:30]:<30} id={m.extra.get('id')}  "
              f"P={'' if m.w is None else round(m.w)} W  Bezug={'' if m.import_wh is None else round(m.import_wh / 1000, 1)} kWh")
    return 0


def cmd_erkennen(args) -> int:
    from . import erkennung
    cfg = load_cfg(args.config)
    netz = args.netz or str(erkennung.eigenes_netz())
    print(f"Suche Geräte in {netz} … (dauert etwa eine Minute)")

    def fortschritt(i, n):
        if i % 32 == 0 or i == n:
            print(f"  {i}/{n} Adressen geprüft", flush=True)

    try:
        funde = erkennung.suchen(netz, cfg.get("smartme"), fortschritt)
    except ValueError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    print()
    erkennung.drucken(funde)
    if args.toml:
        erkennung.schreibe(funde, args.toml)
        print(f"\nVorschlag für die Konfiguration gespeichert: {args.toml}")
    else:
        print("\nMit --toml DATEI wird ein Konfigurationsvorschlag gespeichert.")
    return 0


def cmd_benutzer(args) -> int:
    cfg = load_cfg(args.config)
    _store, haus, auth, _r = open_all(cfg, args.db)
    if args.aktion == "list":
        for b in auth.liste():
            last = time.strftime("%d.%m.%Y %H:%M", time.localtime(b["letzter_login"])) if b["letzter_login"] else "nie"
            print(f"  {b['name']:<20} {b['rolle']:<11} {b['einheit'] or 'alle Einheiten':<16} letzter Login: {last}")
        return 0
    if not args.name:
        print("Name fehlt", file=sys.stderr)
        return 2
    if args.aktion == "del":
        print("gelöscht" if auth.loeschen(args.name) else "nicht gefunden")
        return 0
    if args.rolle == "mieter" and haus and haus.einheit(args.einheit or "") is None:
        ids = ", ".join(e["id"] for e in haus.einheiten)
        print(f"Einheit {args.einheit!r} unbekannt. Vorhanden: {ids}", file=sys.stderr)
        return 2
    pw = getpass.getpass("Passwort (mind. 10 Zeichen): ")
    if pw != getpass.getpass("Wiederholen: "):
        print("Passwörter stimmen nicht überein", file=sys.stderr)
        return 2
    try:
        auth.anlegen(args.name, pw, args.rolle, args.einheit, args.anzeigename or "")
    except ValueError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    print(f"Benutzer {args.name} ({args.rolle}) gespeichert.")
    return 0


def cmd_rechnungen(args) -> int:
    cfg = load_cfg(args.config)
    _s, haus, _a, rechnungen = open_all(cfg, args.db)
    if rechnungen is None:
        print("[haus] aktiv = true fehlt in der Konfiguration", file=sys.stderr)
        return 2
    try:
        res = rechnungen.importieren(Path(args.datei).read_text(encoding="utf-8-sig"), von="cli")
    except ValueError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    print(f"{res['neu']} neu, {res['aktualisiert']} aktualisiert")
    for r in res["nicht_zugeordnet"]:
        print(f"  nicht zugeordnet: {r}  (Einheit-ID, Name oder smartme_ordner in der Konfiguration prüfen)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="solarmaa_dash")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def with_cfg(sp, db=False):
        sp.add_argument("-c", "--config")
        if db:
            sp.add_argument("--db", help="Pfad der Datenbank (überschreibt die Konfiguration)")
        return sp

    r = with_cfg(sub.add_parser("run", help="Dienst starten"), db=True)
    r.add_argument("-p", "--port", type=int)
    s = sub.add_parser("scan", help="SunSpec-Modelle eines Geräts anzeigen")
    s.add_argument("host")
    s.add_argument("--port", type=int, default=502)
    s.add_argument("--unit", type=int, default=1)
    with_cfg(sub.add_parser("read", help="einen Anlagen-Messwert lesen"))
    with_cfg(sub.add_parser("geraet-test", help="alle Geräte und Zähler einmal lesen"))
    with_cfg(sub.add_parser("smartme-liste", help="Zähler im smart-me-Konto anzeigen"))
    er = with_cfg(sub.add_parser("erkennen", help="Geräte im Netz automatisch suchen"))
    er.add_argument("--netz", help="z. B. 192.168.1.0/24 (Standard: Netz des Pi)")
    er.add_argument("--toml", help="Konfigurationsvorschlag in diese Datei schreiben")
    b = with_cfg(sub.add_parser("benutzer", help="Benutzer verwalten"), db=True)
    b.add_argument("aktion", choices=("add", "list", "del"))
    b.add_argument("name", nargs="?")
    b.add_argument("--rolle", choices=ROLLEN, default="mieter")
    b.add_argument("--einheit")
    b.add_argument("--anzeigename")
    ri = with_cfg(sub.add_parser("rechnungen-import", help="smart-me-Rechnungsexport (CSV) einlesen"), db=True)
    ri.add_argument("datei")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return {"run": cmd_run, "scan": cmd_scan, "read": cmd_read, "geraet-test": cmd_geraet_test,
            "smartme-liste": cmd_smartme, "erkennen": cmd_erkennen, "benutzer": cmd_benutzer,
            "rechnungen-import": cmd_rechnungen}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
