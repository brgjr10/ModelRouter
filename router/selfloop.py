"""
router/selfloop.py — Self-loop prevention utilities.

Provides two public functions:

  _own_addresses(port) -> set[str]
      Collects all local interface IPs (loopback + hostname resolution)
      and returns a set of "ip:port" strings for the given port.

  _is_self_loop(url, own, router_port) -> bool
      Returns True if the given URL resolves to one of the router's own
      addresses on the router port, meaning it would create a routing loop.
      Returns True (safe-exclude) on DNS failure.
      Logs a WARNING for every excluded URL with the reason.

Requirements: 4.1, 4.2, 4.3, 4.4
"""

import logging
import socket
from typing import Set

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Own-address discovery
# ---------------------------------------------------------------------------

#: Loopback addresses that are always included regardless of interface
#: enumeration results.
_LOOPBACK_IPS: frozenset = frozenset({"127.0.0.1", "::1", "0.0.0.0"})


def _own_addresses(port: int = 5000) -> Set[str]:
    """Return the set of ``"ip:port"`` strings for all local interfaces.

    The set always includes the three well-known loopback addresses
    (``127.0.0.1``, ``::1``, ``0.0.0.0``) plus every IP returned by
    :func:`socket.gethostbyname_ex` for the machine's hostname.

    Args:
        port: The port to embed in each address string.  Defaults to 5000.

    Returns:
        A :class:`set` of strings in ``"ip:port"`` format, e.g.
        ``{"127.0.0.1:5000", "::1:5000", "192.168.1.10:5000"}``.
    """
    ips: Set[str] = set(_LOOPBACK_IPS)

    try:
        hostname = socket.gethostname()
        # gethostbyname_ex returns (hostname, aliaslist, ipaddrlist)
        _, _, ipaddrlist = socket.gethostbyname_ex(hostname)
        for ip in ipaddrlist:
            ips.add(ip)
    except socket.gaierror as exc:
        logger.warning(
            "Could not resolve local hostname to IPs: %s — using loopback addresses only.",
            exc,
        )

    return {f"{ip}:{port}" for ip in ips}


# ---------------------------------------------------------------------------
# Self-loop check
# ---------------------------------------------------------------------------

#: Literal hostnames that are unconditionally treated as local without DNS.
_LITERAL_LOOPBACK_HOSTS: frozenset = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_self_loop(url: str, own: Set[str], router_port: int = 5000) -> bool:
    """Return ``True`` if *url* points back at this router instance.

    The check proceeds in three steps:

    1. **Literal fast-path** — if the URL's host is ``localhost``,
       ``127.0.0.1``, or ``::1`` *and* the port equals *router_port*,
       return ``True`` immediately without touching DNS.

    2. **Port mismatch early-exit** — if the URL's port differs from
       *router_port*, return ``False`` immediately (different port → not
       the same process).

    3. **DNS resolution** — resolve the hostname to an IP via
       :func:`socket.gethostbyname`; if the resulting ``"ip:port"`` string
       is present in *own*, return ``True``.  On
       :class:`socket.gaierror` or any other exception during resolution,
       log a WARNING and return ``True`` (safe-exclude).

    A WARNING is logged for every URL that is excluded (steps 1 or 3).

    Args:
        url:         The endpoint URL to test, e.g.
                     ``"http://myserver:5000/v1/chat/completions"``.
        own:         The set of ``"ip:port"`` strings produced by
                     :func:`_own_addresses`.
        router_port: The port on which this router is listening.
                     Defaults to 5000.

    Returns:
        ``True`` if the URL is a self-loop and should be excluded from the
        Registry; ``False`` otherwise.
    """
    try:
        parsed = httpx.URL(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not parse URL %r — excluding from Registry. Reason: %s",
            url,
            exc,
        )
        return True

    host: str = parsed.host or ""
    # httpx.URL.port is None when the URL uses the default port for its scheme.
    port: int = parsed.port if parsed.port is not None else (
        443 if parsed.scheme == "https" else 80
    )

    # ------------------------------------------------------------------
    # Step 1: Literal fast-path — no DNS needed for well-known loopbacks.
    # ------------------------------------------------------------------
    if host in _LITERAL_LOOPBACK_HOSTS and port == router_port:
        logger.warning(
            "Excluding self-loop URL %r — literal loopback host %r on router port %d.",
            url,
            host,
            router_port,
        )
        return True

    # ------------------------------------------------------------------
    # Step 2: Port mismatch — cannot be the same process.
    # ------------------------------------------------------------------
    if port != router_port:
        return False

    # ------------------------------------------------------------------
    # Step 3: DNS resolution — check if the resolved IP is one of ours.
    # ------------------------------------------------------------------
    try:
        resolved_ip = socket.gethostbyname(host)
        candidate = f"{resolved_ip}:{port}"
        if candidate in own:
            logger.warning(
                "Excluding self-loop URL %r — host %r resolves to %r "
                "which matches a local interface address.",
                url,
                host,
                candidate,
            )
            return True
    except socket.gaierror as exc:
        logger.warning(
            "DNS resolution failed for %r — excluding from Registry. "
            "Reason: %s",
            url,
            exc,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Unexpected error during DNS resolution for %r — excluding from "
            "Registry. Reason: %s",
            url,
            exc,
        )
        return True

    return False
