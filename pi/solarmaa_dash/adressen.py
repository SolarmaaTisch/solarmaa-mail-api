"""Prüfung von Internet-Adressen aus der Konfiguration.

urllib öffnet auch file://- und ftp://-Adressen. Eine Adresse aus der Konfiguration (die über das
Portal geändert werden kann) könnte damit lokale Dateien lesen oder einen Versand vortäuschen
(POST auf file: gilt als gelungen). Darum lassen alle Stellen, die eine solche Adresse aufrufen,
nur http und https mit nicht leerem Rechnernamen zu; die Konfiguration selbst ist strenger.
"""
from __future__ import annotations

from urllib.parse import urlsplit

LOKAL = ("127.0.0.1", "localhost", "::1")


def pruefe_url(url, schemata=("http", "https"), was: str = "Adresse", lokal_http: bool = False) -> str:
    """Gibt url zurück oder wirft ValueError. lokal_http: http nur für 127.0.0.1/localhost (Tests)."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"{was}: Adresse fehlt")
    try:
        teile = urlsplit(url)
        host = teile.hostname
    except ValueError:
        raise ValueError(f"{was}: Adresse ungültig") from None
    schema = teile.scheme.lower()
    erlaubt = schema in schemata or (lokal_http and schema == "http" and host in LOKAL)
    if not erlaubt:
        noetig = " oder ".join(f"{s}://" for s in schemata)
        raise ValueError(f"{was} muss mit {noetig} beginnen")
    if not host:
        raise ValueError(f"{was}: Rechnername fehlt")
    return url
