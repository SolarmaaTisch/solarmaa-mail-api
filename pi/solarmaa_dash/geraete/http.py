"""Kleiner HTTP-JSON-Client mit Basic- und Digest-Auth (SHA-256 und MD5).

Shelly Gen2+ nutzt Digest mit SHA-256, das urllib nicht kann, darum eigene Umsetzung.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

from . import GeraetFehler


def _digest_header(www: str, method: str, url: str, user: str, password: str) -> str:
    params = dict(re.findall(r'(\w+)="?([^",]*)"?', www))
    algo = params.get("algorithm", "MD5").upper()
    h = (lambda s: hashlib.sha256(s.encode()).hexdigest()) if algo.startswith("SHA-256") else \
        (lambda s: hashlib.md5(s.encode()).hexdigest())  # noqa: S324 (vom Gerät vorgegeben)
    realm, nonce = params.get("realm", ""), params.get("nonce", "")
    path = urlparse(url).path + (("?" + urlparse(url).query) if urlparse(url).query else "")
    ha1 = h(f"{user}:{realm}:{password}")
    ha2 = h(f"{method}:{path}")
    cnonce, nc = os.urandom(8).hex(), "00000001"
    if "auth" in params.get("qop", ""):
        resp = h(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
        extra = f', qop=auth, nc={nc}, cnonce="{cnonce}"'
    else:
        resp = h(f"{ha1}:{nonce}:{ha2}")
        extra = ""
    opaque = f', opaque="{params["opaque"]}"' if "opaque" in params else ""
    return (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{path}", '
            f'algorithm={algo}, response="{resp}"{extra}{opaque}')


def get_json(url: str, timeout: float = 5.0, user: str | None = None, password: str | None = None,
             headers: dict | None = None):
    hdrs = {"Accept": "application/json", "User-Agent": "SolarmaaDash/1.1", **(headers or {})}
    req = urllib.request.Request(url, headers=hdrs)
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            www = e.headers.get("WWW-Authenticate", "") if e.code == 401 else ""
            if not (www and password):
                raise
            name = "admin" if user is None else user  # leerer Name ist erlaubt (z. B. whatwatt)
            if www.lower().startswith("digest"):
                auth = _digest_header(www, "GET", url, name, password)
            else:
                auth = "Basic " + base64.b64encode(f"{name}:{password}".encode()).decode()
            req = urllib.request.Request(url, headers={**hdrs, "Authorization": auth})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        hint = " (Passwort prüfen)" if e.code == 401 else ""
        raise GeraetFehler(f"{url}: HTTP {e.code}{hint}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise GeraetFehler(f"{url}: {getattr(e, 'reason', e)}") from e
