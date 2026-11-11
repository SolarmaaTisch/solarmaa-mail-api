"""Automatische Geräteerkennung im Kundennetz.

  python3 -m solarmaa_dash erkennen                 Netz des Pi absuchen (z. B. 192.168.1.0/24)
  python3 -m solarmaa_dash erkennen --netz 10.0.0.0/24 --toml vorschlag.toml

Ablauf pro Adresse:
  1. Port 80 offen  → Shelly (/shelly), myStrom (/report), go-e (/api/status),
                      Fronius Solar API (/solar_api/GetAPIVersion.cgi)
  2. Port 502 oder 1502 offen → SunSpec-Marker bei mehreren Unit-IDs; sonst Registerprofile mit
                      [erkennung]-Abschnitt (Huawei, GoodWe, Sigenergy, ...)
  3. smart-me: Zähler im Cloud-Konto (falls [smartme] eingerichtet)
  Easee-Wallboxen haben keine lokale Schnittstelle; sie werden über [easee] und charger_id eingetragen.

Ergebnis ist ein Vorschlag für die [[geraet]]-Blöcke. Die Rolle (PV, Netz, Speicher) lässt
sich bei frei montierbaren Messgeräten wie Shelly nicht sicher erkennen: Vorschlag prüfen.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import sunspec
from .geraete import GeraetFehler
from .geraete.http import get_json
from .geraete.modbus_profil import PROFIL_DIR
from .modbus import ModbusError, ModbusTcpClient

SUNSPEC_UNITS = (1, 2, 3, 71, 126, 200, 240, 247, 100)
MODBUS_PORTS = (502, 1502)  # 1502: Kostal Plenticore, häufig SolarEdge


def eigenes_netz() -> ipaddress.IPv4Network:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # sendet nichts, ermittelt nur die eigene Adresse
        ip = s.getsockname()[0]
    except OSError:
        ip = "192.168.1.2"
    finally:
        s.close()
    return ipaddress.ip_network(f"{ip}/24", strict=False)


def port_offen(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def regs_text(regs: list[int]) -> str:
    raw = b"".join(r.to_bytes(2, "big") for r in regs)
    return raw.split(b"\x00")[0].decode("ascii", errors="ignore").strip()


def sunspec_info(host: str, unit: int, port: int = 502) -> dict | None:
    c = ModbusTcpClient(host, port, unit, timeout=1.5)
    try:
        models = sunspec.discover(c)
        info = {"modelle": sorted(models)}
        if 1 in models:
            try:  # Gerätename ist nett, aber nicht nötig
                regs = c.read_many(models[1].start, 64)
                info.update(hersteller=regs_text(regs[0:16]), modell=regs_text(regs[16:32]),
                            seriennummer=regs_text(regs[48:64]))
            except (ModbusError, OSError):
                pass
        return info
    except (ModbusError, OSError):
        return None
    finally:
        c.close()


def profile_mit_erkennung() -> list[tuple[str, dict]]:
    out = []
    for p in sorted(PROFIL_DIR.glob("*.toml")):
        with open(p, "rb") as fh:
            prof = tomllib.load(fh)
        if prof.get("erkennung") and prof.get("werte"):
            out.append((p.stem, prof))
    return out


def profil_test(host: str, name: str, prof: dict, port: int = 502) -> dict | None:
    e = prof["erkennung"]
    for unit in e.get("units", [1]):
        c = ModbusTcpClient(host, port, int(unit), timeout=1.5)
        try:
            regs = c.read_holding(int(e["register"]), int(e.get("laenge", 1)))
            text = regs_text(regs) if e.get("typ", "text") == "text" else str(regs[0])
            if re.search(e.get("muster", "."), text):
                return {"profil": name, "unit": int(unit), "kennung": text, "geprueft": bool(prof.get("geprueft"))}
        except (ModbusError, OSError):
            pass
        finally:
            c.close()
    return None


def pruefe_host(host: str, profile: list) -> list[dict]:
    funde: list[dict] = []
    if port_offen(host, 80):
        try:
            j = get_json(f"http://{host}/shelly", timeout=2)
            if isinstance(j, dict) and ("gen" in j or "type" in j):
                gen = int(j.get("gen", 1) or 1)
                modell = j.get("model") or j.get("type") or "?"
                misst_3ph = any(x in str(modell).upper() for x in ("3EM", "SPEM-003", "S3EM"))
                funde.append({"host": host, "typ": "shelly", "modell": f"Shelly {modell} (Gen{gen})",
                              "seriennummer": j.get("mac", ""), "rolle": "netz" if misst_3ph else "pv",
                              "hinweis": "Rolle prüfen: Netzanschluss, PV-Abgang oder Einheit?"})
        except GeraetFehler:
            pass
        if not funde:
            try:
                j = get_json(f"http://{host}/report", timeout=2)
                if isinstance(j, dict) and "power" in j and "relay" in j:
                    funde.append({"host": host, "typ": "mystrom", "modell": "myStrom Switch", "rolle": "verbraucher",
                                  "hinweis": "Was hängt am Stecker? Name setzen; bei Balkonkraftwerk rolle = \"pv\""})
            except GeraetFehler:
                pass
        if not funde:
            try:
                j = get_json(f"http://{host}/api/status?filter=car,eto", timeout=2)
                if isinstance(j, dict) and "car" in j:
                    funde.append({"host": host, "typ": "goe", "modell": "go-e Charger", "rolle": "verbraucher",
                                  "art": "wallbox", "hinweis": ""})
            except GeraetFehler:
                pass
        if not funde:
            try:
                j = get_json(f"http://{host}/solar_api/GetAPIVersion.cgi", timeout=2)
                if isinstance(j, dict) and "APIVersion" in j:
                    funde.append({"host": host, "typ": "fronius", "modell": "Fronius (Solar API)",
                                  "rolle": "anlage", "hinweis": "Batterie-Vorzeichen bei Sonne prüfen"})
            except GeraetFehler:
                pass
    for port in MODBUS_PORTS:
        if not port_offen(host, port):
            continue
        vorher = len(funde)
        gefunden_units = set()
        for unit in SUNSPEC_UNITS:
            info = sunspec_info(host, unit, port)
            if not info:
                continue
            gefunden_units.add(unit)
            mods = info["modelle"]
            for rolle, ids in (("pv", sunspec.INVERTER_MODELS), ("netz", sunspec.METER_MODELS),
                               ("speicher", (sunspec.STORAGE_MODEL,))):
                if any(m in mods for m in ids):
                    funde.append({"host": host, "port": port, "typ": "sunspec", "unit": unit, "rolle": rolle,
                                  "modell": f"{info.get('hersteller', '')} {info.get('modell', '')}".strip() or "SunSpec",
                                  "seriennummer": info.get("seriennummer", ""),
                                  "hinweis": "Netz-Vorzeichen bei Sonne prüfen" if rolle == "netz" else ""})
        if not gefunden_units:
            for name, prof in profile:
                r = profil_test(host, name, prof, port)
                if r:
                    funde.append({"host": host, "port": port, "typ": "modbus_profil", "profil": name, "unit": r["unit"],
                                  "rolle": prof.get("rolle", "anlage"), "modell": prof.get("name", name),
                                  "seriennummer": r["kennung"],
                                  "hinweis": "" if r["geprueft"] else "Profil ungeprüft: Werte mit geraet-test vergleichen"})
                    break
        if len(funde) == vorher:
            funde.append({"host": host, "port": port, "typ": "unbekannt",
                          "modell": f"Modbus TCP offen (Port {port}), Gerät nicht erkannt",
                          "rolle": "", "hinweis": "Registerprofil anlegen (siehe profile/)"})
    return funde


def suchen(netz: str | None = None, smartme_cfg: dict | None = None, fortschritt=None) -> list[dict]:
    net = ipaddress.ip_network(netz, strict=False) if netz else eigenes_netz()
    if net.num_addresses > 1024:
        raise ValueError("Netz zu gross, höchstens /22")
    hosts = [str(h) for h in net.hosts()]
    profile = profile_mit_erkennung()
    funde: list[dict] = []
    with ThreadPoolExecutor(max_workers=48) as ex:
        for i, res in enumerate(ex.map(lambda h: pruefe_host(h, profile), hosts)):
            funde.extend(res)
            if fortschritt:
                fortschritt(i + 1, len(hosts))
    if smartme_cfg and (smartme_cfg.get("api_key") or smartme_cfg.get("benutzer")):
        from .geraete.smartme import konto, zu_messung
        try:
            for d in konto(smartme_cfg).geraete():
                m = zu_messung(d)
                funde.append({"host": "smart-me Cloud", "typ": "smartme_cloud", "modell": str(m.extra.get("name")),
                              "seriennummer": str(m.extra.get("serial")), "rolle": "einheit",
                              "hinweis": "Einer Einheit oder Rolle zuordnen"})
        except Exception as e:  # Cloud nicht erreichbar: lokale Funde trotzdem zeigen
            funde.append({"host": "smart-me Cloud", "typ": "fehler", "modell": str(e), "rolle": "", "hinweis": ""})
    return funde


def als_toml(funde: list[dict]) -> str:
    zeilen = ["# Vorschlag aus «erkennen». Rollen prüfen, nicht Benötigtes löschen,",
              "# dann in /etc/solarmaa-dash/config.toml übernehmen.", ""]
    for f in funde:
        if f["typ"] in ("unbekannt", "fehler") or f.get("rolle") == "einheit":
            continue
        zeilen += ["[[geraet]]", f'name = "{f["modell"]}"', f'rolle = "{f["rolle"]}"', f'typ = "{f["typ"]}"',
                   f'host = "{f["host"]}"']
        if f.get("port", 502) != 502:
            zeilen.append(f'port = {f["port"]}')
        if "unit" in f:
            zeilen.append(f'unit = {f["unit"]}')
        if f.get("profil"):
            zeilen.append(f'profil = "{f["profil"]}"')
        if f.get("art"):
            zeilen.append(f'art = "{f["art"]}"')
        if f.get("hinweis"):
            zeilen.append(f'# {f["hinweis"]}')
        zeilen.append("")
    einheiten = [f for f in funde if f.get("rolle") == "einheit"]
    if einheiten:
        zeilen.append("# smart-me-Zähler: unter [[haus.einheit.zaehler]] einer Einheit zuordnen:")
        for f in einheiten:
            zeilen.append(f'#   typ = "smartme_cloud", seriennummer = "{f["seriennummer"]}"   # {f["modell"]}')
    return "\n".join(zeilen) + "\n"


def drucken(funde: list[dict]) -> None:
    if not funde:
        print("Keine Geräte gefunden. Ist Modbus TCP am Wechselrichter aktiviert und der Pi im selben Netz?")
        return
    print(f"{'Adresse':<16} {'Typ':<14} {'Rolle':<9} {'Gerät':<34} Hinweis")
    for f in funde:
        print(f"{f['host']:<16} {f['typ']:<14} {f.get('rolle', ''):<9} {f['modell'][:34]:<34} {f.get('hinweis', '')}")


def schreibe(funde: list[dict], pfad: str) -> None:
    Path(pfad).write_text(als_toml(funde), encoding="utf-8")
