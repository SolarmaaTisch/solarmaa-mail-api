"""Minimaler Modbus-TCP-Client (nur Funktion 3: Holding-Register lesen).

Bewusst ohne Fremdbibliothek: weniger Abhängigkeiten heisst weniger, das bei
einem Update im Verteiler kaputtgehen kann.
"""
from __future__ import annotations

import socket
import struct
import threading


class ModbusError(Exception):
    """Fehler bei der Modbus-Kommunikation."""


class ModbusTcpClient:
    def __init__(self, host: str, port: int = 502, unit: int = 1, timeout: float = 3.0):
        self.host = host
        self.port = port
        self.unit = unit
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._tid = 0
        self._lock = threading.Lock()

    # -- Verbindung -------------------------------------------------------
    def connect(self) -> None:
        self.close()
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        self._sock = sock

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _recv_exact(self, n: int) -> bytes:
        assert self._sock is not None
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ModbusError("Verbindung vom Gerät geschlossen")
            buf += chunk
        return buf

    # -- Lesen ------------------------------------------------------------
    def read_holding(self, address: int, count: int) -> list[int]:
        if not 1 <= count <= 125:
            raise ValueError("count muss zwischen 1 und 125 liegen")
        with self._lock:
            if self._sock is None:
                try:
                    self.connect()
                except OSError as exc:
                    raise ModbusError(f"Keine Verbindung zu {self.host}:{self.port} ({exc})") from exc
            self._tid = (self._tid + 1) & 0xFFFF
            pdu = struct.pack(">BHH", 3, address, count)
            mbap = struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, self.unit)
            try:
                self._sock.sendall(mbap + pdu)
                tid, _proto, length, _unit = struct.unpack(">HHHB", self._recv_exact(7))
                body = self._recv_exact(length - 1)
            except OSError as exc:
                self.close()
                raise ModbusError(f"Übertragungsfehler ({exc})") from exc
            except ModbusError:
                self.close()
                raise
            if tid != self._tid:
                self.close()
                raise ModbusError("Antwort passt nicht zur Anfrage (Transaktions-ID)")
            fc = body[0]
            if fc == 0x83:
                raise ModbusError(f"Gerät meldet Modbus-Exception {body[1]} bei Register {address}")
            if fc != 3:
                raise ModbusError(f"Unerwarteter Funktionscode {fc}")
            byte_count = body[1]
            if byte_count != 2 * count or len(body) < 2 + byte_count:
                raise ModbusError("Antwort hat falsche Länge")
            return list(struct.unpack(f">{count}H", body[2 : 2 + byte_count]))

    def write_registers(self, address: int, values: list[int]) -> None:
        """Funktion 16 (Write Multiple Registers). Nur für die Abregelung verwendet."""
        if not 1 <= len(values) <= 120:
            raise ValueError("1 bis 120 Register")
        with self._lock:
            if self._sock is None:
                try:
                    self.connect()
                except OSError as exc:
                    raise ModbusError(f"Keine Verbindung zu {self.host}:{self.port} ({exc})") from exc
            self._tid = (self._tid + 1) & 0xFFFF
            n = len(values)
            pdu = struct.pack(f">BHHB{n}H", 16, address, n, 2 * n, *[v & 0xFFFF for v in values])
            mbap = struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, self.unit)
            try:
                self._sock.sendall(mbap + pdu)
                tid, _p, length, _u = struct.unpack(">HHHB", self._recv_exact(7))
                body = self._recv_exact(length - 1)
            except (OSError, ModbusError) as exc:
                self.close()
                raise ModbusError(f"Schreiben fehlgeschlagen ({exc})") from exc
            if tid != self._tid:
                self.close()
                raise ModbusError("Antwort passt nicht zur Anfrage (Transaktions-ID)")
            if body[0] == 0x90:
                raise ModbusError(f"Gerät lehnt Schreiben ab (Exception {body[1]}) bei Register {address}")
            if body[0] != 16:
                raise ModbusError(f"Unerwarteter Funktionscode {body[0]}")

    def read_many(self, address: int, count: int) -> list[int]:
        """Liest auch mehr als 125 Register in mehreren Blöcken."""
        out: list[int] = []
        while count > 0:
            n = min(count, 120)
            out += self.read_holding(address, n)
            address += n
            count -= n
        return out
