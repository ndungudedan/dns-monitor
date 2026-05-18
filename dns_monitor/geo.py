"""
GeoIP enrichment using MaxMind GeoLite2 (free, offline).

Setup:
  1. Register at https://dev.maxmind.com/geoip/geolite2-free-geolocation-data
  2. Download GeoLite2-City.mmdb and GeoLite2-ASN.mmdb
  3. Place them in the project root (or set GEOIP_DB_DIR env var)

If the DB files are not found, enrichment is silently skipped.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_city_reader = None
_asn_reader  = None
_geo_unavailable = False


def _db_dir() -> Path:
    return Path(os.environ.get("GEOIP_DB_DIR", "."))


def _load_readers():
    global _city_reader, _asn_reader, _geo_unavailable
    if _geo_unavailable:
        return False
    if _city_reader and _asn_reader:
        return True
    try:
        import geoip2.database  # type: ignore
        city_path = _db_dir() / "GeoLite2-City.mmdb"
        asn_path  = _db_dir() / "GeoLite2-ASN.mmdb"
        if not city_path.exists() or not asn_path.exists():
            logger.info("GeoIP DB not found — enrichment disabled. See dns_monitor/geo.py for setup.")
            _geo_unavailable = True
            return False
        _city_reader = geoip2.database.Reader(str(city_path))
        _asn_reader  = geoip2.database.Reader(str(asn_path))
        logger.info("GeoIP loaded from %s", _db_dir())
        return True
    except ImportError:
        logger.info("geoip2 package not installed — enrichment disabled.")
        _geo_unavailable = True
        return False


@dataclass
class GeoResult:
    ip: str
    country_code: Optional[str]
    country_name: Optional[str]
    city: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    asn: Optional[int]
    asn_org: Optional[str]


def enrich(ip: str) -> Optional[GeoResult]:
    """Return geo/ASN data for an IP. Returns None if unavailable or private."""
    if not _load_readers():
        return None
    # Skip private/loopback ranges
    if ip.endswith(".onion") or ip.endswith(".i2p"):
        return None
    try:
        city = _city_reader.city(ip)
        asn  = _asn_reader.asn(ip)
        return GeoResult(
            ip=ip,
            country_code=city.country.iso_code,
            country_name=city.country.name,
            city=city.city.name,
            latitude=city.location.latitude,
            longitude=city.location.longitude,
            asn=asn.autonomous_system_number,
            asn_org=asn.autonomous_system_organization,
        )
    except Exception:
        return None


def enrich_batch(ips: list[str]) -> dict[str, GeoResult]:
    """Enrich a list of IPs, returning a dict keyed by IP."""
    if not _load_readers():
        return {}
    return {ip: r for ip in ips if (r := enrich(ip)) is not None}
