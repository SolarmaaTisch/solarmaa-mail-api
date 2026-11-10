"""Rechnungen aus der smart-me-Abrechnung.

smart-me erstellt die ZEV-/Mieterstrom-Rechnungen im Webportal. Eine Rechnungs-API ist nicht
dokumentiert. Darum: Export aus smart-me (CSV) hier importieren. Spaltennamen werden
automatisch erkannt; abweichende Namen in [haus.rechnungen_csv] zuordnen.

Status: offen, bezahlt, gemahnt. «Überfällig» wird berechnet (offen und Fälligkeit vorbei).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import time

SPALTEN = {
    "nr": ["rechnungsnummer", "rechnungs-nr", "rechnung nr", "rechnung", "nr", "nummer", "invoice", "invoice number"],
    "einheit": ["einheit", "abrechnungseinheit", "wohnung", "ordner", "folder", "objekt", "mieteinheit"],
    "empfaenger": ["empfänger", "empfaenger", "rechnungsempfänger", "mieter", "name", "kunde", "recipient"],
    "von": ["von", "periode von", "abrechnungsperiode von", "start", "from", "beginn"],
    "bis": ["bis", "periode bis", "abrechnungsperiode bis", "ende", "end", "to"],
    "betrag": ["betrag", "betrag chf", "total", "total chf", "summe", "rechnungsbetrag", "amount", "brutto"],
    "status": ["status", "bezahlt", "zahlungsstatus", "paid"],
    "faellig": ["fällig", "faellig", "fällig am", "fälligkeit", "zahlbar bis", "due", "due date"],
    "pdf": ["pdf", "link", "url", "dokument"],
}
STATUS = ("offen", "bezahlt", "gemahnt")


def _datum(s: str) -> str | None:
    s = (s or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _betrag(s: str) -> float | None:
    s = re.sub(r"[^\d,.\-]", "", (s or "").replace("'", "").replace("’", ""))
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _status(s: str) -> str:
    s = (s or "").strip().lower()
    if s in ("bezahlt", "paid", "ja", "yes", "true", "1", "beglichen"):
        return "bezahlt"
    if "mahn" in s:
        return "gemahnt"
    return "offen"


class Rechnungen:
    def __init__(self, store, haus=None, spalten: dict | None = None):
        self.store, self.haus = store, haus
        self.spalten = {k: [*(([spalten[k]] if spalten and spalten.get(k) else [])), *v] for k, v in SPALTEN.items()}
        with store.lock:
            store.db.execute(
                "CREATE TABLE IF NOT EXISTS rechnungen (nr TEXT PRIMARY KEY, einheit TEXT, empfaenger TEXT, von TEXT, "
                "bis TEXT, betrag REAL, status TEXT, faellig TEXT, pdf TEXT, geaendert INTEGER, geaendert_von TEXT)")
            store.db.commit()

    def _einheit_id(self, wert: str) -> str | None:
        w = (wert or "").strip().lower()
        if not w or self.haus is None:
            return w or None
        for e in self.haus.einheiten:
            if w in (e["id"].lower(), e["name"].lower(), (e.get("smartme_ordner") or "").lower()):
                return e["id"]
        return None

    def importieren(self, text: str, von: str = "import") -> dict:
        text = text.lstrip("﻿")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
        except csv.Error:
            class dialect(csv.excel):  # noqa: N801
                delimiter = ";"
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("CSV ist leer")
        kopf = {f.strip().lower(): f for f in reader.fieldnames if f}
        zuordnung = {}
        for feld, namen in self.spalten.items():
            for n in namen:
                if n.lower() in kopf:
                    zuordnung[feld] = kopf[n.lower()]
                    break
        fehlend = [f for f in ("nr", "einheit", "betrag") if f not in zuordnung]
        if fehlend:
            raise ValueError(f"Spalten nicht gefunden: {', '.join(fehlend)}. Vorhanden: {', '.join(reader.fieldnames)}. "
                             "Zuordnung in [haus.rechnungen_csv] eintragen.")
        neu = aktualisiert = 0
        unbekannt: list[str] = []
        jetzt = int(time.time())
        with self.store.lock:
            for row in reader:
                g = {f: (row.get(c) or "").strip() for f, c in zuordnung.items()}
                if not g["nr"]:
                    continue
                eid = self._einheit_id(g["einheit"])
                if eid is None:
                    unbekannt.append(f'{g["nr"]} ({g["einheit"]})')
                    continue
                alt = self.store.db.execute("SELECT status FROM rechnungen WHERE nr = ?", (g["nr"],)).fetchone()
                status = _status(g.get("status", "")) if "status" in zuordnung else (alt[0] if alt else "offen")
                if alt and alt[0] == "bezahlt" and "status" not in zuordnung:
                    status = "bezahlt"
                self.store.db.execute(
                    "INSERT INTO rechnungen VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(nr) DO UPDATE SET "
                    "einheit=excluded.einheit, empfaenger=excluded.empfaenger, von=excluded.von, bis=excluded.bis, "
                    "betrag=excluded.betrag, status=excluded.status, faellig=excluded.faellig, pdf=excluded.pdf, "
                    "geaendert=excluded.geaendert, geaendert_von=excluded.geaendert_von",
                    (g["nr"], eid, g.get("empfaenger", ""), _datum(g.get("von", "")), _datum(g.get("bis", "")),
                     _betrag(g["betrag"]), status, _datum(g.get("faellig", "")), g.get("pdf", ""), jetzt, von))
                if alt:
                    aktualisiert += 1
                else:
                    neu += 1
            self.store.db.commit()
        return {"neu": neu, "aktualisiert": aktualisiert, "nicht_zugeordnet": unbekannt}

    def status_setzen(self, nr: str, status: str, von: str) -> bool:
        if status not in STATUS:
            raise ValueError(f"Status muss eine von {', '.join(STATUS)} sein")
        with self.store.lock:
            cur = self.store.db.execute("UPDATE rechnungen SET status = ?, geaendert = ?, geaendert_von = ? WHERE nr = ?",
                                        (status, int(time.time()), von, nr))
            self.store.db.commit()
        return cur.rowcount > 0

    def liste(self, einheit: str | None = None) -> list[dict]:
        sql = "SELECT nr, einheit, empfaenger, von, bis, betrag, status, faellig, pdf FROM rechnungen"
        args: tuple = ()
        if einheit:
            sql += " WHERE einheit = ?"
            args = (einheit,)
        with self.store.lock:
            rows = self.store.db.execute(sql + " ORDER BY COALESCE(bis, von, nr) DESC", args).fetchall()
        heute = dt.date.today().isoformat()
        out = []
        for r in rows:
            d = dict(zip(("nr", "einheit", "empfaenger", "von", "bis", "betrag", "status", "faellig", "pdf"), r))
            d["ueberfaellig"] = d["status"] != "bezahlt" and bool(d["faellig"]) and d["faellig"] < heute
            out.append(d)
        return out

    def offen_summe(self) -> dict[str, float]:
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT einheit, SUM(betrag) FROM rechnungen WHERE status != 'bezahlt' GROUP BY einheit").fetchall()
        return {r[0]: round(r[1] or 0.0, 2) for r in rows}

    def demo_fuellen(self) -> None:
        if self.haus is None or not self.haus.demo:
            return
        with self.store.lock:
            if self.store.db.execute("SELECT COUNT(*) FROM rechnungen").fetchone()[0]:
                return
        heute = dt.date.today()
        zeilen = ["Rechnungsnummer;Einheit;Empfänger;Von;Bis;Betrag;Status;Fällig"]
        n = 1000
        for q in range(4, 0, -1):
            for i, e in enumerate(self.haus.einheiten):
                if e["typ"] == "technik":
                    continue
                bis = dt.date(heute.year, heute.month, 1) - dt.timedelta(days=1 + 91 * (q - 1))
                von = bis - dt.timedelta(days=90)
                n += 1
                betrag = 180 + 35 * (i % 5) + 18 * q
                status = "offen" if q == 1 or (q == 2 and i in (1, 4)) else "bezahlt"
                fae = bis + dt.timedelta(days=30)
                zeilen.append(f"R-{heute.year}-{n};{e['id']};Beispiel {e['name']};{von:%d.%m.%Y};{bis:%d.%m.%Y};"
                              f"{betrag:.2f};{status};{fae:%d.%m.%Y}")
        self.importieren("\n".join(zeilen), von="demo")
