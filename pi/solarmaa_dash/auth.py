"""Benutzer, Rollen und Sitzungen.

Rollen:
  admin       Solarmaa: sieht alles, importiert Rechnungen, verwaltet Status
  verwaltung  Liegenschaftsverwaltung / Eigentümer: sieht alles, setzt Rechnungsstatus
  mieter      sieht nur die eigene Einheit (Verbrauch, Solaranteil, eigene Rechnungen)

Passwörter: PBKDF2-HMAC-SHA256 mit Salz. Sitzungen: zufälliges Token im Cookie,
in der Datenbank nur als SHA-256-Hash gespeichert.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time

ROLLEN = ("admin", "verwaltung", "mieter")
ITER = 200_000
SITZUNG_S = 30 * 86400
COOKIE = "sm_sitzung"


def hash_pw(pw: str, salt: bytes | None = None, it: int = ITER) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, it)
    return f"pbkdf2_sha256${it}${salt.hex()}${dk.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _algo, it, salt, dk = stored.split("$")
        test = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(test.hex(), dk)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_pw(secrets.token_hex(16))


class Auth:
    def __init__(self, store):
        self.store = store
        self.fails: dict[str, list[float]] = {}
        self.flock = threading.Lock()
        with store.lock:
            store.db.execute(
                "CREATE TABLE IF NOT EXISTS benutzer (name TEXT PRIMARY KEY, anzeigename TEXT, pw_hash TEXT NOT NULL, "
                "rolle TEXT NOT NULL, einheit TEXT, erstellt INTEGER, letzter_login INTEGER)")
            store.db.execute(
                "CREATE TABLE IF NOT EXISTS sitzungen (token_hash TEXT PRIMARY KEY, benutzer TEXT NOT NULL, ablauf INTEGER)")
            store.db.commit()

    # -- Verwaltung -------------------------------------------------------
    def anlegen(self, name: str, pw: str, rolle: str, einheit: str | None = None, anzeigename: str = "") -> None:
        if rolle not in ROLLEN:
            raise ValueError(f"Rolle muss eine von {', '.join(ROLLEN)} sein")
        if rolle == "mieter" and not einheit:
            raise ValueError("Mieter brauchen eine Einheit (--einheit)")
        if not re.fullmatch(r"[A-Za-z0-9._@-]{1,40}", name or ""):
            raise ValueError("Benutzername: höchstens 40 Zeichen, nur Buchstaben, Ziffern und . _ @ -")
        if len(pw) < 10:
            raise ValueError("Passwort muss mindestens 10 Zeichen haben")
        with self.store.lock:
            self.store.db.execute(
                "INSERT INTO benutzer (name, anzeigename, pw_hash, rolle, einheit, erstellt) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET anzeigename=excluded.anzeigename, pw_hash=excluded.pw_hash, "
                "rolle=excluded.rolle, einheit=excluded.einheit",
                (name, anzeigename or name, hash_pw(pw), rolle, einheit if rolle == "mieter" else None, int(time.time())))
            self.store.db.execute("DELETE FROM sitzungen WHERE benutzer = ?", (name,))
            self.store.db.commit()

    def loeschen(self, name: str) -> bool:
        with self.store.lock:
            cur = self.store.db.execute("DELETE FROM benutzer WHERE name = ?", (name,))
            self.store.db.execute("DELETE FROM sitzungen WHERE benutzer = ?", (name,))
            self.store.db.commit()
            return cur.rowcount > 0

    def liste(self) -> list[dict]:
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT name, anzeigename, rolle, einheit, letzter_login FROM benutzer ORDER BY rolle, name").fetchall()
        return [dict(zip(("name", "anzeigename", "rolle", "einheit", "letzter_login"), r)) for r in rows]

    def admins(self) -> int:
        return sum(1 for b in self.liste() if b["rolle"] == "admin")

    # -- Ersteinrichtung: erster Admin über den Browser, geschützt durch einen Einmal-Code --------
    def einrichtung_offen(self) -> bool:
        return self.admins() == 0

    def einrichten(self, code: str, code_datei, name: str, pw: str) -> None:
        if not self.einrichtung_offen():
            raise ValueError("Die Ersteinrichtung ist bereits abgeschlossen")
        try:
            soll = code_datei.read_text().strip()
        except OSError:
            raise ValueError("Kein Einrichtungs-Code vorhanden. Installation erneut ausführen.") from None
        if not soll or not hmac.compare_digest(soll.replace("-", "").upper(), (code or "").replace("-", "").replace(" ", "").upper()):
            time.sleep(1.0)
            raise ValueError("Einrichtungs-Code stimmt nicht")
        self.anlegen(name, pw, "admin", anzeigename=name)
        try:
            code_datei.unlink()
        except OSError:
            pass

    def berechtigte(self, einheit: str) -> list[dict]:
        return [b for b in self.liste() if b["rolle"] in ("admin", "verwaltung") or b["einheit"] == einheit]

    # -- Login ------------------------------------------------------------
    def gesperrt(self, ip: str) -> bool:
        with self.flock:
            jetzt = time.time()
            self.fails[ip] = [t for t in self.fails.get(ip, []) if jetzt - t < 900]
            return len(self.fails[ip]) >= 10

    def login(self, name: str, pw: str, ip: str) -> str | None:
        if self.gesperrt(ip):
            return None
        with self.store.lock:
            r = self.store.db.execute("SELECT pw_hash FROM benutzer WHERE name = ?", (name,)).fetchone()
        ok = check_pw(pw, r[0] if r else _DUMMY_HASH)  # ein Hash in beiden Fällen: gleiche Laufzeit
        if not (r and ok):
            with self.flock:
                self.fails.setdefault(ip, []).append(time.time())
            time.sleep(0.8)
            return None
        token = secrets.token_urlsafe(32)
        jetzt = int(time.time())
        with self.store.lock:
            self.store.db.execute("DELETE FROM sitzungen WHERE ablauf < ?", (jetzt,))
            self.store.db.execute("INSERT INTO sitzungen VALUES (?, ?, ?)",
                                  (hashlib.sha256(token.encode()).hexdigest(), name, jetzt + SITZUNG_S))
            self.store.db.execute("UPDATE benutzer SET letzter_login = ? WHERE name = ?", (jetzt, name))
            self.store.db.commit()
        return token

    def benutzer(self, token: str | None) -> dict | None:
        if not token:
            return None
        th = hashlib.sha256(token.encode()).hexdigest()
        with self.store.lock:
            r = self.store.db.execute(
                "SELECT b.name, b.anzeigename, b.rolle, b.einheit FROM sitzungen s JOIN benutzer b ON b.name = s.benutzer "
                "WHERE s.token_hash = ? AND s.ablauf > ?", (th, int(time.time()))).fetchone()
        return dict(zip(("name", "anzeigename", "rolle", "einheit"), r)) if r else None

    def logout(self, token: str | None) -> None:
        if token:
            with self.store.lock:
                self.store.db.execute("DELETE FROM sitzungen WHERE token_hash = ?",
                                      (hashlib.sha256(token.encode()).hexdigest(),))
                self.store.db.commit()


def darf_alles(u: dict | None) -> bool:
    return bool(u) and u["rolle"] in ("admin", "verwaltung")


def darf_einheit(u: dict | None, einheit: str) -> bool:
    return darf_alles(u) or (bool(u) and u["rolle"] == "mieter" and u["einheit"] == einheit)
