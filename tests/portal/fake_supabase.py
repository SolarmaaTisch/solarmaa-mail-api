"""Attrappe für Supabase, nur für Tests: Anmeldung, Zwei-Faktor und ein Mini-PostgREST vor einer
lokalen Postgres-Datenbank. Die Zugriffsregeln (RLS, Rechte, Funktionen) sind die echten aus
schema.sql; nachgebildet ist nur, was Supabase davor schaltet.

  PGHOST=… PGPORT=… PGDATABASE=… python3 fake_supabase.py <portal/web> <port>

Dazu ein Mini-Storage (Hochladen, Abrufen, Löschen) in der Form der Supabase-Storage-API: Die Regeln
auf storage.objects sind die echten aus schema.sql, die Dateien liegen in einem Temp-Ordner.

Nicht nachgebildet: echte Passwörter und TOTP-Codes (Code ist immer 424242), signierte Tokens,
PostgREST und Storage im Detail. Bindet nur an 127.0.0.1. Inhalte werden nie protokolliert. Siehe README.md.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

WEB = Path(sys.argv[1]).resolve()
PORT = int(sys.argv[2])
PASSWORT = "test-passwort-1"          # gilt nur hier
TOTP_CODE = "424242"
USERS = {"chef@solarmaa.ch": "00000000-0000-0000-0000-000000000001",
         "kunde@example.ch": "00000000-0000-0000-0000-000000000002",
         "fremd@example.ch": "00000000-0000-0000-0000-000000000003"}
RPC = {"ist_admin": [], "hat_2fa": [],
       "anlage_anlegen": [("p_name", "text"), ("p_ort", "text"), ("p_kwp", "numeric")],
       "anlage_token_neu": [("p_anlage", "uuid")], "anlage_loeschen": [("p_anlage", "uuid")],
       "mitglied_setzen": [("p_anlage", "uuid"), ("p_email", "text"), ("p_entfernen", "boolean")],
       "befehl_anlegen": [("p_anlage", "uuid"), ("p_art", "text"), ("p_nutzlast", "jsonb")],
       "pi_melden": [("p_token", "text"), ("p_daten", "jsonb")],
       "pi_ergebnis": [("p_token", "text"), ("p_befehl", "uuid"), ("p_ok", "boolean"), ("p_text", "text")],
       "pi_konfig": [("p_token", "text"), ("p_text", "text")],
       "dokument_anlegen": [("p_anlage", "uuid"), ("p_titel", "text"), ("p_kategorie", "text"), ("p_dateiname", "text"),
                            ("p_typ", "text"), ("p_groesse", "bigint"), ("p_sichtbar", "boolean")],
       "dokument_fertig": [("p_id", "uuid")],
       "dokument_aendern": [("p_id", "uuid"), ("p_titel", "text"), ("p_kategorie", "text"), ("p_sichtbar", "boolean")],
       "dokument_loeschen_beginnen": [("p_id", "uuid")], "dokument_loeschen": [("p_id", "uuid")]}
VOID = {"anlage_loeschen", "pi_ergebnis", "pi_konfig", "dokument_aendern", "dokument_loeschen_beginnen", "dokument_loeschen"}
STORAGE = "/storage/v1/object/"
DATEIEN = Path(tempfile.mkdtemp(prefix="smstorage."))      # im TMPDIR von run.sh, wird dort weggeräumt
ID = re.compile(r"^[a-z_][a-z0-9_]*$")
FEHLER = re.compile(r"ERROR:\s+([0-9A-Z]{5}):\s*(.*)")
FAKTOREN: dict[str, list[dict]] = {}       # Konto -> Zwei-Faktor-Einträge (nur im Speicher)
SPERRE = threading.Lock()
QR = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 21 21"><rect width="21" height="21" fill="#fff"/>'
      '<path d="M1 1h7v7H1zM13 1h7v7h-7zM1 13h7v7H1zM10 10h2v2h-2zM14 14h3v3h-3z"/></svg>')
with open(WEB / "vercel.json", encoding="utf-8") as _fh:
    KOPFZEILEN = {h["key"]: h["value"] for h in json.load(_fh)["headers"][0]["headers"]}
# wie auf Vercel, nur die eigene Adresse statt *.supabase.co als erlaubtes Ziel
KOPFZEILEN["Content-Security-Policy"] = KOPFZEILEN["Content-Security-Policy"].replace(
    "connect-src 'self'", f"connect-src 'self' http://127.0.0.1:{PORT}")
KOPFZEILEN.pop("Strict-Transport-Security", None)      # hier nur HTTP


class DbFehler(Exception):
    def __init__(self, code: str, text: str):
        super().__init__(text)
        self.code, self.text = code, text

    def http(self, angemeldet: bool) -> int:
        """Zuordnung SQLSTATE -> HTTP wie in der PostgREST-Doku beschrieben (am Projekt ungeprüft)."""
        c = self.code
        if c == "42501":
            return 403 if angemeldet else 401
        if c.startswith("28"):
            return 403
        if c in ("42883", "42P01"):
            return 404
        if c.startswith(("55", "57", "XX")):
            return 500
        return 400


def q(v) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


def unb64(s: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))


def sitzung(uid: str, email: str, aal: str) -> dict:
    claims = {"sub": uid, "email": email, "aal": aal, "role": "authenticated", "exp": int(time.time()) + 3600}
    return {"access_token": f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}.x",
            "refresh_token": "ref." + b64({"sub": uid, "email": email, "aal": aal}),
            "token_type": "bearer", "expires_in": 3600, "user": {"id": uid, "email": email}}


def ablage(bucket: str, name: str) -> Path:
    return DATEIEN / hashlib.sha256(f"{bucket}/{name}".encode()).hexdigest()


def sql(claims: dict | None, stmt: str, als_dienst: bool = False) -> str:
    if als_dienst:
        pre = ""          # wie Supabase Storage intern (Bucket-Angaben lesen)
    elif claims:
        pre = f"select set_config('request.jwt.claims', {q(json.dumps(claims))}, false); set role authenticated; "
    else:
        pre = "set role anon; "
    r = subprocess.run(["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose", "-U", "postgres",
                        "-c", pre + stmt], capture_output=True, text=True)
    if r.returncode:
        m = FEHLER.search(r.stderr)
        raise DbFehler(m.group(1) if m else "XX000", m.group(2).strip() if m else r.stderr.strip()[:300])
    return r.stdout.strip().split("\n")[-1]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def out(self, code: int, body=None, ctype: str = "application/json", statisch: bool = False):
        data = b"" if body is None else body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        if statisch:
            for k, v in KOPFZEILEN.items():
                self.send_header(k, v)
        if code != 204:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def claims(self) -> dict | None:
        a = self.headers.get("Authorization", "")
        teile = a[7:].split(".") if a.startswith("Bearer ") else []
        if len(teile) != 3:
            return None           # z. B. der öffentliche Schlüssel des Pi: gilt als nicht angemeldet
        try:
            c = unb64(teile[1])
        except Exception:  # noqa: BLE001
            return None
        return c if c.get("sub") in USERS.values() else None

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            d = {}
        return d if isinstance(d, dict) else {}

    # ------------------------------------------------------------ GET
    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        c = self.claims()
        if u.path.startswith(STORAGE):
            return self.speicher_lesen(u, c)
        if u.path == "/auth/v1/user":
            if not c:
                return self.out(401, {"msg": "JWT fehlt"})
            with SPERRE:
                return self.out(200, {"id": c["sub"], "email": c["email"], "factors": [
                    {k: v for k, v in f.items() if k != "challenge"} for f in FAKTOREN.get(c["sub"], [])]})
        if u.path.startswith("/rest/v1/"):
            return self.rest(u, c)
        if u.path == "/config.js":
            return self.out(200, f'window.PORTAL = {{ url: "http://127.0.0.1:{PORT}", key: "test-key" }};'.encode(),
                            "text/javascript", statisch=True)
        f = (WEB / (u.path.lstrip("/") or "index.html")).resolve()
        if WEB in f.parents and f.is_file():
            typ = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
                   ".svg": "image/svg+xml"}.get(f.suffix, "text/plain")
            return self.out(200, f.read_bytes(), typ, statisch=True)
        self.out(404, {})

    def rest(self, u, c):
        if not c:
            return self.out(401, {"message": "JWT fehlt"})
        table = u.path[9:]
        params = dict(parse_qsl(u.query))
        try:
            cols = params.pop("select")
            order, limit = params.pop("order", None), params.pop("limit", None)
            if not ID.match(table) or not all(ID.match(x) for x in cols.split(",")):
                raise ValueError(table)
            where = []
            for k, v in params.items():
                op, val = v.split(".", 1)
                if not ID.match(k) or op not in ("eq", "gte"):
                    raise ValueError(k)
                where.append(f"{k} {'=' if op == 'eq' else '>='} {q(val)}")
            s = f"select {cols} from public.{table}" + (" where " + " and ".join(where) if where else "")
            if order:
                spalte, _, richtung = order.partition(".")
                if not ID.match(spalte) or richtung not in ("", "asc", "desc"):
                    raise ValueError(order)
                s += f" order by {spalte} {richtung}"
            if limit:
                s += f" limit {int(limit)}"
        except (KeyError, ValueError) as e:
            return self.out(400, {"message": f"Attrappe versteht die Abfrage nicht: {e}"})
        try:
            return self.out(200, sql(c, f"select coalesce(jsonb_agg(t), '[]'::jsonb) from ({s}) t").encode())
        except DbFehler as e:
            return self.out(e.http(True), {"code": e.code, "message": e.text})

    # ------------------------------------------------------------ POST / DELETE
    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path.startswith(STORAGE):
            return self.speicher_hochladen(u, self.claims())
        body = self.body()
        c = self.claims()
        if u.path == "/auth/v1/token":
            if "refresh_token" in body:
                try:
                    r = unb64(str(body["refresh_token"])[4:])
                    return self.out(200, sitzung(r["sub"], r["email"], r["aal"]))     # Stufe bleibt erhalten
                except Exception:  # noqa: BLE001
                    return self.out(400, {"error_description": "Invalid Refresh Token"})
            if body.get("email") in USERS and body.get("password") == PASSWORT:
                return self.out(200, sitzung(USERS[body["email"]], body["email"], "aal1"))
            return self.out(400, {"error_description": "Invalid login credentials"})
        if u.path in ("/auth/v1/logout", "/auth/v1/recover"):
            return self.out(204)
        if u.path.startswith("/auth/v1/factors"):
            return self.faktoren(u.path, body, c)
        if u.path.startswith("/rest/v1/rpc/"):
            fn = u.path[13:]
            if fn not in RPC:
                return self.out(404, {"code": "PGRST202", "message": f"Funktion {fn} nicht gefunden"})
            args = ", ".join(f"{k} => {q(json.dumps(body[k]) if t == 'jsonb' else body[k])}::{t}"
                             for k, t in RPC[fn] if k in body and body[k] is not None)
            try:
                if fn in VOID:
                    sql(c, f"select public.{fn}({args})")
                    return self.out(204)
                return self.out(200, sql(c, f"select to_jsonb(public.{fn}({args}))").encode())
            except DbFehler as e:
                return self.out(e.http(bool(c)), {"code": e.code, "message": e.text})
        self.out(404, {})

    def do_DELETE(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path.startswith(STORAGE):
            return self.speicher_loeschen(u, self.claims())
        self.faktoren(u.path, {}, self.claims(), loeschen=True)

    # ------------------------------------------------------------ Storage
    # Form der Supabase-Storage-API (am echten Projekt ungeprüft): Fehler als HTTP 400 mit dem
    # eigentlichen Status als Text in statusCode.
    def speicherfehler(self, status: str, fehler: str, text: str):
        return self.out(400, {"statusCode": status, "error": fehler, "message": text})

    def speicher_lesen(self, u, c):
        teile = [unquote(x) for x in u.path[len(STORAGE):].split("/")]
        if teile[0] == "public":
            return self.speicherfehler("400", "InvalidRequest", "Bucket ist nicht öffentlich")
        if teile[0] == "authenticated":
            teile = teile[1:]
        if not c:
            return self.speicherfehler("403", "Unauthorized", "Anmeldung fehlt")
        if len(teile) < 2:
            return self.speicherfehler("400", "InvalidRequest", "Pfad fehlt")
        bucket, name = teile[0], "/".join(teile[1:])
        try:      # mit der Rolle des Kontos: die Leseregel entscheidet
            # immer genau eine Zeile (sonst wäre die letzte Zeile die Ausgabe von set_config)
            typ = sql(c, "select 'typ:' || coalesce((select metadata ->> 'mimetype' from storage.objects "
                         f"where bucket_id = {q(bucket)} and name = {q(name)}), '')")[4:]
        except DbFehler as e:
            return self.speicherfehler("403" if e.code == "42501" else "500", "Unauthorized", e.text)
        f = ablage(bucket, name)
        if not typ or not f.is_file():
            return self.speicherfehler("404", "not_found", "Object not found")
        return self.out(200, f.read_bytes(), typ)

    def speicher_hochladen(self, u, c):
        n = int(self.headers.get("Content-Length") or 0)
        daten = self.rfile.read(n)
        teile = [unquote(x) for x in u.path[len(STORAGE):].split("/")]
        if not c:
            return self.speicherfehler("403", "Unauthorized", "Anmeldung fehlt")
        if len(teile) < 2 or teile[0] in ("public", "authenticated", "sign"):
            return self.speicherfehler("400", "InvalidRequest", "Pfad fehlt")
        bucket, name = teile[0], "/".join(teile[1:])
        b = json.loads(sql(None, f"select coalesce(to_jsonb(b), 'null') from storage.buckets b where id = {q(bucket)}", als_dienst=True) or "null")
        if not b:
            return self.speicherfehler("404", "Bucket not found", "Bucket not found")
        if b.get("file_size_limit") and len(daten) > b["file_size_limit"]:
            return self.speicherfehler("413", "Payload too large", "The object exceeded the maximum allowed size")
        typ = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if b.get("allowed_mime_types") and typ not in b["allowed_mime_types"]:
            return self.speicherfehler("415", "invalid_mime_type", f"mime type {typ} is not supported")
        meta = json.dumps({"size": len(daten), "mimetype": typ, "cacheControl": self.headers.get("Cache-Control", "")})
        stmt = (f"insert into storage.objects (bucket_id, name, owner, owner_id, metadata) values "
                f"({q(bucket)}, {q(name)}, {q(c['sub'])}, {q(c['sub'])}, {q(meta)}::jsonb)")
        if (self.headers.get("x-upsert") or "").lower() == "true":
            stmt += " on conflict (bucket_id, name) do update set metadata = excluded.metadata, updated_at = now()"
        try:      # mit der Rolle des Kontos: die Regel zum Hochladen entscheidet
            oid = sql(c, stmt + " returning id")
        except DbFehler as e:
            if e.code == "23505":
                return self.speicherfehler("409", "Duplicate", "The resource already exists")
            return self.speicherfehler("403", "Unauthorized", "new row violates row-level security policy")
        ablage(bucket, name).write_bytes(daten)
        return self.out(200, {"Key": f"{bucket}/{name}", "Id": oid})

    def speicher_loeschen(self, u, c):
        body = self.body()
        bucket = unquote(u.path[len(STORAGE):]).strip("/")
        if not c:
            return self.speicherfehler("403", "Unauthorized", "Anmeldung fehlt")
        namen = body.get("prefixes")
        if not bucket or "/" in bucket or not isinstance(namen, list) or not all(isinstance(x, str) for x in namen):
            return self.speicherfehler("400", "InvalidRequest", "prefixes fehlt")
        liste = "array[" + ",".join(q(x) for x in namen) + "]::text[]" if namen else "array[]::text[]"
        try:      # mit der Rolle des Kontos: nur, was die Regel zum Löschen erlaubt; sonst leere Liste
            weg = json.loads(sql(c, f"with x as (delete from storage.objects where bucket_id = {q(bucket)} and name = any({liste}) "
                                    f"returning id, name, bucket_id) select coalesce(jsonb_agg(to_jsonb(x)), '[]'::jsonb) from x"))
        except DbFehler as e:
            return self.speicherfehler("403", "Unauthorized", e.text)
        for o in weg:
            ablage(bucket, o["name"]).unlink(missing_ok=True)
        return self.out(200, weg)

    def faktoren(self, pfad: str, body: dict, c, loeschen: bool = False):
        """Zwei-Faktor in der Form der Supabase-Auth-API (GoTrue); am echten Projekt ungeprüft."""
        if not c:
            return self.out(401, {"msg": "JWT fehlt"})
        teile = pfad.split("/")[4:]            # [], [id], [id, "challenge" | "verify"]
        with SPERRE:
            liste = FAKTOREN.setdefault(c["sub"], [])
            if not teile and not loeschen:
                if any(f["status"] == "verified" for f in liste) and c.get("aal") != "aal2":
                    return self.out(403, {"msg": "AAL2 required to enroll a new factor"})
                f = {"id": str(uuid.uuid4()), "factor_type": "totp", "status": "unverified",
                     "friendly_name": body.get("friendly_name", "")}
                liste.append(f)
                return self.out(200, {"id": f["id"], "type": "totp", "totp": {
                    "qr_code": QR, "secret": "TESTSECRETNURATTRAPPE234567",
                    "uri": "otpauth://totp/Solarmaa:test?secret=TESTSECRETNURATTRAPPE234567"}})
            f = next((x for x in liste if teile and x["id"] == teile[0]), None)
            if f is None:
                return self.out(404, {"msg": "Factor not found"})
            if loeschen:
                if f["status"] == "verified" and c.get("aal") != "aal2":
                    return self.out(403, {"msg": "AAL2 required to unenroll verified factor"})
                liste.remove(f)
                return self.out(200, {"id": f["id"]})
            if teile[1:] == ["challenge"]:
                f["challenge"] = str(uuid.uuid4())
                return self.out(200, {"id": f["challenge"], "expires_at": int(time.time()) + 300})
            if teile[1:] == ["verify"]:
                if body.get("challenge_id") != f.get("challenge") or str(body.get("code")) != TOTP_CODE:
                    return self.out(422, {"msg": "Invalid TOTP code entered"})
                f["status"] = "verified"
                f.pop("challenge", None)
                return self.out(200, sitzung(c["sub"], c["email"], "aal2"))
        self.out(404, {})


if __name__ == "__main__":
    for var in ("PGHOST", "PGPORT", "PGDATABASE"):
        if not os.environ.get(var):
            sys.exit(f"{var} ist nicht gesetzt (siehe README.md)")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
