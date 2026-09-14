from __future__ import annotations

import struct
import time
from dataclasses import dataclass

import serial
from serial.tools import list_ports

MSP_DATAFLASH_SUMMARY = 70
MSP_DATAFLASH_READ = 71
MSP_API_VERSION = 1
MSP_FC_VARIANT = 2
MSP_FC_VERSION = 3

@dataclass
class PortInfo:
    device: str
    description: str
    manufacturer: str | None = None
    serial_number: str | None = None


def ports() -> list[dict]:
    out = []
    for p in list_ports.comports():
        out.append({"device": p.device, "description": p.description or "", "manufacturer": p.manufacturer or "", "serial_number": p.serial_number or ""})
    return out


def _checksum(data: bytes) -> int:
    v = 0
    for b in data:
        v ^= b
    return v


def _request(ser: serial.Serial, command: int, payload: bytes = b"", timeout: float = 8.0) -> tuple[int, bytes]:
    if len(payload) > 255:
        raise ValueError("MSP v1 payload too large")
    body = bytes([len(payload), command]) + payload
    ser.write(b"$M<" + body + bytes([_checksum(body)]))
    ser.flush()
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while time.monotonic() < deadline:
        b = ser.read(1)
        if not b:
            continue
        buf += b
        if len(buf) >= 3 and bytes(buf[-3:]) not in (b"$M>", b"$M!"):
            continue
        if len(buf) < 6:
            continue
        size = buf[3]
        total = 6 + size
        while len(buf) < total and time.monotonic() < deadline:
            chunk = ser.read(total - len(buf))
            if chunk:
                buf += chunk
        if len(buf) < total:
            break
        direction = bytes(buf[2:3])
        cmd = buf[4]
        data = bytes(buf[5:5 + size])
        checksum = buf[5 + size]
        if _checksum(bytes([size, cmd]) + data) != checksum:
            raise RuntimeError("Betaflight returned an invalid MSP checksum")
        if direction == b"!":
            raise RuntimeError(f"Betaflight rejected MSP command {cmd}")
        return cmd, data
    raise TimeoutError("Timed out waiting for Betaflight over USB")


class Betaflight:
    def __init__(self, device: str, baudrate: int = 115200, timeout: float = 0.25):
        self.device = device
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser: serial.Serial | None = None

    def __enter__(self):
        self.ser = serial.Serial(self.device, self.baudrate, timeout=self.timeout, write_timeout=8)
        time.sleep(0.15)
        self.ser.reset_input_buffer()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.ser:
            self.ser.close()

    def request(self, command: int, payload: bytes = b"", timeout: float = 8.0) -> bytes:
        if not self.ser:
            raise RuntimeError("Betaflight USB connection is not open")
        return _request(self.ser, command, payload, timeout)[1]

    def identify(self) -> dict:
        result = {"port": self.device}
        for key, cmd in (("api_version", MSP_API_VERSION), ("fc_variant", MSP_FC_VARIANT), ("fc_version", MSP_FC_VERSION)):
            try:
                data = self.request(cmd)
                if cmd == MSP_API_VERSION and len(data) >= 3:
                    result[key] = ".".join(str(x) for x in data[:3])
                elif cmd == MSP_FC_VARIANT:
                    result[key] = data.rstrip(b"\x00").decode("ascii", "replace")
                elif cmd == MSP_FC_VERSION and len(data) >= 3:
                    result[key] = ".".join(str(x) for x in data[:3])
            except Exception as exc:
                result[key + "_error"] = str(exc)
        return result

    def dataflash_summary(self) -> dict:
        data = self.request(MSP_DATAFLASH_SUMMARY)
        if len(data) < 13:
            raise RuntimeError(f"Unexpected MSP_DATAFLASH_SUMMARY response ({len(data)} bytes)")
        flags, sectors, total_size, used_size = struct.unpack_from("<BIII", data, 0)
        return {"flags": flags, "sectors": sectors, "total_size": total_size, "used_size": used_size, "supported": bool(flags & 1), "ready": bool(flags & 2)}

    def read_dataflash(self, address: int, size: int) -> tuple[int, bytes]:
        payload = struct.pack("<IH", address, size) + b"\x00"
        data = self.request(MSP_DATAFLASH_READ, payload, timeout=12.0)
        if len(data) < 4:
            raise RuntimeError("Betaflight returned an empty dataflash response")
        returned_address = struct.unpack_from("<I", data, 0)[0]
        # Current MSP format: address, uint16 actual length, compression byte, bytes.
        if len(data) >= 7:
            returned_size = struct.unpack_from("<H", data, 4)[0]
            compression = data[6]
            if compression != 0:
                raise RuntimeError("Betaflight returned compressed dataflash data; compression is not enabled in this client")
            chunk = data[7:7 + returned_size]
            if len(chunk) != returned_size:
                raise RuntimeError("Truncated dataflash response")
            return returned_address, chunk
        # Legacy format fallback: address followed by a fixed 128-byte block.
        return returned_address, data[4:]

    def download_dataflash(self, destination, chunk_size: int = 128, progress=None) -> dict:
        summary = self.dataflash_summary()
        if not summary["supported"] or not summary["ready"]:
            raise RuntimeError("Betaflight dataflash is not supported or not ready")
        limit = min(summary["used_size"], summary["total_size"])
        if limit <= 0:
            raise RuntimeError("Betaflight dataflash contains no recorded data")
        address = 0
        total_written = 0
        with open(destination, "wb") as out:
            while address < limit:
                requested = min(chunk_size, limit - address)
                returned_address, chunk = self.read_dataflash(address, requested)
                if returned_address != address:
                    raise RuntimeError(f"Dataflash address mismatch: requested {address}, received {returned_address}")
                if not chunk:
                    raise RuntimeError("Betaflight returned an empty dataflash chunk")
                out.write(chunk)
                total_written += len(chunk)
                address += len(chunk)
                if progress:
                    progress(address, limit)
                if len(chunk) < requested:
                    break
        return {**summary, "bytes_written": total_written}


def identify(device: str) -> dict:
    with Betaflight(device) as bf:
        return {**bf.identify(), "dataflash": bf.dataflash_summary()}


def download(device: str, destination, progress=None) -> dict:
    with Betaflight(device) as bf:
        return bf.download_dataflash(destination, progress=progress)
