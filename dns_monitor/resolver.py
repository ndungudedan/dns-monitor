import dns.resolver
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SeedResult:
    seed: str
    network: str
    queried_at: float
    response_time_ms: Optional[float]
    records: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and len(self.records) > 0


def resolve_seed(seed: str, network: str, timeout: float = 5.0) -> SeedResult:
    queried_at = time.time()
    resolver = dns.resolver.Resolver()
    resolver.timeout = timeout
    resolver.lifetime = timeout

    start = time.monotonic()
    try:
        answers = resolver.resolve(seed, "A")
        elapsed_ms = (time.monotonic() - start) * 1000
        records = [str(r) for r in answers]
        logger.debug("%s resolved %d records in %.1fms", seed, len(records), elapsed_ms)
        return SeedResult(seed, network, queried_at, elapsed_ms, records)
    except Exception as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.warning("%s failed: %s", seed, e)
        return SeedResult(seed, network, queried_at, elapsed_ms, error=str(e))


def resolve_all(seeds: dict[str, list[str]]) -> list[SeedResult]:
    results = []
    for network, seed_list in seeds.items():
        for seed in seed_list:
            results.append(resolve_seed(seed, network))
    return results
