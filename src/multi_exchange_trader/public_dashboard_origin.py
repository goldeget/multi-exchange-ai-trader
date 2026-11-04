"""One explicit HTTPS dashboard origin; configuration, never account authority."""

import ipaddress
import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class PublicDashboardOrigin:
    origin: str
    authority: str

    def matches_host(self, value: str) -> bool:
        try:
            candidate = parse_public_dashboard_origin("https://" + value)
        except ValueError:
            return False
        return candidate == self


def parse_public_dashboard_origin(value: str | None) -> PublicDashboardOrigin | None:
    """Absence keeps local-only; present invalid input never falls back to local."""
    if value is None:
        return None
    try:
        if (type(value) is not str or not value or len(value) > 2048 or not value.isascii()
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
                or any(char in value for char in ("\\", ",", "%", "*"))):
            raise ValueError
        parsed = urlsplit(value)
        if (not value.startswith("https://") or parsed.scheme != "https"
                or not parsed.netloc or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment
                or value != "https://" + parsed.netloc):
            raise ValueError
        host = parsed.hostname
        if host is None:
            raise ValueError
        if ":" in host:
            address = ipaddress.IPv6Address(host)
            hostname = "[" + address.compressed + "]"
        else:
            try:
                hostname = str(ipaddress.IPv4Address(host))
            except ipaddress.AddressValueError:
                # Numeric/alternate IPv4 spellings must not be accepted as DNS names.
                if (len(host) > 253 or not re.search(r"[a-z]", host.rsplit(".", 1)[-1])
                        or any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                               is None for label in host.split("."))):
                    raise ValueError from None
                hostname = host
        port = parsed.port
        # Reject empty, leading-zero and ambiguous authority spellings.
        spelling = hostname + (":" + str(port) if port is not None else "")
        if parsed.netloc.lower() != spelling or port is not None and not 1 <= port <= 65535:
            raise ValueError
        authority = hostname + (":" + str(port) if port not in (None, 443) else "")
        return PublicDashboardOrigin("https://" + authority, authority)
    except ValueError:
        raise ValueError("INVALID_PUBLIC_DASHBOARD_ORIGIN") from None


def is_local_dashboard_host(value: str) -> bool:
    """Accept only a local authority, never a URL, list or userinfo form."""
    match = re.fullmatch(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?", value.lower())
    return match is not None and (match[1] is None or int(match[1]) <= 65535)


def dashboard_headers_allowed(hosts: Sequence[str], origins: Sequence[str], *,
                              public_origin: PublicDashboardOrigin | None) -> bool:
    """Shared read boundary; route-specific CSRF/body/order guards remain separate."""
    if len(hosts) != 1 or len(origins) > 1:
        return False
    if origins and (any(char.isspace() or ord(char) < 32 or ord(char) == 127
                        for char in origins[0]) or "," in origins[0]):
        return False
    if public_origin is not None and public_origin.matches_host(hosts[0]):
        return not origins or origins[0] == public_origin.origin
    return is_local_dashboard_host(hosts[0])
