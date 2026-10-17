"""Read-only Modbus/TCP register helper shared by independent measurement sources."""
from __future__ import annotations

import socket
import struct
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pv2hash.sources.battery_modbus import ModbusValueConfig

REGISTER_TYPE_TO_FUNCTION = {
    "holding": 0x03,
    "input": 0x04,
    "coil": 0x01,
    "discrete_input": 0x02,
}

class ModbusTcpRegisterReader:
    """Uses self.unit_id and self._transaction_id; contains no Modbus writes."""

    def _read_numeric_value(self, sock: socket.socket, cfg: ModbusValueConfig) -> float | None:
        if not cfg.enabled:
            return None

        register_type = cfg.register_type if cfg.register_type in REGISTER_TYPE_TO_FUNCTION else "holding"
        function_code = REGISTER_TYPE_TO_FUNCTION[register_type]
        raw_bytes = self._read_modbus_value(sock, function_code, int(cfg.address), cfg.register_count)
        decoded = self._decode_value(raw_bytes=raw_bytes, cfg=cfg, register_type=register_type)
        if decoded is None:
            return None
        return float(decoded) * float(cfg.factor)

    def _read_modbus_value(
        self,
        sock: socket.socket,
        function_code: int,
        address: int,
        quantity: int,
    ) -> bytes:
        self._transaction_id = (self._transaction_id + 1) % 0x10000
        transaction_id = self._transaction_id

        request_pdu = struct.pack(
            ">BHH",
            function_code,
            address,
            quantity,
        )
        request_mbap = struct.pack(
            ">HHHB",
            transaction_id,
            0,
            len(request_pdu) + 1,
            self.unit_id,
        )
        sock.sendall(request_mbap + request_pdu)

        header = self._recv_exact(sock, 7)
        rx_transaction_id, protocol_id, length, unit_id = struct.unpack(
            ">HHHB",
            header,
        )
        if rx_transaction_id != transaction_id:
            raise RuntimeError("Modbus transaction id mismatch")
        if protocol_id != 0:
            raise RuntimeError("Modbus protocol id mismatch")
        if unit_id != self.unit_id:
            raise RuntimeError("Modbus unit id mismatch")
        payload = self._recv_exact(sock, max(0, length - 1))
        if not payload:
            raise RuntimeError("Modbus response payload is empty")

        response_function = payload[0]
        if response_function & 0x80:
            exc_code = payload[1] if len(payload) > 1 else None
            raise RuntimeError(f"Modbus exception response: function=0x{response_function:02x} code={exc_code}")
        if response_function != function_code:
            raise RuntimeError("Modbus function code mismatch")

        if function_code in (0x01, 0x02):
            byte_count = payload[1]
            data = payload[2 : 2 + byte_count]
            if len(data) != byte_count:
                raise RuntimeError("Modbus bit response length mismatch")
            return data

        byte_count = payload[1]
        data = payload[2 : 2 + byte_count]
        if len(data) != byte_count:
            raise RuntimeError("Modbus register response length mismatch")
        expected_len = quantity * 2
        if byte_count < expected_len:
            raise RuntimeError(
                f"Modbus register response too short: got={byte_count} expected={expected_len}"
            )
        return data[:expected_len]

    def _decode_value(
        self,
        *,
        raw_bytes: bytes,
        cfg: ModbusValueConfig,
        register_type: str,
    ) -> float | int | None:
        if register_type in {"coil", "discrete_input"}:
            return 1 if raw_bytes and (raw_bytes[0] & 0x01) else 0

        data = bytes(raw_bytes)
        if cfg.endian == "little_endian":
            data = data[::-1]

        value_type = cfg.value_type

        if value_type == "int8":
            return struct.unpack(">b", data[:1])[0]
        if value_type == "uint8":
            return struct.unpack(">B", data[:1])[0]
        if value_type == "int16":
            return struct.unpack(">h", data[:2])[0]
        if value_type == "uint16":
            return struct.unpack(">H", data[:2])[0]
        if value_type == "int32":
            return struct.unpack(">i", data[:4])[0]
        if value_type == "uint32":
            return struct.unpack(">I", data[:4])[0]
        if value_type == "float32":
            return struct.unpack(">f", data[:4])[0]

        raise RuntimeError(f"Unsupported Modbus value type: {value_type}")

    def _recv_exact(self, sock: socket.socket, length: int) -> bytes:
        chunks: list[bytes] = []
        received = 0
        while received < length:
            chunk = sock.recv(length - received)
            if not chunk:
                raise RuntimeError("Modbus socket closed unexpectedly")
            chunks.append(chunk)
            received += len(chunk)
        return b"".join(chunks)

