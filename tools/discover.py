#!/usr/bin/env python3
"""
One-shot SSDP discovery tool for Bose SoundTouch UPnP descriptor URL.

Run this from ANY machine on the SAME local network as the speaker
(home laptop, Pi, phone tethering — anything on 192.168.1.x).
Do NOT run it from the office server or the Docker container; SSDP
responses do not traverse WireGuard NAT from those positions.

Usage:
    python tools/discover.py                    # scan for all UPnP devices
    python tools/discover.py 192.168.1.49       # filter to one speaker IP

Output: the exact BOSE_UPNP_DESCRIPTOR_URL value to paste into docker-compose.yml.

Requirements: Python 3.9+, no third-party packages needed.
"""
import re
import socket
import sys
import time
from typing import Optional


# ── SSDP constants ────────────────────────────────────────────────────────────

_MCAST_ADDR = "239.255.255.250"
_MCAST_PORT = 1900
_TIMEOUT    = 8.0   # seconds to wait for responses per search-target

_SEARCH_TARGETS = [
    "upnp:rootdevice",
    "ssdp:all",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
]

_MSEARCH_TMPL = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 4\r\n"
    "ST: {st}\r\n"
    "\r\n"
)


# ── Discovery ─────────────────────────────────────────────────────────────────

def _make_sock(ttl: int = 4) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
    return s


def _parse_location(response: str) -> Optional[str]:
    m = re.search(r"^LOCATION:\s*(.+)$", response, re.MULTILINE | re.IGNORECASE)
    return m.group(1).strip() if m else None


def _parse_server(response: str) -> str:
    m = re.search(r"^SERVER:\s*(.+)$", response, re.MULTILINE | re.IGNORECASE)
    return m.group(1).strip() if m else "unknown"


def discover(target_ip: Optional[str] = None) -> dict[str, dict]:
    """
    Broadcast SSDP M-SEARCH for each ST and collect LOCATION responses.

    Returns {ip: {"location": url, "server": server_header}}.
    """
    found: dict[str, dict] = {}

    for st in _SEARCH_TARGETS:
        pkt = _MSEARCH_TMPL.format(st=st).encode()

        with _make_sock() as s:
            try:
                s.sendto(pkt, (_MCAST_ADDR, _MCAST_PORT))
            except OSError as exc:
                print(f"  [warn] Could not send multicast ({exc}); trying unicast only", file=sys.stderr)

            # Also send unicast directly to the target if we know the IP.
            # This is more reliable when multicast routing is imperfect.
            if target_ip:
                try:
                    s.sendto(pkt, (target_ip, _MCAST_PORT))
                except OSError:
                    pass

            s.settimeout(_TIMEOUT)
            deadline = time.monotonic() + _TIMEOUT

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                s.settimeout(remaining)
                try:
                    data, addr = s.recvfrom(4096)
                except socket.timeout:
                    break
                except OSError:
                    break

                ip   = addr[0]
                text = data.decode("utf-8", errors="replace")

                if target_ip and ip != target_ip:
                    continue

                loc = _parse_location(text)
                if loc and ip not in found:
                    found[ip] = {
                        "location": loc,
                        "server":   _parse_server(text),
                        "st":       st,
                    }
                    print(f"  Hit  {ip}  →  {loc}  [{_parse_server(text)}]")

    return found


def probe_passive(target_ip: str, duration: float = 30.0) -> Optional[str]:
    """
    Join the SSDP multicast group and listen passively for ssdp:alive
    announcements from target_ip.  Devices announce themselves every
    30–1800 s; this only makes sense to run locally where multicast works.

    Returns the LOCATION URL or None if none received within `duration`.
    """
    print(f"  Passive listen on 239.255.255.250:1900 for {duration:.0f}s …")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        import struct
        mreq = struct.pack("4sL", socket.inet_aton(_MCAST_ADDR), socket.INADDR_ANY)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError as exc:
        print(f"  [warn] Cannot join multicast group: {exc}", file=sys.stderr)

    s.bind(("", _MCAST_PORT))
    s.settimeout(duration)
    deadline = time.monotonic() + duration

    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            s.settimeout(max(0.5, remaining))
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                break
            ip = addr[0]
            if ip != target_ip:
                continue
            text = data.decode("utf-8", errors="replace")
            if "NOTIFY" in text and "ssdp:alive" in text:
                loc = _parse_location(text)
                if loc:
                    print(f"  Passive hit from {ip}: {loc}")
                    return loc
    finally:
        s.close()
    return None


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else None

    print("=" * 60)
    print("  Bose SoundTouch UPnP Descriptor Discovery")
    print("  Run this from a device on the SAME LAN as the speaker.")
    print("=" * 60)

    if target:
        print(f"\nTarget IP: {target}")
    else:
        print("\nNo target IP given — scanning all UPnP devices on this LAN.")

    # Phase 1: Active M-SEARCH
    print(f"\n[1/2] Active SSDP M-SEARCH ({_TIMEOUT:.0f}s per search-target) …")
    results = discover(target)

    # Phase 2: Passive listen (only if active found nothing and target is known)
    if not results and target:
        print("\n[2/2] No active response. Trying passive listener (30 s) …")
        loc = probe_passive(target, duration=30.0)
        if loc:
            results[target] = {"location": loc, "server": "passive", "st": "notify"}

    # ── Output ────────────────────────────────────────────────────────────────
    print()
    if not results:
        print("✗  No UPnP descriptors found.")
        print()
        print("Checklist:")
        print("  • Are you running this from a machine on 192.168.1.x?")
        print("  • Is the Bose speaker powered on and connected to WiFi?")
        print("  • Try: curl -v http://<speaker-ip>:8091/")
        print("    If it returns HTTP headers (even 404), SSDP should be working.")
        print("  • Try again with the speaker IP as argument:")
        print("    python tools/discover.py 192.168.1.49")
        return 1

    print("✓  Found descriptors:")
    print()
    for ip, info in results.items():
        loc = info["location"]
        print(f"  Speaker: {ip}")
        print(f"  URL    : {loc}")
        print(f"  Server : {info['server']}")
        print()
        print("  Paste this into docker-compose.yml → environment:")
        print(f"    - BOSE_UPNP_DESCRIPTOR_URL={loc}")
        print()
        print("  Then: docker compose up -d --force-recreate touchingsounds")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
