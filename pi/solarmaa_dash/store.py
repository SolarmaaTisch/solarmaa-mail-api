"""Speicherung in SQLite: Minutenwerte für Tageskurven, Tagessummen für Verläufe.

Energie wird aus den Leistungswerten integriert. Das funktioniert mit jedem
Gerät, auch ohne Energiezähler-Register.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .sources import Snapshot

MAX_GAP_S = 120  # grössere Lücken (Pi aus, Gerät weg) werden nicht integriert
DAY_FIELDS = ("pv_wh", "load_wh", "import_wh", "export_wh", "charge_wh", "discharge_wh", "ev_wh")
MIN_FIELDS = ("pv_w", "grid_w", "load_w", "battery_w", "soc")


def day_key(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def ratios(d: dict) -> dict:
    """Eigenverbrauch und Autarkie in Prozent (oder None, wenn nicht berechenbar)."""
    pv, load = d.get("pv_wh") or 0.0, d.get("load_wh") or 0.0
    imp, exp = d.get("import_wh") or 0.0, d.get("export_wh") or 0.0
    self_use = max(0.0, min(100.0, 100 * (pv - exp) / pv)) if pv > 50 else None
    autarky = max(0.0, min(100.0, 100 * (load - imp) / load)) if load > 50 else None
    return {"eigenverbrauch_pct": self_use, "autarkie_pct": autarky}


class Store:
    def __init__(self, path: str | Path, retention_days: int = 800):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS minutes (ts INTEGER PRIMARY KEY, "
            "pv_w REAL, grid_w REAL, load_w REAL, battery_w REAL, soc REAL)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, "
            + ", ".join(f"{f} REAL NOT NULL DEFAULT 0" for f in DAY_FIELDS) + ")")
        vorhanden = {r[1] for r in self.db.execute("PRAGMA table_info(days)")}
        for f in DAY_FIELDS:  # ältere Datenbanken nachrüsten
            if f not in vorhanden:
                self.db.execute(f"ALTER TABLE days ADD COLUMN {f} REAL NOT NULL DEFAULT 0")
        self.db.commit()
        self.retention_days = retention_days
        self.lock = threading.Lock()
        self._last_ts: float | None = None
        self._day = day_key(time.time())
        self._energy = self._load_day(self._day)
        self._min_ts: int | None = None
        self._min_sum: dict[str, float] = {}
        self._min_n: dict[str, int] = {}

    # -- Schreiben --------------------------------------------------------
    def _load_day(self, day: str) -> dict:
        row = self.db.execute(f"SELECT {', '.join(DAY_FIELDS)} FROM days WHERE day=?", (day,)).fetchone()
        return dict(zip(DAY_FIELDS, row)) if row else dict.fromkeys(DAY_FIELDS, 0.0)

    def _save_day(self) -> None:
        cols = ", ".join(DAY_FIELDS)
        marks = ", ".join("?" for _ in DAY_FIELDS)
        updates = ", ".join(f"{f}=excluded.{f}" for f in DAY_FIELDS)
        self.db.execute(
            f"INSERT INTO days (day, {cols}) VALUES (?, {marks}) ON CONFLICT(day) DO UPDATE SET {updates}",
            (self._day, *[self._energy[f] for f in DAY_FIELDS]))

    def _flush_minute(self) -> None:
        if self._min_ts is None:
            return
        vals = [self._min_sum[f] / self._min_n[f] if self._min_n.get(f) else None for f in MIN_FIELDS]
        self.db.execute("INSERT OR REPLACE INTO minutes VALUES (?, ?, ?, ?, ?, ?)", (self._min_ts, *vals))
        self._save_day()
        self.db.commit()

    def add(self, s: Snapshot) -> None:
        with self.lock:
            day = day_key(s.ts)
            if day != self._day:
                self._flush_minute()
                self._min_ts = None
                self._day = day
                self._energy = self._load_day(day)
            if self._last_ts is not None:
                dt = s.ts - self._last_ts
                if 0 < dt <= MAX_GAP_S:
                    h = dt / 3600
                    e = self._energy
                    e["pv_wh"] += (s.pv_w or 0.0) * h
                    if s.load_w is not None:
                        e["load_wh"] += s.load_w * h
                    if s.grid_w is not None:
                        e["import_wh" if s.grid_w > 0 else "export_wh"] += abs(s.grid_w) * h
                    if s.battery_w is not None:
                        e["discharge_wh" if s.battery_w > 0 else "charge_wh"] += abs(s.battery_w) * h
                    if s.ev_w:
                        e["ev_wh"] += s.ev_w * h
            self._last_ts = s.ts

            minute = int(s.ts // 60 * 60)
            if minute != self._min_ts:
                self._flush_minute()
                self._min_ts, self._min_sum, self._min_n = minute, {}, {}
            for f in MIN_FIELDS:
                v = getattr(s, f)
                if v is not None:
                    self._min_sum[f] = self._min_sum.get(f, 0.0) + v
                    self._min_n[f] = self._min_n.get(f, 0) + 1

    def flush(self) -> None:
        with self.lock:
            self._flush_minute()

    def prune(self) -> None:
        cutoff = time.time() - self.retention_days * 86400
        with self.lock:
            self.db.execute("DELETE FROM minutes WHERE ts < ?", (int(cutoff),))
            self.db.execute("DELETE FROM days WHERE day < ?", (day_key(cutoff),))
            self.db.commit()

    # -- Lesen ------------------------------------------------------------
    def today(self) -> dict:
        with self.lock:
            d = {"day": self._day, **{k: round(v, 1) for k, v in self._energy.items()}}
        return {**d, **ratios(d)}

    def day_series(self, day: str, bucket_s: int = 300) -> list[list]:
        start = time.mktime(time.strptime(day, "%Y-%m-%d"))
        end = start + 86400 + 3600  # Sommerzeit-Umstellung abdecken
        with self.lock:
            rows = self.db.execute(
                f"SELECT (ts / {bucket_s}) * {bucket_s} AS b, AVG(pv_w), AVG(load_w), AVG(grid_w), "
                "AVG(battery_w), AVG(soc) FROM minutes WHERE ts >= ? AND ts < ? GROUP BY b ORDER BY b",
                (int(start), int(end))).fetchall()
        return [[int(r[0])] + [None if v is None else round(v, 1) for v in r[1:]]
                for r in rows if day_key(r[0]) == day]

    def days(self, first: str, last: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                f"SELECT day, {', '.join(DAY_FIELDS)} FROM days WHERE day BETWEEN ? AND ? ORDER BY day",
                (first, last)).fetchall()
        out = []
        for r in rows:
            d = {"day": r[0], **{f: round(v, 1) for f, v in zip(DAY_FIELDS, r[1:])}}
            out.append({**d, **ratios(d)})
        return out

    def months(self, year: int) -> list[dict]:
        sums = ", ".join(f"SUM({f})" for f in DAY_FIELDS)
        with self.lock:
            rows = self.db.execute(
                f"SELECT substr(day, 1, 7) AS m, {sums} FROM days WHERE day LIKE ? GROUP BY m ORDER BY m",
                (f"{year}-%",)).fetchall()
        out = []
        for r in rows:
            d = {"month": r[0], **{f: round(v, 1) for f, v in zip(DAY_FIELDS, r[1:])}}
            out.append({**d, **ratios(d)})
        return out

    def totals(self) -> dict:
        sums = ", ".join(f"SUM({f})" for f in DAY_FIELDS)
        with self.lock:
            r = self.db.execute(f"SELECT MIN(day), {sums} FROM days").fetchone()
        return {"seit": r[0], **{f: round(v or 0.0, 1) for f, v in zip(DAY_FIELDS, r[1:])}}
