"""
Minimal Bitcoin P2P handshake — sends version, reads peer version, sends verack.
Extracts: UserAgent, services bitmask, protocol version, start height.
"""

import hashlib
import logging
import random
import socket
import struct
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 70015

# Per-network magic bytes (little-endian in wire format)
NETWORK_MAGIC = {
    "mainnet":  b"\xf9\xbe\xb4\xd9",
    "testnet3": b"\x0b\x11\x09\x07",
    "signet":   b"\x0a\x03\xcf\x40",
}

# Services bitmask names (BIP 37, 111, 157, 159)
SERVICE_FLAGS = {
    "NODE_NETWORK":         1 << 0,
    "NODE_BLOOM":           1 << 2,
    "NODE_WITNESS":         1 << 3,
    "NODE_COMPACT_FILTERS": 1 << 6,
    "NODE_NETWORK_LIMITED": 1 << 10,
}

OUR_USER_AGENT = "/dns-monitor:0.1.0/"


@dataclass
class PeerInfo:
    protocol_version: int
    services: int
    user_agent: str
    start_height: int

    @property
    def service_names(self) -> list[str]:
        return [name for name, flag in SERVICE_FLAGS.items() if self.services & flag]


# ---------------------------------------------------------------------------
# Message framing
# ---------------------------------------------------------------------------

def _double_sha256(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def _encode_varint(n: int) -> bytes:
    if n < 0xFD:
        return struct.pack("B", n)
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def _encode_varstr(s: str | bytes) -> bytes:
    b = s.encode() if isinstance(s, str) else s
    return _encode_varint(len(b)) + b


def _frame(command: str, payload: bytes, magic: bytes) -> bytes:
    cmd = command.encode().ljust(12, b"\x00")
    checksum = _double_sha256(payload)[:4]
    return magic + cmd + struct.pack("<I", len(payload)) + checksum + payload


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed connection")
        buf += chunk
    return buf


def _read_message(sock: socket.socket) -> tuple[str, bytes]:
    header = _recv_exact(sock, 24)
    command = header[4:16].rstrip(b"\x00").decode("ascii", errors="replace")
    payload_len = struct.unpack_from("<I", header, 16)[0]
    if payload_len > 4_000_000:
        raise ValueError(f"implausibly large payload: {payload_len}")
    payload = _recv_exact(sock, payload_len)
    return command, payload


# ---------------------------------------------------------------------------
# Version message construction
# ---------------------------------------------------------------------------

def _make_version_payload(peer_ip: str, peer_port: int) -> bytes:
    try:
        raw = socket.inet_pton(socket.AF_INET, peer_ip)
        ipv6_mapped = b"\x00" * 10 + b"\xff\xff" + raw
    except OSError:
        try:
            ipv6_mapped = socket.inet_pton(socket.AF_INET6, peer_ip)
        except OSError:
            ipv6_mapped = b"\x00" * 16

    addr_recv = struct.pack("<Q", 0) + ipv6_mapped + struct.pack(">H", peer_port)
    addr_from = struct.pack("<Q", 0) + b"\x00" * 16 + struct.pack(">H", 0)

    return (
        struct.pack("<i", PROTOCOL_VERSION)       # version
        + struct.pack("<Q", 0)                     # our services (client, none)
        + struct.pack("<q", int(time.time()))      # timestamp
        + addr_recv                                # addr_recv
        + addr_from                                # addr_from
        + struct.pack("<Q", random.getrandbits(64))# nonce
        + _encode_varstr(OUR_USER_AGENT)           # user agent
        + struct.pack("<i", 0)                     # start height
        + struct.pack("?", False)                  # relay
    )


# ---------------------------------------------------------------------------
# Version payload parsing
# ---------------------------------------------------------------------------

def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    first = data[offset]
    if first < 0xFD:
        return first, offset + 1
    if first == 0xFD:
        return struct.unpack_from("<H", data, offset + 1)[0], offset + 3
    if first == 0xFE:
        return struct.unpack_from("<I", data, offset + 1)[0], offset + 5
    return struct.unpack_from("<Q", data, offset + 1)[0], offset + 9


def _parse_version_payload(payload: bytes) -> PeerInfo:
    offset = 0
    version = struct.unpack_from("<i", payload, offset)[0]; offset += 4
    services = struct.unpack_from("<Q", payload, offset)[0]; offset += 8
    offset += 8   # timestamp
    offset += 26  # addr_recv
    offset += 26  # addr_from
    offset += 8   # nonce

    ua_len, offset = _decode_varint(payload, offset)
    user_agent = payload[offset:offset + ua_len].decode("utf-8", errors="replace")
    offset += ua_len

    start_height = struct.unpack_from("<i", payload, offset)[0]

    return PeerInfo(version, services, user_agent, start_height)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def handshake(
    ip: str,
    port: int,
    network: str,
    timeout: float = 5.0,
) -> Optional[PeerInfo]:
    """
    Opens a TCP connection, performs the Bitcoin version handshake,
    and returns PeerInfo. Returns None on any failure.
    """
    magic = NETWORK_MAGIC.get(network, NETWORK_MAGIC["mainnet"])

    try:
        with socket.create_connection((ip, port), timeout=timeout) as sock:
            sock.settimeout(timeout)

            # Send our version
            version_payload = _make_version_payload(ip, port)
            sock.sendall(_frame("version", version_payload, magic))

            # Read messages until we get the peer's version
            peer_info: Optional[PeerInfo] = None
            for _ in range(5):
                command, payload = _read_message(sock)
                if command == "version":
                    peer_info = _parse_version_payload(payload)
                    # Send verack
                    sock.sendall(_frame("verack", b"", magic))
                    break
                # ignore ping/other preamble messages

            return peer_info

    except Exception as e:
        logger.debug("Handshake failed %s:%d — %s", ip, port, e)
        return None
