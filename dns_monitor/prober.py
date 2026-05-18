import ipaddress
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from .handshake import handshake, PeerInfo
from .resolver import SeedResult

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0
MAX_WORKERS = 50


def classify_network_type(ip: str) -> str:
    if ip.endswith(".onion"):
        return "tor"
    if ip.endswith(".i2p") or ip.endswith(".b32.i2p"):
        return "i2p"
    try:
        return "ipv6" if ipaddress.ip_address(ip).version == 6 else "ipv4"
    except ValueError:
        return "unknown"


@dataclass
class NodeResult:
    ip: str
    port: int
    seed: str
    network: str
    probed_at: float
    reachable: bool
    connect_ms: Optional[float] = None
    network_type: str = "ipv4"
    # Populated when handshake succeeds
    user_agent: Optional[str] = None
    services: Optional[int] = None
    service_names: list[str] = field(default_factory=list)
    protocol_version: Optional[int] = None
    start_height: Optional[int] = None
    error: Optional[str] = None


def probe_node(ip: str, port: int, seed: str, network: str) -> NodeResult:
    probed_at = time.time()
    start = time.monotonic()

    net_type = classify_network_type(ip)
    peer: Optional[PeerInfo] = handshake(ip, port, network, timeout=CONNECT_TIMEOUT)
    connect_ms = (time.monotonic() - start) * 1000

    if peer is None:
        logger.debug("%s:%d unreachable", ip, port)
        return NodeResult(ip, port, seed, network, probed_at, False, connect_ms,
                          network_type=net_type, error="handshake failed")

    logger.debug("%s:%d  ua=%s  services=%s  height=%d",
                 ip, port, peer.user_agent, peer.service_names, peer.start_height)
    return NodeResult(
        ip=ip, port=port, seed=seed, network=network,
        probed_at=probed_at, reachable=True, connect_ms=connect_ms,
        network_type=net_type,
        user_agent=peer.user_agent,
        services=peer.services,
        service_names=peer.service_names,
        protocol_version=peer.protocol_version,
        start_height=peer.start_height,
    )


def probe_seed_results(
    seed_results: list[SeedResult],
    default_ports: dict[str, int],
    max_workers: int = MAX_WORKERS,
) -> list[NodeResult]:
    tasks = [
        (ip, default_ports.get(sr.network, 8333), sr.seed, sr.network)
        for sr in seed_results if sr.ok
        for ip in sr.records
    ]

    results: list[NodeResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(probe_node, *t): t for t in tasks}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                logger.error("Probe task error: %s", e)

    return results
