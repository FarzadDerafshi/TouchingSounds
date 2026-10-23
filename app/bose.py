"""
bose.py — speaker control layer for TouchingSounds.

UPDATE (0.6.0): the 1005 root cause was the dead Bose "marge" account
server, which the speaker asks for its source table at boot.  We now
redirect margeServerUrl to our own stand-in (app/marge.py), and playback
goes through /select LOCAL_INTERNET_RADIO again (play_stream).  The UPnP
notes below are historical.

Transport architecture (post internal-UPnP pivot):
  Port 8090  — Bose proprietary XML REST API (status, volume, presets, keys)
  Port 8091  — Speaker's internal UPnP AVTransport renderer (separate code path)
  Port 23    — Unauthenticated root Telnet shell (BusyBox ash, "root@spotty")
               Used to discover the AVTransport controlURL and trigger
               playback from inside the speaker

Playback mechanism (internal UPnP injection):
  /select with source=LOCAL_INTERNET_RADIO returns 1005 UNKNOWN_SOURCE_ERROR
  on v27 firmware EVEN AFTER patching Sources.xml — the runtime source table
  hardcodes a block on that source string regardless of on-disk config.

  UPnP AVTransport (port 8091) is a completely separate code path that does
  not consult Sources.xml or the source table at all. From inside the speaker
  (via the Telnet shell), curl posts SetAVTransportURI + Play SOAP requests
  directly to the AVTransport controlURL, bypassing the source-table gate
  entirely.
"""

import asyncio
import base64
import binascii
import contextlib
import contextvars
import json
import logging
import os
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote, unquote
from xml.sax.saxutils import escape as _xml_escape

import httpx

log = logging.getLogger("bose")

# ── Configuration ─────────────────────────────────────────────────────────────

_PORT       = os.getenv("BOSE_PORT",       "8090")


# ── Devices ───────────────────────────────────────────────────────────────────
#
# The speakers the app controls live in data/devices.json (since 1.5), which
# the "Add a speaker" wizard (app/setup.py) writes.  On the first start it is
# seeded from BOSE_DEVICES, comma-separated:
#   BOSE_DEVICES=192.168.1.20,192.168.1.21:fresh
# After that BOSE_DEVICES is ignored; add and remove speakers in the app.
# The first one is the primary device: it keeps the pre-1.1 data layout
# (data/Sources.xml etc.) and the PRESET_N_URL defaults.  ":fresh" marks a
# device whose source list may be replaced outright (no Sources.xml snapshot,
# e.g. a second-hand unit still carrying the previous owner's accounts); such
# devices are set up through the port-17000 console (sys configuration +
# envswitch), which also works on firmware without `remote_services`.
# BOSE_HOST alone still works for a single speaker.

@dataclass(frozen=True)
class Device:
    host: str
    fresh: bool = False
    primary: bool = False


_DEVICES_FILE = os.path.join(os.getenv("TS_DATA_DIR", "data"), "devices.json")


def _parse_env_devices() -> list[Device]:
    raw = os.getenv("BOSE_DEVICES", "").strip() or os.getenv("BOSE_HOST", "")
    out: list[Device] = []
    for item in (x.strip() for x in raw.split(",")):
        if not item:
            continue
        host, _, flags = item.partition(":")
        out.append(Device(host=host.strip(), fresh="fresh" in flags.lower(), primary=not out))
    return out


def _load_devices() -> list[Device]:
    try:
        with open(_DEVICES_FILE, encoding="utf-8") as f:
            items = json.load(f)["devices"]
        out = [Device(host=str(x["host"]), fresh=bool(x.get("fresh")), primary=i == 0)
               for i, x in enumerate(items)]
        if out:
            return out
    except (OSError, ValueError, KeyError, TypeError):
        pass
    out = _parse_env_devices()
    if not out:
        raise SystemExit("No speaker configured: set BOSE_DEVICES (see .env.example).")
    _save_devices(out)
    return out


def _save_devices(devs: list[Device]) -> None:
    with contextlib.suppress(OSError):
        os.makedirs(os.path.dirname(_DEVICES_FILE) or ".", exist_ok=True)
        tmp = _DEVICES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"devices": [{"host": d.host, "fresh": d.fresh} for d in devs]}, f, indent=2)
        os.replace(tmp, _DEVICES_FILE)


# Mutated in place by add_device/remove_device, so modules holding a
# reference to the list see the change.
DEVICES: list[Device] = _load_devices()
_DEVICES_BY_HOST = {d.host: d for d in DEVICES}


def add_device(h: str, fresh: bool = True) -> Device:
    """Add speaker `h` to the list (idempotent) and save it."""
    if h in _DEVICES_BY_HOST:
        return _DEVICES_BY_HOST[h]
    d = Device(host=h, fresh=fresh, primary=not DEVICES)
    DEVICES.append(d)
    _DEVICES_BY_HOST[h] = d
    _save_devices(DEVICES)
    log.info("Device %s added (fresh=%s)", h, fresh)
    return d


def remove_device(h: str) -> None:
    """Remove speaker `h` from the list.  The primary can't be removed."""
    d = _DEVICES_BY_HOST.get(h)
    if d is None:
        return
    if d.primary:
        raise BoseError("The first speaker can't be removed: it holds the original data.")
    DEVICES.remove(d)
    del _DEVICES_BY_HOST[h]
    _IDENTITY.pop(h, None)
    _ONLINE.pop(h, None)
    _save_identities()
    _save_devices(DEVICES)
    log.info("Device %s removed", h)

# The device the current request / task talks to.  Set per API request (the
# web app sends X-TS-Device) and per background task; defaults to the primary.
_current_host: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bose_host", default=DEVICES[0].host)


def host() -> str:
    return _current_host.get()


def device(h: str | None = None) -> Device:
    return _DEVICES_BY_HOST[h or host()]


def is_known_host(h: str) -> bool:
    return h in _DEVICES_BY_HOST


@contextlib.contextmanager
def use_device(h: str):
    """Run the enclosed speaker calls against device `h`."""
    token = _current_host.set(h)
    try:
        yield
    finally:
        _current_host.reset(token)


def _base() -> str:
    return f"http://{host()}:{_PORT}"

_TIMEOUT    = httpx.Timeout(connect=4.0, read=6.0, write=4.0, pool=2.0)

# External URL of THIS FastAPI server, reachable from every speaker.
# Used as the bmxRegistryUrl base so the speaker can register LOCAL_INTERNET_RADIO.
_SERVER_URL = os.getenv("BOSE_SERVER_URL", "").strip().rstrip("/")
if not _SERVER_URL:
    raise SystemExit("BOSE_SERVER_URL is not set: the address the speakers reach this "
                     "server at, e.g. http://192.168.1.10:8000 (see .env.example).")

# Filesystem path on the speaker (root@spotty) used for temp SOAP payload files
_HTML_DIR = os.getenv("BOSE_HTML_DIR", "/opt/Bose/html")


# ── BoseError ─────────────────────────────────────────────────────────────────

class BoseError(RuntimeError):
    """Wraps any speaker-communication failure with a human-readable message."""


# ── HTTP transport (proprietary API on port 8090) ─────────────────────────────

def _classify(exc: Exception, url: str) -> BoseError:
    if isinstance(exc, httpx.ConnectError):
        return BoseError(
            f"Cannot connect to speaker at {url}. "
            "Check BOSE_HOST/PORT and that the speaker is on the network."
        )
    if isinstance(exc, httpx.ConnectTimeout):
        return BoseError(f"Connection to {url} timed out.")
    if isinstance(exc, httpx.ReadTimeout):
        return BoseError(f"Speaker at {url} connected but did not respond in time.")
    if isinstance(exc, httpx.HTTPStatusError):
        return BoseError(
            f"Speaker returned HTTP {exc.response.status_code} for {exc.request.url}."
        )
    return BoseError(f"{type(exc).__name__}: {exc}")


async def _get(path: str) -> str:
    url = f"{_base()}{path}"
    log.debug("GET %s", url)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.text
    except BoseError:
        raise
    except Exception as exc:
        err = _classify(exc, url)
        log.error("GET %s failed: %s", url, err)
        raise err from exc


async def _post(path: str, body: str) -> str:
    url = f"{_base()}{path}"
    log.debug("POST %s  body=%r", url, body[:120])
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(
                url,
                content=body.encode(),
                headers={"Content-Type": "application/xml"},
            )
            r.raise_for_status()
            return r.text
    except BoseError:
        raise
    except Exception as exc:
        err = _classify(exc, url)
        log.error("POST %s failed: %s", url, err)
        raise err from exc


# ── Proprietary REST API ───────────────────────────────────────────────────────

async def now_playing() -> str:
    return await _get("/now_playing")

async def volume() -> str:
    return await _get("/volume")

async def info() -> str:
    return await _get("/info")

async def sources() -> str:
    return await _get("/sources")

async def presets() -> str:
    return await _get("/presets")

async def key(name: str) -> None:
    for state in ("press", "release"):
        await _post("/key", f'<key state="{state}" sender="Gabbo">{name}</key>')

async def set_volume(level: int) -> None:
    await _post("/volume", f"<volume>{level}</volume>")

async def select_source(source: str, account: str = "") -> None:
    acct = f' sourceAccount="{account}"' if account else ""
    await _post("/select", f'<ContentItem source="{source}"{acct}></ContentItem>')


async def select_internet_radio(stream_url: str, name: str = "Test Stream") -> str:
    """
    Definitive test: POST /select with source=LOCAL_INTERNET_RADIO,
    type="stationurl", location=<stream_url>, sourceAccount=_BMX_ANON_USER_ID
    (the account written into Sources.xml by patch_sources_xml()).

    Returns the raw response body from the speaker (success XML or the
    1005 UNKNOWN_SOURCE_ERROR fault), unlike the other helpers which raise
    on non-2xx — we want to see the body either way.
    """
    body = (
        f'<ContentItem source="LOCAL_INTERNET_RADIO" type="stationurl" '
        f'location="{_xml_escape(stream_url)}" '
        f'sourceAccount="{_BMX_ANON_USER_ID}" isPresetable="true">'
        f'<itemName>{_xml_escape(name)}</itemName></ContentItem>'
    )
    url = f"{_base()}/select"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.post(url, content=body.encode(), headers={"Content-Type": "application/xml"})
        return f"HTTP {r.status_code}\n{r.text}"


def orion_location(stream_url: str, name: str, image_url: str = "") -> str:
    """
    Build a relative LOCAL_INTERNET_RADIO ("Orion") station location.

    The speaker resolves it against the Orion baseUrl advertised by our BMX
    registry, i.e. GET {_SERVER_URL}/bmx/orion/station?data=..., which hands
    back the real stream URL (see main.bmx_orion_station).  Same format as
    AfterTouch's BuildOrionLocation (github.com/gesellix/bose-soundtouch).
    """
    payload = json.dumps(
        {"name": name, "imageUrl": image_url, "streamUrl": stream_url},
        separators=(",", ":"),
    )
    data = base64.b64encode(payload.encode()).decode("ascii")
    return f"/station?data={quote(data, safe='')}"


def decode_orion_data(data: str) -> dict:
    """
    Inverse of orion_location's data parameter -> {name, imageUrl, streamUrl}.
    Accepts standard or URL-safe base64, missing padding, and "+" that a
    query-string decoder turned into " ".  Raises ValueError on bad data.
    """
    b64 = data.replace(" ", "+").replace("-", "+").replace("_", "/")
    try:
        station = json.loads(base64.b64decode(b64 + "=" * (-len(b64) % 4)))
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"Bad station data: {exc}") from exc
    if not isinstance(station, dict) or "streamUrl" not in station:
        raise ValueError("Bad station data: no streamUrl")
    return station


def decode_orion_location(location: str) -> dict | None:
    """Station dict for an orion "/station?data=..." location, else None."""
    if not location.startswith("/station?data="):
        return None
    with contextlib.suppress(ValueError):
        return decode_orion_data(unquote(location.split("data=", 1)[1]))
    return None


async def play_stream(stream_url: str, name: str = "Radio", image_url: str = "") -> None:
    """
    Play any HTTP/HTTPS/HLS stream via /select source=LOCAL_INTERNET_RADIO
    (HTTPS and HLS go through our /relay, see speaker_stream_url).

    Only works once the speaker has fetched its source table from our marge
    stand-in (app/marge.py) — before that, /select returns 1005.
    """
    validate_playable_url(stream_url)
    location = orion_location(stream_url, name, image_url)
    log.info("play_stream: %s  name=%r", stream_url, name)
    await _post("/select", _content_item_xml(location, name))


def _content_item_xml(location: str, name: str) -> str:
    return (
        f'<ContentItem source="LOCAL_INTERNET_RADIO" type="stationurl" '
        f'location="{_xml_escape(location)}" sourceAccount="" isPresetable="true">'
        f'<itemName>{_xml_escape(name)}</itemName></ContentItem>'
    )


async def select_tunein_stationurl(stream_url: str, name: str = "Test Stream", account: str = "") -> str:
    """
    Alternate test: source="TUNEIN" (already a valid/registered source —
    existing presets use it) with type="stationurl" and a DIRECT http
    stream URL in location, instead of the Bose-cloud
    /v1/playback/station/sXXXXX path. TUNEIN won't hit 1005 since it's a
    real source; the question is whether type="stationurl" plays `location`
    directly or always routes through Bose cloud regardless.

    `account` should match the sourceAccount on existing TUNEIN presets —
    see GET /api/diagnose/presets_raw.
    """
    acct = f' sourceAccount="{_xml_escape(account)}"' if account else ' sourceAccount=""'
    body = (
        f'<ContentItem source="TUNEIN" type="stationurl" '
        f'location="{_xml_escape(stream_url)}"{acct} isPresetable="true">'
        f'<itemName>{_xml_escape(name)}</itemName></ContentItem>'
    )
    url = f"{_base()}/select"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.post(url, content=body.encode(), headers={"Content-Type": "application/xml"})
        return f"HTTP {r.status_code}\n{r.text}"


# ── Stream URL validation ──────────────────────────────────────────────────────

_PLAYLIST_EXTS = frozenset({".m3u", ".m3u8", ".pls", ".xspf", ".asx", ".wpl"})


async def store_preset(slot: int, location: str, name: str) -> None:
    """Store a LOCAL_INTERNET_RADIO station in the speaker's preset slot 1-6."""
    if not 1 <= slot <= 6:
        raise BoseError(f"Preset slot must be 1-6, got {slot}")
    await _post("/storePreset", f'<preset id="{slot}">{_content_item_xml(location, name)}</preset>')
    log.info("Preset %d stored on speaker: %s", slot, name)


def env_presets() -> dict[int, dict]:
    """PRESET_N_URL/NAME from the environment as slot -> {name, url, location}."""
    return {
        n: {"name": _PRESET_NAMES[n], "url": url,
            "location": orion_location(url, _PRESET_NAMES[n])}
        for n, url in _PRESET_URLS.items() if url
    }


async def sync_presets(wanted: dict[int, dict]) -> None:
    """
    Make the speaker's preset slots match `wanted` (slot -> {name, location}),
    storing only the slots that differ, so the buttons play our stations
    natively.  Needs LOCAL_INTERNET_RADIO to be registered, i.e. call it
    after the speaker has booted against our marge.
    """
    try:
        stored = {
            p.get("id"): p.find("ContentItem")
            for p in ET.fromstring(await presets()).iter("preset")
        }
    except (BoseError, ET.ParseError) as exc:
        log.warning("Preset sync skipped, couldn't read presets: %s", exc)
        return
    for n, p in sorted(wanted.items()):
        item = stored.get(str(n))
        if item is not None and item.get("location") == p["location"]:
            continue
        try:
            await store_preset(n, p["location"], p["name"])
        except BoseError as exc:
            log.warning("Preset %d storePreset failed: %s", n, exc)


def _validate_stream_url(url: str) -> None:
    """Reject HTTPS (expired Bose TLS certs) and playlist container files."""
    if url.startswith("https://"):
        raise BoseError(
            "Stream URL must be HTTP not HTTPS — Bose firmware TLS root certs are expired."
        )
    if not url.startswith("http://"):
        raise BoseError(f"Stream URL must start with http://. Got: {url!r}")
    bare = url.lower().split("?")[0].split("#")[0]
    ext  = os.path.splitext(bare)[1]
    if ext in _PLAYLIST_EXTS:
        raise BoseError(
            f"Bose firmware cannot parse playlist files ({ext}). "
            "Provide a direct audio stream URL (.mp3, .aac, or bare shoutcast endpoint). "
            "Tip: open the playlist in a text editor — the raw http:// URL is inside."
        )


# HTTPS and HLS streams can't be played by the firmware itself (expired TLS
# root certs, no HLS demuxer), so the station resolver hands the speaker our
# /relay endpoint instead, which re-streams them as plain-HTTP MP3 via ffmpeg.
_RELAY_EXTS = frozenset({".m3u8"})


def _url_ext(url: str) -> str:
    return os.path.splitext(url.lower().split("?")[0].split("#")[0])[1]


def needs_relay(url: str) -> bool:
    return url.startswith("https://") or _url_ext(url) in _RELAY_EXTS


def speaker_stream_url(url: str) -> str:
    """The URL the speaker should fetch: the stream itself, or our relay of it."""
    if needs_relay(url):
        return f"{_SERVER_URL}/relay?url={quote(url, safe='')}"
    return url


def validate_playable_url(url: str) -> None:
    """Like _validate_stream_url, but allows what our /relay can handle."""
    if not url.startswith(("http://", "https://")):
        raise BoseError(f"Stream URL must start with http:// or https://. Got: {url!r}")
    ext = _url_ext(url)
    if ext in _PLAYLIST_EXTS and ext not in _RELAY_EXTS:
        raise BoseError(
            f"Playlist files ({ext}) aren't supported. "
            "Provide a direct audio stream URL (.mp3, .aac, .m3u8 or bare shoutcast endpoint). "
            "Tip: open the playlist in a text editor — the raw stream URL is inside."
        )


_MIME_BY_EXT = {
    ".mp3":  "audio/mpeg",
    ".aac":  "audio/aac",
    ".ogg":  "audio/ogg",
    ".flac": "audio/flac",
    ".m4a":  "audio/mp4",
    ".wav":  "audio/wav",
}


def _infer_mime(url: str) -> str:
    """Guess a protocolInfo MIME type from the stream URL's extension."""
    bare = url.lower().split("?")[0].split("#")[0]
    ext  = os.path.splitext(bare)[1]
    return _MIME_BY_EXT.get(ext, "audio/mpeg")


# ── Root Telnet Shell (port 23) ───────────────────────────────────────────────
#
# The speaker (hostname: spotty) runs BusyBox Linux. After executing
# `remote_services on` via port 17000, Telnet on port 23 presents an
# unauthenticated login that accepts "root" with no password and drops
# into an ash shell as root@spotty.
#
# Filesystem note: the speaker mounts its overlay filesystem read-only by
# default. The `rw` command (a Bose-provided shell alias) remounts it
# writable. We call it automatically after every new connection.
#
# Command execution uses a unique sentinel string so we know precisely
# when a command's output has ended, regardless of prompt format.

_PORT_17000 = int(os.getenv("BOSE_CONSOLE_PORT", "17000"))  # Bose diagnostic CLI — always open, no remote_services needed


async def _enable_remote_services(host: str) -> None:
    """
    Send `remote_services on` to the speaker's diagnostic CLI on port 17000.

    Port 17000 exposes a `->`-prompt CLI that is ALWAYS accessible regardless
    of remote_services state.  This function re-opens port 23 after a reboot
    clears the volatile remote_services flag.

    Port 17000 protocol:
      1. Connect TCP
      2. Receive a banner ending with "->"
      3. Send "remote_services on\\r\\n"
      4. Port 23 becomes available within ~2 seconds
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, _PORT_17000),
            timeout=8.0,
        )
    except Exception as exc:
        raise BoseError(
            f"Cannot reach port 17000 on {host}: {exc}. "
            "Manually run: nc {host} 17000  then type: remote_services on"
        ) from exc

    try:
        # Read banner — wait up to 5 s for the "->" prompt
        buf = b""
        deadline = asyncio.get_event_loop().time() + 5.0
        with contextlib.suppress(asyncio.TimeoutError):
            while b"->" not in buf:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                chunk = await asyncio.wait_for(reader.read(256), timeout=remaining)
                if not chunk:
                    break
                buf += chunk
        log.debug("Port 17000 banner: %r", buf.decode("utf-8", errors="replace").strip())

        writer.write(b"remote_services on\r\n")
        await writer.drain()

        # Brief wait for the command to be processed
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(reader.read(256), timeout=2.0)

        log.info("Port 17000: remote_services on sent — waiting 3 s for port 23 to open")
        await asyncio.sleep(3.0)

    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def _query_port17000(host: str, commands: list[str], delay: float = 0.0) -> dict[str, str]:
    """
    Open one session to the port-17000 diagnostic CLI (the `->`-prompt shell)
    and send each command in `commands`, capturing the output that accrues
    before the next `->` prompt.

    Used to probe for source-state / UPnP-activation commands (e.g. `sources`,
    `source`, `help`) — the port 17000 CLI is a different command set from the
    BusyBox ash shell on port 23.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, _PORT_17000),
            timeout=8.0,
        )
    except Exception as exc:
        raise BoseError(f"Cannot reach port 17000 on {host}: {exc}") from exc

    results: dict[str, str] = {}
    try:
        async def _read_until_prompt(timeout: float) -> str:
            buf = b""
            deadline = asyncio.get_event_loop().time() + timeout
            with contextlib.suppress(asyncio.TimeoutError):
                while b"->" not in buf:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break
                    chunk = await asyncio.wait_for(reader.read(512), timeout=remaining)
                    if not chunk:
                        break
                    buf += chunk
            return buf.decode("utf-8", errors="replace")

        # Consume the initial banner / prompt
        await _read_until_prompt(5.0)

        for cmd in commands:
            writer.write(f"{cmd}\r\n".encode())
            await writer.drain()
            out = await _read_until_prompt(4.0)
            results[cmd] = out.strip()
            if delay:
                await asyncio.sleep(delay)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    return results


async def _query_port17000_isolated(host: str, commands: list[str]) -> dict[str, str]:
    """
    Like _query_port17000, but drains a short window of late-arriving
    asynchronous output after each command's "->" prompt, on a single shared
    connection.

    The plain shared-connection version showed response bleed between
    consecutive commands (e.g. an async "Key sent\\n" for command N arriving
    only after the "->OK->" prompt for command N+1 was already read). A
    per-command drain fixes that without the cost of reconnecting for every
    command (47 commands x ~8s reconnect was timing out the whole probe).
    """
    results: dict[str, str] = {}

    async def _read_for(reader: asyncio.StreamReader, timeout: float) -> bytes:
        buf = b""
        deadline = asyncio.get_event_loop().time() + timeout
        with contextlib.suppress(asyncio.TimeoutError):
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                chunk = await asyncio.wait_for(reader.read(512), timeout=remaining)
                if not chunk:
                    break
                buf += chunk
        return buf

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, _PORT_17000),
            timeout=8.0,
        )
    except Exception as exc:
        return {cmd: f"ERROR: {exc}" for cmd in commands}

    try:
        await _read_for(reader, 2.0)  # banner / initial prompt

        for cmd in commands:
            writer.write(f"{cmd}\r\n".encode())
            await writer.drain()

            buf = b""
            deadline = asyncio.get_event_loop().time() + 2.0
            with contextlib.suppress(asyncio.TimeoutError):
                while b"->" not in buf:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break
                    chunk = await asyncio.wait_for(reader.read(512), timeout=remaining)
                    if not chunk:
                        break
                    buf += chunk

            buf += await _read_for(reader, 0.5)  # drain late async output

            results[cmd] = buf.decode("utf-8", errors="replace").strip()
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    return results


class _BoseShell:
    """
    Persistent async root-shell manager over Telnet (port 23).

    Usage:
        output = await _shell.exec("cat /etc/hostname")

    Connects lazily on first exec(), reconnects automatically on drop.
    A per-instance Lock serialises commands so callers never interleave.
    """

    _PORT    = 23
    _SENTINEL = "BOSE_TS_DONE_7F4A"  # unique, shell-safe, no special chars
    _CONNECT_TIMEOUT = 12.0
    _CMD_TIMEOUT     = 25.0
    _READ_CHUNK      = 1024

    def __init__(self, host: str) -> None:
        self._host      = host
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock      = asyncio.Lock()
        self._connected = False

    # ── connection lifecycle ───────────────────────────────────────────────

    async def _open(self) -> None:
        """
        Open a Telnet connection and authenticate as root.

        `remote_services on` (the command that opens port 23) is volatile —
        it resets on every speaker reboot.  If port 23 is refused we
        automatically re-enable it by sending the command to port 17000
        (the diagnostic CLI, which is always open) then retry once.
        """
        if self._writer:
            with contextlib.suppress(Exception):
                self._writer.close()
                await self._writer.wait_closed()
        self._reader = self._writer = None
        self._connected = False

        log.info("Shell: connecting to %s:%d …", self._host, self._PORT)
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._PORT),
                timeout=self._CONNECT_TIMEOUT,
            )
        except (ConnectionRefusedError, OSError) as exc:
            if getattr(exc, "errno", None) == 111 or isinstance(exc, ConnectionRefusedError):
                # Port 23 is closed — remote_services was lost on reboot.
                # Re-activate it via port 17000 then retry the connection once.
                log.warning(
                    "Shell: port %d refused — "
                    "remote_services lost on reboot; re-enabling via port 17000 …",
                    self._PORT,
                )
                await _enable_remote_services(self._host)
                log.info("Shell: retrying port %d after remote_services on …", self._PORT)
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._PORT),
                    timeout=self._CONNECT_TIMEOUT,
                )
            else:
                raise

        # Consume any banner then wait for the login prompt
        await self._absorb_until(b"login:", timeout=8.0)

        self._writer.write(b"root\n")
        await self._writer.drain()

        # Wait for the shell prompt; detect a password challenge
        tail = await self._absorb_until(b"# ", timeout=8.0)
        if b"password" in tail.lower() or b"Password" in tail:
            raise BoseError(
                "Telnet root login requires a password — "
                "remote_services on was not accepted by port 17000."
            )

        self._connected = True
        log.info("Shell: root session open on %s", self._host)

        # Disable terminal echo — prevents readline line-wrap artifacts.
        with contextlib.suppress(Exception):
            await self._run_raw("stty -echo", timeout=4.0)

        # Make the overlay filesystem writable for this session.
        try:
            out = await self._run_raw("rw", timeout=6.0)
            log.debug("Shell: rw → %r", out[:80])
        except Exception as exc:
            log.warning("Shell: `rw` failed (%s) — writes may be refused", exc)

        # Make remote_services permanent so future reboots don't close port 23.
        # The firmware checks for /mnt/nv/remote_services at boot and enables
        # the service automatically when the file exists.
        with contextlib.suppress(Exception):
            await self._run_raw(
                "test -f /mnt/nv/remote_services "
                "|| touch /mnt/nv/remote_services",
                timeout=5.0,
            )
            log.info("Shell: /mnt/nv/remote_services ensured (port 23 survives reboots)")

    async def _absorb_until(self, marker: bytes, timeout: float = 10.0) -> bytes:
        """Read from the stream until `marker` appears; return everything read."""
        buf      = b""
        deadline = asyncio.get_event_loop().time() + timeout
        while marker not in buf:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"Timeout waiting for {marker!r} "
                    f"(last 200 bytes: {buf[-200:]!r})"
                )
            chunk = await asyncio.wait_for(
                self._reader.read(self._READ_CHUNK),
                timeout=min(remaining, 2.0),
            )
            if not chunk:
                raise ConnectionError("Shell: speaker closed the Telnet connection")
            buf += chunk
        return buf

    # ── command execution ─────────────────────────────────────────────────

    async def _run_raw(self, cmd: str, timeout: float = _CMD_TIMEOUT) -> str:
        """
        Send `cmd` to the shell, read until sentinel, return cleaned output.
        Caller must hold self._lock and ensure connection is open.
        """
        # A leading space suppresses the command in ash's history file.
        payload = f" {cmd} 2>&1 ; echo {self._SENTINEL}\n"
        self._writer.write(payload.encode())
        await self._writer.drain()

        buf        = b""
        sentinel_b = self._SENTINEL.encode()
        deadline   = asyncio.get_event_loop().time() + timeout

        while sentinel_b not in buf:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                self._connected = False
                raise asyncio.TimeoutError(
                    f"Command timed out after {timeout}s: {cmd!r}"
                )
            chunk = await asyncio.wait_for(
                self._reader.read(self._READ_CHUNK),
                timeout=min(remaining, 2.0),
            )
            if not chunk:
                raise ConnectionError("Shell: connection closed mid-command")
            buf += chunk

        text   = buf.decode("utf-8", errors="replace")
        # Everything before the first sentinel occurrence is our output
        output = text.split(self._SENTINEL)[0]
        lines  = output.splitlines()

        # Remove the echoed command (ash echoes input) and leading blank lines
        short_cmd = cmd.strip()[:30]
        while lines and (
            not lines[0].strip()
            or short_cmd in lines[0]
            or self._SENTINEL[:8] in lines[0]
        ):
            lines.pop(0)

        return "\n".join(lines).strip()

    async def exec(self, cmd: str, timeout: float = _CMD_TIMEOUT) -> str:
        """
        Execute a shell command and return its stdout+stderr.
        Reconnects once automatically if the session has dropped.
        Raises BoseError on persistent failure.
        """
        async with self._lock:
            for attempt in range(2):
                try:
                    if not self._connected:
                        await self._open()
                    return await self._run_raw(cmd, timeout=timeout)
                except (ConnectionError, asyncio.TimeoutError, OSError) as exc:
                    self._connected = False
                    self._reader = self._writer = None
                    if attempt == 0:
                        log.warning(
                            "Shell: session lost (%s) — reconnecting for retry", exc
                        )
                        continue
                    raise BoseError(
                        f"Shell exec failed after reconnect: {exc}"
                    ) from exc
            raise BoseError("Shell exec: unexpected exit from retry loop")

    async def close(self) -> None:
        """Gracefully close the Telnet connection."""
        self._connected = False
        if self._writer:
            with contextlib.suppress(Exception):
                self._writer.close()
                await self._writer.wait_closed()
        self._reader = self._writer = None


# One persistent root shell per device; `_shell` forwards to the current one.
_shells: dict[str, _BoseShell] = {}


class _ShellProxy:
    def __getattr__(self, name):
        h = host()
        if h not in _shells:
            _shells[h] = _BoseShell(h)
        return getattr(_shells[h], name)


_shell = _ShellProxy()


async def close_shells() -> None:
    for sh in _shells.values():
        await sh.close()


# ── Filesystem helpers ────────────────────────────────────────────────────────

async def _write_file(remote_path: str, content: str) -> None:
    """
    Write `content` to `remote_path` on the speaker's filesystem.

    Uses base64 piped through `base64 -d` to avoid all shell-escaping issues.
    The speaker's BusyBox includes `base64` (applet since BusyBox 1.23, 2014).
    Falls back to `printf '%s'` if base64 is not available.
    """
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    # BusyBox base64 -d reads from stdin
    cmd = f"echo '{b64}' | base64 -d > {remote_path}"
    out = await _shell.exec(cmd, timeout=10.0)

    # Verify write succeeded (check file exists and is non-empty)
    check = await _shell.exec(f"wc -c < {remote_path} 2>/dev/null || echo MISSING")
    if "MISSING" in check or check.strip() == "0":
        # base64 might not be available — fall back to printf
        log.warning("Shell: base64 write may have failed (%r) — trying printf", out)
        # Escape single quotes in content for use inside single-quoted shell arg
        safe = content.replace("'", "'\\''")
        await _shell.exec(f"printf '%s' '{safe}' > {remote_path}", timeout=10.0)

    log.debug("Shell: wrote %d bytes to %s", len(content), remote_path)


async def _write_file_bytes(remote_path: str, data: bytes) -> None:
    """Like _write_file, but for arbitrary binary payloads (no utf-8 decode)."""
    b64 = base64.b64encode(data).decode("ascii")
    await _shell.exec(f"echo '{b64}' | base64 -d > {remote_path}", timeout=10.0)
    log.debug("Shell: wrote %d binary bytes to %s", len(data), remote_path)


# The anonymous account userId provisioned by our BMX /account endpoint.
# Used by patch_sources_xml() to align the Sources.xml sourceKey account
# field with the userId our /bmx/orion/account endpoint returns.
# Must match _BMX_ANON_USER_ID in main.py exactly.
_BMX_ANON_USER_ID = "touchingsounds-anon-001"

# Canonical LOCAL_INTERNET_RADIO / TUNEIN source tokens: base64 of
# {"serial": "<name>"}, as minted by AfterTouch (GenerateSerialSecret).
def serial_secret(serial: str) -> str:
    # Compact separators: byte-identical to Go's json.Marshal output.
    payload = json.dumps({"serial": serial}, separators=(",", ":"))
    return base64.b64encode(payload.encode()).decode("ascii")

LOCAL_RADIO_SECRET = serial_secret("local-internet-radio")


# ── BMX registry configuration (one-time, survives reboots) ──────────────────
#
# Error 1005 UNKNOWN_SOURCE_ERROR means LOCAL_INTERNET_RADIO is not in the
# speaker's runtime source table.  The firmware builds that table at boot by
# fetching bmxRegistryUrl and parsing its JSON.  The factory default points to
# a dead Bose cloud server, so the table never gets the LOCAL_INTERNET_RADIO
# entry (id.value=11) and every /select with that source returns 1005.
#
# Fix: redirect bmxRegistryUrl to our FastAPI server, which serves the exact
# BMX registry JSON format confirmed from Soundploy's live server.  We write
# /mnt/nv/OverrideSdkPrivateCfg.xml (override file takes precedence over the
# read-only /opt/Bose/etc/SoundTouchSdkPrivateCfg.xml without touching it).
# A reboot is required once for the change to take effect.

_SDK_CFG_PATH      = "/opt/Bose/etc/SoundTouchSdkPrivateCfg.xml"
_SDK_OVERRIDE_PATH = "/mnt/nv/OverrideSdkPrivateCfg.xml"
_BMX_REGISTRY_PATH = "/bmx/registry/v1/services"
_MARGE_PATH        = "/marge"   # app/marge.py serves /marge/streaming/...

# Default guess — overridden by _find_sources_xml() at runtime.
# v27 on some hardware mounts /mnt/nv with a different internal structure.
_SOURCES_XML_DEFAULT = "/mnt/nv/BoseApp-Persistence/1/Sources.xml"

# Candidate paths ordered by likelihood across known firmware variants.
_SOURCES_XML_CANDIDATES = (
    "/mnt/nv/BoseApp-Persistence/1/Sources.xml",   # most common
    "/mnt/nv/BoseApp-Persistence/Sources.xml",      # variant without index dir
    "/mnt/nv/Sources.xml",                          # flat layout
    "/mnt/nv/persistence/Sources.xml",              # some v27 builds
    "/mnt/nv/data/Sources.xml",                     # alternate data dir
    "/mnt/nv/BoseApp/Sources.xml",                  # another variant
)


async def _find_sources_xml() -> str | None:
    """
    Discover the actual path of Sources.xml on this firmware variant.

    Strategy:
      1. Probe candidate paths in order — fast, no filesystem walk.
      2. Fall back to a recursive `find` across /mnt and /opt if all
         candidates miss, to catch completely non-standard layouts.

    Returns the absolute path of the first readable file that contains
    a <sources> root element, or None if the file cannot be found.
    """
    # Step 1 — probe known candidates (each is a single cheap stat+head)
    probe_cmd = " || ".join(
        f"(test -f {p} && echo FOUND:{p})" for p in _SOURCES_XML_CANDIDATES
    ) + " || echo NOTFOUND"
    result = await _shell.exec(probe_cmd, timeout=8.0)
    for line in result.splitlines():
        line = line.strip()
        if line.startswith("FOUND:"):
            path = line[len("FOUND:"):]
            log.info("Sources.xml found at candidate path: %s", path)
            return path

    # Step 2 — recursive find (slower, but catches non-standard layouts)
    log.info("Candidate probe missed — running recursive find for Sources.xml …")
    find_cmd = (
        "find /mnt /opt /tmp -maxdepth 8 "
        r"\( -name 'Sources.xml' -o -name 'sources.xml' \) "
        "2>/dev/null | head -10"
    )
    find_result = await _shell.exec(find_cmd, timeout=20.0)
    paths = [
        p.strip() for p in find_result.splitlines()
        if p.strip() and p.strip().startswith("/")
    ]
    log.info("find results: %s", paths)

    # Prefer paths under /mnt/nv (writable persistent NV storage)
    nv_paths = [p for p in paths if "/mnt/nv" in p]
    candidates = nv_paths + [p for p in paths if p not in nv_paths]

    for path in candidates:
        # Quick content check: a real Sources.xml contains a <sources> root
        head = await _shell.exec(f"head -5 {path} 2>/dev/null")
        if "<sources" in head or "<Sources" in head:
            log.info("Sources.xml confirmed at: %s", path)
            return path

    log.warning("Sources.xml not found anywhere on this firmware — paths found: %s", paths)
    return None


async def _configure_cloud_urls(redirect_marge: bool) -> bool:
    """
    Redirect the speaker's bmxRegistryUrl (and margeServerUrl) to our server.

    1. Read the factory SoundTouchSdkPrivateCfg.xml (logged for visibility).
    2. Replace bmxRegistryUrl — and, if redirect_marge, margeServerUrl — with
       our URLs and write the result to /mnt/nv/OverrideSdkPrivateCfg.xml.
       The firmware loads this override at boot in preference to the
       read-only original (same mechanism soundcork uses).
    3. Skip if the existing override is already identical (idempotent).
    4. Signal that a reboot is needed — the speaker only reads these URLs at
       boot.  setup_speaker() issues the single reboot.

    margeServerUrl is the one that matters: marge hands the speaker its
    source table at boot.  Pointing it at a dead server (Bose's) is why
    every LOCAL_INTERNET_RADIO /select returned 1005.  redirect_marge is
    False when app/marge.py has no source snapshot to serve yet, so we
    never point the speaker at a marge that would answer with no SPOTIFY.

    Returns True if the override changed (reboot needed).
    Raises BoseError if the config file cannot be read or a tag is absent.
    """
    targets = {"bmxRegistryUrl": f"{_SERVER_URL}{_BMX_REGISTRY_PATH}"}
    if redirect_marge:
        targets["margeServerUrl"] = f"{_SERVER_URL}{_MARGE_PATH}"

    # Step 1 — read the factory config file and log it for operator visibility
    current_xml = await _shell.exec(f"cat {_SDK_CFG_PATH}")
    log.info("SoundTouchSdkPrivateCfg.xml (factory):\n%s", current_xml)

    if not current_xml.strip():
        raise BoseError(
            f"Cannot read {_SDK_CFG_PATH} — check that the shell is connected "
            "and rw is active."
        )

    # Step 2 — build override XML: copy of factory file with our URLs swapped in
    override_xml = current_xml
    for tag, url in targets.items():
        tag_re = re.compile(rf"<{tag}>[^<]*</{tag}>")
        if not tag_re.search(override_xml):
            raise BoseError(
                f"<{tag}> tag not found in {_SDK_CFG_PATH}. "
                "Raw content logged above — adjust _SDK_CFG_PATH or tag regex."
            )
        override_xml = tag_re.sub(f"<{tag}>{url}</{tag}>", override_xml)

    # Step 3 — idempotency check against the override already on disk
    existing_override = await _shell.exec(
        f"cat {_SDK_OVERRIDE_PATH} 2>/dev/null || echo MISSING"
    )
    if all(f"<{tag}>{url}</{tag}>" in existing_override for tag, url in targets.items()) \
            and (redirect_marge or "/marge</margeServerUrl>" not in existing_override):
        log.info("OverrideSdkPrivateCfg.xml already set — skipping reboot")
        return False

    await _write_file(_SDK_OVERRIDE_PATH, override_xml)

    # Verify the write landed correctly
    verify = await _shell.exec(
        f"grep -o -e 'bmxRegistryUrl.[^<]*' -e 'margeServerUrl.[^<]*' {_SDK_OVERRIDE_PATH}"
    )
    log.info("Override written:\n%s", verify.strip())

    # Step 4 — the caller (setup_speaker) issues the single reboot
    return True


async def patch_sources_xml() -> bool:
    """
    Patch Sources.xml so LOCAL_INTERNET_RADIO is treated as a local,
    no-authentication-required source — exactly like AUX.

    WHY THIS IS NEEDED
    ──────────────────
    The firmware builds its runtime source table from Sources.xml at boot,
    not from the BMX registry (the registry only provides content-browsing
    metadata).  The factory entry for LOCAL_INTERNET_RADIO is:

        <source secret="eyJzZXJpYWwiOiAi..." secretType="token">
            <sourceKey type="LOCAL_INTERNET_RADIO" account="" />
        </source>

    The base64 secret decodes to {"serial": "<cloud-uuid>"}.  The firmware
    POSTs this serial to the Bose cloud token-validation endpoint at boot.
    That endpoint is dead.  Validation fails.  Source is excluded from the
    runtime table.  Every /select call with source=LOCAL_INTERNET_RADIO
    returns 1005 UNKNOWN_SOURCE_ERROR regardless of bmxRegistryUrl state.

    THE FIX
    ───────
    Clear secret and secretType — the same values AUX carries.  The firmware
    then adds LOCAL_INTERNET_RADIO to the runtime table unconditionally (no
    cloud call attempted).

    The <sourceKey account="..."> field becomes the required sourceAccount
    attribute in ContentItem XML.  We set it to _BMX_ANON_USER_ID so it
    aligns with presets already stored by setup_speaker().

    Also clears the dead INTERNET_RADIO token for the same reason.

    Returns True if the file was modified (reboot required).
    Returns False if already patched (idempotent).
    Raises BoseError if the file cannot be read or has no recognisable entries.
    """
    # Discover the real path — do not assume a hardcoded location.
    sources_path = await _find_sources_xml()
    if not sources_path:
        raise BoseError(
            "Sources.xml not found on any known path. "
            "Call GET /api/diagnose/filesystem to see the full "
            "/mnt/nv directory tree and paste the output here."
        )

    raw = await _shell.exec(f"cat {sources_path} 2>/dev/null || echo NOTFOUND")

    if "NOTFOUND" in raw or not raw.strip():
        raise BoseError(
            f"Sources.xml found at {sources_path} but cannot be read — "
            "the path may be on a read-only partition. "
            "Run GET /api/diagnose/filesystem for partition details."
        )

    log.info("Sources.xml before patch:\n%s", raw)

    # Parse — the file uses standard UTF-8 XML
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise BoseError(f"Sources.xml is malformed: {exc}") from exc

    # Idempotency check: if LOCAL_INTERNET_RADIO already carries the canonical
    # token and empty account, there is nothing to do.
    already_patched = all(
        sk.get("type") != "LOCAL_INTERNET_RADIO"
        or (
            source_el.get("secret") == LOCAL_RADIO_SECRET
            and source_el.get("secretType") == "token"
            and sk.get("account") == ""
        )
        for source_el in root.findall("source")
        for sk in source_el.findall("sourceKey")
    )
    if already_patched and any(
        sk.get("type") == "LOCAL_INTERNET_RADIO"
        for source_el in root.findall("source")
        for sk in source_el.findall("sourceKey")
    ):
        log.info("Sources.xml already patched — skipping")
        return False

    local_ir_found = False

    for source_el in root.findall("source"):
        for sk in source_el.findall("sourceKey"):
            src_type = sk.get("type", "")

            if src_type == "LOCAL_INTERNET_RADIO":
                # Canonical entry (matches what app/marge.py serves): a
                # {"serial": "local-internet-radio"} token and empty account.
                # Undoes the 0.4.x patch (blank token, account=
                # touchingsounds-anon-001), which never worked.
                source_el.set("secret", LOCAL_RADIO_SECRET)
                source_el.set("secretType", "token")
                sk.set("account", "")
                local_ir_found = True
                log.info("Patched LOCAL_INTERNET_RADIO to canonical token")

            elif src_type == "INTERNET_RADIO":
                # Same dead cloud dependency — clear it for consistency
                source_el.set("secret", "")
                source_el.set("secretType", "")
                log.info("Patched INTERNET_RADIO: cleared dead token")

    if not local_ir_found:
        # Entry absent — add it fresh so the firmware can register the source
        log.warning(
            "LOCAL_INTERNET_RADIO not found in Sources.xml — adding new entry"
        )
        new_src = ET.SubElement(root, "source", attrib={
            "secret": LOCAL_RADIO_SECRET,
            "secretType": "token",
        })
        ET.SubElement(new_src, "sourceKey", attrib={
            "type":    "LOCAL_INTERNET_RADIO",
            "account": "",
        })

    # Serialise.  ET.indent() (Python 3.9+) preserves human-readable formatting.
    # The firmware's XML parser is not whitespace-sensitive, so reformatting is safe.
    ET.indent(root, space="    ")
    patched_xml = '<?xml version="1.0" encoding="UTF-8" ?>\n' + ET.tostring(
        root, encoding="unicode"
    )

    log.info("Sources.xml after patch:\n%s", patched_xml)
    await _write_file(sources_path, patched_xml)
    log.info("Sources.xml written to %s — reboot required to apply", sources_path)
    return True


# ── Speaker setup (called once at startup) ────────────────────────────────────

async def read_sources_xml() -> str:
    """Return the speaker's on-disk Sources.xml (includes SPOTIFY/ALEXA secrets)."""
    sources_path = await _find_sources_xml()
    if not sources_path:
        raise BoseError("Sources.xml not found on the speaker")
    raw = await _shell.exec(f"cat {sources_path} 2>/dev/null")
    if "<sources" not in raw:
        raise BoseError(f"Unexpected Sources.xml content at {sources_path}: {raw[:200]!r}")
    return raw[raw.index("<"):]


# ── Device identity (deviceID, account) ──────────────────────────────────────
#
# marge requests name an account id and sometimes a deviceID, never an IP, so
# keep each device's /info identity to route them to the right speaker.

# Persisted so a restart can route a booting speaker's first marge request
# before its /info answers again.
_IDENTITY_FILE = os.path.join(os.getenv("TS_DATA_DIR", "data"), "identities.json")


def _load_identities() -> dict[str, dict]:
    try:
        with open(_IDENTITY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {h: v for h, v in data.items() if h in _DEVICES_BY_HOST and isinstance(v, dict)}
    except (OSError, ValueError):
        return {}


def _save_identities() -> None:
    with contextlib.suppress(OSError):
        os.makedirs(os.path.dirname(_IDENTITY_FILE) or ".", exist_ok=True)
        tmp = _IDENTITY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_IDENTITY, f, indent=2)
        os.replace(tmp, _IDENTITY_FILE)


_IDENTITY: dict[str, dict] = _load_identities()
_ONLINE: dict[str, bool] = {}


def _parse_identity(xml: str) -> dict:
    root = ET.fromstring(xml)
    return {
        "deviceID": root.get("deviceID", ""),
        "name":     root.findtext("name", ""),
        "type":     root.findtext("type", ""),
        "account":  root.findtext("margeAccountUUID", ""),
    }


async def refresh_identity(h: str) -> dict:
    """Fetch and cache device `h`'s identity; keeps the last copy if it's offline."""
    with use_device(h):
        try:
            ident = _parse_identity(await info())
            _ONLINE[h] = True
            if ident != _IDENTITY.get(h):
                _IDENTITY[h] = ident
                _save_identities()
        except (BoseError, ET.ParseError) as exc:
            if _ONLINE.get(h, True):
                log.warning("Device %s: /info unavailable (%s)", h, exc)
            _ONLINE[h] = False
    return _IDENTITY.get(h, {})


def is_online(h: str) -> bool:
    """Whether device `h` answered the last identity refresh."""
    return _ONLINE.get(h, False)


async def refresh_identities() -> None:
    await asyncio.gather(*(refresh_identity(d.host) for d in DEVICES))


def identity(h: str | None = None) -> dict:
    return _IDENTITY.get(h or host(), {})


def hosts_for_account(account: str) -> list[str]:
    hosts = [d.host for d in DEVICES if _IDENTITY.get(d.host, {}).get("account") == account]
    if not hosts and len(DEVICES) == 1 and not _IDENTITY.get(DEVICES[0].host):
        # Single speaker whose identity we've never seen: it's the one asking (1.0 behaviour).
        hosts = [DEVICES[0].host]
    return hosts


def host_for_device_id(device_id: str) -> str | None:
    for d in DEVICES:
        if _IDENTITY.get(d.host, {}).get("deviceID", "").upper() == device_id.upper():
            return d.host
    return None


# ── Setup via the port-17000 console (devices marked :fresh) ─────────────────
#
# Firmware without `remote_services` (e.g. the Wireless Link Adapter on 26.0.1)
# has no root shell for OverrideSdkPrivateCfg.xml, but its console accepts
# `sys configuration <key> "<url>"` (runtime layer) and `envswitch boseurls
# set "<marge>" "<swUpdate>"`, which commits the runtime layer so it survives
# a reboot.  Order matters: all `sys configuration` writes first, envswitch
# last.  Method and pacing from AfterTouch (gesellix/bose-soundtouch, MIT,
# docs/analysis/TELNET-COMMAND-REFERENCE.md).

_CONSOLE_DELAY = 3.0  # AfterTouch's DefaultTelnetCommandDelay


def _pdo_value(text: str, key: str) -> str:
    m = re.search(key + r'\s*\{\s*text:\s*"([^"]*)"', text)
    return m.group(1) if m else ""


async def _console_config(h: str) -> str:
    cmd = "getpdo CurrentSystemConfiguration"
    return (await _query_port17000(h, [cmd])).get(cmd, "")


def our_cloud_urls() -> dict[str, str]:
    """The marge and BMX registry URLs that point a speaker at this server."""
    return {"marge": f"{_SERVER_URL}{_MARGE_PATH}",
            "bmx":   f"{_SERVER_URL}/bmx/registry/v1/services"}


async def console_cloud_urls(h: str) -> dict[str, str]:
    """Device `h`'s current marge and BMX registry URLs, read through the console."""
    cfg = await _console_config(h)
    return {"marge": _pdo_value(cfg, "margeServerUrl"), "bmx": _pdo_value(cfg, "bmxRegistryUrl")}


async def setup_via_console(h: str) -> bool:
    """
    Point device `h`'s marge and BMX registry URLs at this server through the
    console.  Idempotent; returns True if the device must reboot to apply.
    The stats and software-update URLs are left as they are.
    """
    urls = our_cloud_urls()
    return await redirect_via_console(h, urls["marge"], urls["bmx"])


async def redirect_via_console(h: str, marge: str, bmx: str) -> bool:
    """
    Set device `h`'s marge and BMX registry URLs through the console (also
    used to undo the redirect when a speaker is removed).  Idempotent;
    returns True if the device must reboot to apply.
    """
    cfg = await _console_config(h)
    cur_marge, cur_bmx = _pdo_value(cfg, "margeServerUrl"), _pdo_value(cfg, "bmxRegistryUrl")
    stats, swu = _pdo_value(cfg, "statsServerUrl"), _pdo_value(cfg, "swUpdateUrl")
    if cur_marge == marge and cur_bmx == bmx:
        log.info("Device %s: cloud URLs already set — nothing to do", h)
        return False
    if not (cur_marge and swu):
        raise BoseError(f"Device {h}: couldn't read its configuration via getpdo: {cfg[:200]!r}")

    cmds = [
        f'sys configuration bmxRegistryUrl "{bmx}"',
        f'sys configuration statsServerUrl "{stats}"',
        f'sys configuration margeServerUrl "{marge}"',
        f'sys configuration swUpdateUrl "{swu}"',
        f'envswitch boseurls set "{marge}" "{swu}"',
    ]
    log.info("Device %s: redirecting marge %s -> %s and bmx %s -> %s via console",
             h, cur_marge, marge, cur_bmx, bmx)
    results = await _query_port17000(h, cmds, delay=_CONSOLE_DELAY)
    for cmd, out in results.items():
        log.info("Device %s console: %s -> %r", h, cmd, out[:120])
        if "not found" in out.lower() or "invalid" in out.lower():
            raise BoseError(f"Device {h} rejected {cmd!r}: {out[:120]!r}")

    if _pdo_value(await _console_config(h), "margeServerUrl") != marge:
        raise BoseError(f"Device {h}: margeServerUrl didn't take after the console writes")
    return True


async def reboot_and_wait(h: str, timeout: float = 180.0) -> bool:
    """Reboot device `h` via the console, then wait until /info answers again."""
    log.info("Device %s: rebooting via console …", h)
    with contextlib.suppress(BoseError, OSError):
        await _query_port17000(h, ["sys reboot"])
    await asyncio.sleep(15)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    with use_device(h):
        while loop.time() < deadline:
            with contextlib.suppress(BoseError):
                await info()
                # /info answers before marge/sources are settled (~70 s median
                # to full readiness per AfterTouch's measurements)
                await asyncio.sleep(20)
                log.info("Device %s: back online", h)
                return True
            await asyncio.sleep(5)
    log.warning("Device %s: not back within %.0f s — power-cycle it if radio doesn't work", h, timeout)
    return False


# ── Name and Wi-Fi (the "Add a speaker" wizard, app/setup.py) ────────────────
#
# Calls from the speaker's own API, documented by the SoundTouchPlus wiki
# (thlucas1/homeassistantcomponent_soundtouchplus, "SoundTouch WebServices
# API").  The firmware can't delete a saved Wi-Fi profile (only a factory
# reset clears them) and joins whichever saved network it reaches first.

_SURVEY_TIMEOUT = httpx.Timeout(connect=4.0, read=30.0, write=4.0, pool=2.0)


async def set_name(name: str) -> None:
    await _post("/name", f"<name>{_xml_escape(name)}</name>")


def _wifi_state(xml: str) -> dict:
    """networkInfo -> {"wifi": {ssid, ip, state, signal} | None, "ethernet": ip | ""}."""
    out: dict = {"wifi": None, "ethernet": ""}
    for itf in ET.fromstring(xml).iter("interface"):
        kind, ip = itf.get("type", ""), itf.get("ipAddress", "")
        if kind == "WIFI_INTERFACE" and itf.get("mode", "STATION") == "STATION" and out["wifi"] is None                 and itf.get("state") == "NETWORK_WIFI_CONNECTED":
            out["wifi"] = {"ssid": itf.get("ssid", ""), "ip": ip,
                           "signal": itf.get("signal", ""), "state": itf.get("state", "")}
        elif kind == "ETHERNET_INTERFACE" and ip:
            out["ethernet"] = ip
    return out


async def network_state() -> dict:
    return _wifi_state(await _get("/networkInfo"))


async def wifi_survey() -> list[dict]:
    """The Wi-Fi networks the speaker hears, strongest first, one entry per name."""
    url = f"{_base()}/performWirelessSiteSurvey"
    try:
        async with httpx.AsyncClient(timeout=_SURVEY_TIMEOUT) as c:
            r = await c.get(url)
            r.raise_for_status()
    except Exception as exc:
        raise _classify(exc, url) from exc
    root = ET.fromstring(r.text)
    if root.get("error", "none") not in ("none", ""):
        raise BoseError(f"The speaker couldn't scan for Wi-Fi: {root.get('error')}")
    best: dict[str, dict] = {}
    for item in root.iter("item"):
        ssid = item.get("ssid", "")
        if not ssid:
            continue
        try:
            signal = int(item.get("signalStrength", "-100"))
        except ValueError:
            signal = -100
        types = [t.text for t in item.iter("type") if t.text]
        if ssid not in best or signal > best[ssid]["signal"]:
            best[ssid] = {"ssid": ssid, "signal": signal,
                          "secure": item.get("secure", "true") == "true",
                          "security": types[0] if types else ("wpa_or_wpa2" if item.get("secure") != "false" else "none")}
    return sorted(best.values(), key=lambda n: -n["signal"])


async def add_wifi_profile(ssid: str, password: str, security: str = "wpa_or_wpa2",
                           timeout: int = 30) -> None:
    """Save a Wi-Fi network on the speaker.  The password is never logged."""
    body = (f'<addWirelessProfile timeout="{int(timeout)}">'
            f'<profile ssid="{_xml_escape(ssid, _ATTR)}" password="{_xml_escape(password, _ATTR)}" '
            f'securityType="{_xml_escape(security, _ATTR)}"></profile></addWirelessProfile>')
    url = f"{_base()}/addWirelessProfile"
    log.info("Device %s: adding Wi-Fi profile %r (%s)", host(), ssid, security)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=4.0, read=timeout + 10.0,
                                                           write=4.0, pool=2.0)) as c:
            r = await c.post(url, content=body.encode(), headers={"Content-Type": "application/xml"})
            r.raise_for_status()
    except Exception as exc:
        # Built from the URL only: the request (with the password) stays out of the message.
        raise BoseError(f"The speaker didn't accept the Wi-Fi details ({type(exc).__name__}).") from None
    if "<error" in r.text.lower():
        raise BoseError(f"The speaker rejected the Wi-Fi details: {r.text[:160]}")


_ATTR = {'"': "&quot;", "'": "&apos;"}


async def setup_speaker(redirect_marge: bool) -> None:
    """
    One-time startup routine: patch Sources.xml and redirect the speaker's
    cloud URLs (bmxRegistryUrl, and margeServerUrl if redirect_marge).

    Idempotent — safe to call on every container start. A single reboot is
    triggered when anything changed; subsequent starts skip both. The
    function waits 90 s after issuing reboot before continuing.
    """
    log.info("Speaker setup: starting (redirect_marge=%s) …", redirect_marge)

    sources_rebooted = False
    try:
        if await patch_sources_xml():
            log.info("Sources.xml patched — scheduling reboot")
            sources_rebooted = True
    except BoseError as exc:
        log.error("Sources.xml patch failed (continuing anyway): %s", exc)

    try:
        registry_rebooted = await _configure_cloud_urls(redirect_marge)
    except BoseError as exc:
        log.error("Cloud URL override failed (continuing anyway): %s", exc)
        registry_rebooted = False

    # Issue a single reboot if either step flagged one, then wait for recovery.
    if sources_rebooted or registry_rebooted:
        log.info("Rebooting speaker (Sources.xml and/or cloud URLs changed) …")
        with contextlib.suppress(Exception):
            await _shell.exec("reboot", timeout=4.0)
        await _shell.close()
        log.info("Waiting 90 s for speaker to come back online …")
        await asyncio.sleep(90)
        log.info("Resuming speaker setup after reboot.")

    log.info("Speaker setup: complete")


# ── play_url — internal UPnP AVTransport injector ─────────────────────────────
#
# /select with source=LOCAL_INTERNET_RADIO returns 1005 UNKNOWN_SOURCE_ERROR
# even after Sources.xml is patched — the v27 runtime source table hardcodes
# a block on that source string. UPnP AVTransport (port 8091) is a separate
# code path that does not consult Sources.xml or the source table, so it is
# the only working playback path for arbitrary stream URLs on this firmware.

_UPNP_PORT = 8091
_UPNP_CFG_PATH = "/opt/Bose/etc/UpnpSource-spotty.xml"

# Manual override — set BOSE_UPNP_CONTROL_URL to skip discovery entirely and
# test a specific controlURL (absolute, e.g. "http://localhost:8091/AVTransport/ctrl",
# or path-only, e.g. "/upnp/control/AVTransport1").
_UPNP_CONTROL_URL_OVERRIDE = os.getenv("BOSE_UPNP_CONTROL_URL", "").strip()

# Discovered AVTransport controlURL, cached for the process lifetime.
# Cleared on SOAP failure so the next call rediscovers it.
_upnp_control_url: str | None = None


def _esc_xml(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;")
         .replace(">", "&gt;").replace('"', "&quot;")
    )


async def _discover_upnp_control_url() -> str:
    """
    Discover the AVTransport SOAP controlURL on the speaker's internal UPnP
    renderer (localhost:8091).

    1. Read UpnpSource-spotty.xml for a descriptionURL pointing at the
       device descriptor.
    2. If that's absent or doesn't mention AVTransport, probe common
       descriptor paths directly on localhost:8091.
    3. Parse the <controlURL> inside the AVTransport <service> block.

    Result is cached in _upnp_control_url for the process lifetime.
    """
    global _upnp_control_url
    if _upnp_control_url:
        return _upnp_control_url

    if _UPNP_CONTROL_URL_OVERRIDE:
        control_url = (
            _UPNP_CONTROL_URL_OVERRIDE if _UPNP_CONTROL_URL_OVERRIDE.startswith("http")
            else f"http://localhost:{_UPNP_PORT}{_UPNP_CONTROL_URL_OVERRIDE}"
        )
        log.info("UPnP controlURL override (BOSE_UPNP_CONTROL_URL): %s", control_url)
        _upnp_control_url = control_url
        return _upnp_control_url

    cfg_raw = await _shell.exec(f"cat {_UPNP_CFG_PATH} 2>/dev/null || echo NOTFOUND")
    if "NOTFOUND" in cfg_raw:
        raise BoseError(
            f"UpnpSource config not found at {_UPNP_CFG_PATH}. "
            "Run GET /api/diagnose/filesystem for the actual path."
        )
    log.info("UpnpSource config:\n%s", cfg_raw[:500])

    desc_raw = ""
    desc_url_match = re.search(r'descriptionURL[^>]*>([^<]+)<', cfg_raw)
    if desc_url_match:
        desc_url = desc_url_match.group(1).strip()
        desc_raw = await _shell.exec(f"curl -s -m 5 {desc_url} | head -c 4000")

    if "AVTransport" not in desc_raw:
        for url in (
            f"http://localhost:{_UPNP_PORT}/rootDesc.xml",
            f"http://localhost:{_UPNP_PORT}/description.xml",
            f"http://localhost:{_UPNP_PORT}/device.xml",
        ):
            result = await _shell.exec(f"curl -s -m 3 {url} | head -c 4000")
            if "AVTransport" in result:
                desc_raw = result
                log.info("UPnP descriptor found at %s", url)
                break

    ctrl_match = re.search(
        r"<serviceType>[^<]*AVTransport[^<]*</serviceType>.*?<controlURL>([^<]+)</controlURL>",
        desc_raw, re.DOTALL,
    )
    if not ctrl_match:
        raise BoseError(
            "Cannot extract AVTransport controlURL from UPnP descriptor. "
            "Run GET /api/diagnose/filesystem to inspect the raw UpnpSource "
            "config and descriptor XML."
        )

    control_path = ctrl_match.group(1).strip()
    control_url = (
        control_path if control_path.startswith("http")
        else f"http://localhost:{_UPNP_PORT}{control_path}"
    )
    log.info("UPnP AVTransport controlURL: %s", control_url)
    _upnp_control_url = control_url
    return control_url


async def play_url(stream_url: str, name: str = "Radio") -> None:
    """
    Inject a raw HTTP audio stream via the speaker's internal UPnP
    AVTransport renderer (localhost:8091), called from inside the speaker
    over the root Telnet shell.

    1. Discover (and cache) the AVTransport controlURL.
    2. Build DIDL-Lite metadata and SetAVTransportURI / Play SOAP envelopes,
       write them to temp files (curl @file — never pass XML inline over the
       Telnet TTY, BusyBox ash line-wraps long arguments and corrupts it).
    3. POST SetAVTransportURI, then POST Play.

    The stream URL must be HTTP (not HTTPS) — Bose TLS root certs are expired.
    Playlist files (.m3u8 etc.) are rejected immediately with a clear error.
    """
    global _upnp_control_url
    _validate_stream_url(stream_url)

    log.info("play_url (UPnP): %s  name=%r", stream_url, name)

    try:
        control_url = await _discover_upnp_control_url()
    except BoseError:
        _upnp_control_url = None
        raise

    mime = _infer_mime(stream_url)
    didl = (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="0" parentID="-1" restricted="1">'
        f'<dc:title>{_esc_xml(name)}</dc:title>'
        '<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>'
        f'<res protocolInfo="http-get:*:{mime}:*">{_esc_xml(stream_url)}</res>'
        '</item></DIDL-Lite>'
    )

    set_uri_soap = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        '<u:SetAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
        '<InstanceID>0</InstanceID>'
        f'<CurrentURI>{_esc_xml(stream_url)}</CurrentURI>'
        f'<CurrentURIMetaData>{_esc_xml(didl)}</CurrentURIMetaData>'
        '</u:SetAVTransportURI>'
        '</s:Body></s:Envelope>'
    )
    play_soap = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        '<u:Play xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
        '<InstanceID>0</InstanceID><Speed>1</Speed>'
        '</u:Play>'
        '</s:Body></s:Envelope>'
    )

    tmp_seturi = f"{_HTML_DIR}/tmp_upnp_seturi.xml"
    tmp_play   = f"{_HTML_DIR}/tmp_upnp_play.xml"
    await _write_file(tmp_seturi, set_uri_soap)
    await _write_file(tmp_play,   play_soap)

    # `-H 'Expect:'` disables curl's automatic "Expect: 100-continue" header
    # for POST bodies > 1024 bytes (the DIDL-Lite-laden SetAVTransportURI body
    # routinely crosses that threshold). Tiny embedded HTTP servers (libupnp's
    # miniserver included) generally don't implement 100-continue — they never
    # send the interim "100 Continue", curl waits, the server eventually closes
    # the idle connection, and curl reports "Empty reply from server" (52).
    seturi_cmd = (
        f"curl -s -m 8 -X POST '{control_url}' "
        f"-H 'Content-Type: text/xml; charset=\"utf-8\"' "
        f"-H 'SOAPAction: \"urn:schemas-upnp-org:service:AVTransport:1#SetAVTransportURI\"' "
        f"-H 'Expect:' "
        f"-d '@{tmp_seturi}'"
    )
    r1 = await _shell.exec(seturi_cmd, timeout=12.0)
    log.info("UPnP SetAVTransportURI → %r", r1[:200])

    play_cmd = (
        f"curl -s -m 8 -X POST '{control_url}' "
        f"-H 'Content-Type: text/xml; charset=\"utf-8\"' "
        f"-H 'SOAPAction: \"urn:schemas-upnp-org:service:AVTransport:1#Play\"' "
        f"-H 'Expect:' "
        f"-d '@{tmp_play}'"
    )
    r2 = await _shell.exec(play_cmd, timeout=12.0)
    log.info("UPnP Play → %r", r2[:200])

    if "<errorCode>" in r1 or "<errorCode>" in r2:
        # The cached controlURL may be stale (e.g. after a reboot) — drop it
        # so the next call rediscovers from scratch.
        _upnp_control_url = None
        raise BoseError(
            f"UPnP SOAP error — SetAVTransportURI: {r1[:200]}  Play: {r2[:200]}"
        )


# ── Preset configuration ──────────────────────────────────────────────────────

# Configured via environment variables PRESET_N_URL / PRESET_N_NAME (N=1..6).
# Direct audio streams; https:// and .m3u8 ones go through our /relay.
# These are the defaults; presets saved from the UI (data/presets.json) win.
_PRESET_URLS: dict[int, str] = {
    n: os.getenv(f"PRESET_{n}_URL", "") for n in range(1, 7)
}
_PRESET_NAMES: dict[int, str] = {
    n: os.getenv(f"PRESET_{n}_NAME") or f"Preset {n}" for n in range(1, 7)
}


# ── WebSocket preset listener (port 8080) ─────────────────────────────────────
#
# Physical preset button presses emit <nowSelectionUpdated><preset id="N">
# on ws://speaker:8080 (subprotocol "gabbo").
#
# We intercept them and call play_url() as an immediate override.
# This is a belt-and-suspenders fallback: if setup_speaker() successfully
# reprogrammed the preset slots via storePreset, the button press triggers
# LOCAL_INTERNET_RADIO from localhost autonomously. Our WebSocket interception
# ensures playback works even if storePreset was rejected.

def _parse_ws_preset_id(xml_str: str) -> int | None:
    """Return preset slot 1–6 from a nowSelectionUpdated WebSocket message, or None."""
    try:
        root = ET.fromstring(xml_str)
        for el in root.iter():
            if el.tag.split("}")[-1] == "nowSelectionUpdated":
                for child in el.iter():
                    if child.tag.split("}")[-1] == "preset":
                        try:
                            pid = int(child.get("id", "0"))
                            if 1 <= pid <= 6:
                                return pid
                        except ValueError:
                            pass
    except ET.ParseError:
        pass

    m = re.search(
        r'<nowSelectionUpdated[^>]*>.*?<preset\s+id="([1-6])"',
        xml_str, re.DOTALL,
    )
    return int(m.group(1)) if m else None


async def run_preset_listener(get_presets, device_host: str) -> None:
    """
    Long-running background coroutine: when a physical preset button press
    doesn't end up playing our station for that slot, select it ourselves.

    get_presets() returns slot (str) -> {name, location}, the same preset set
    the UI edits (marge.presets: env defaults overridden by presets.json).
    One listener per device, started at application startup (main.py lifespan).
    Reconnects automatically with exponential backoff (5 s → 60 s ceiling).
    """
    import websockets  # deferred import keeps websockets optional at module load

    _current_host.set(device_host)  # this task's speaker calls go to that device
    uri     = f"ws://{device_host}:8080"
    backoff = 5

    while True:
        try:
            async with websockets.connect(uri, subprotocols=["gabbo"]) as ws:
                log.info("WS preset listener connected: %s", uri)
                backoff = 5
                async for raw in ws:
                    if not isinstance(raw, str):
                        continue
                    preset_id = _parse_ws_preset_id(raw)
                    if preset_id is None:
                        continue
                    preset = get_presets().get(str(preset_id))
                    if not preset:
                        log.warning("Preset %d pressed — slot not configured, ignoring", preset_id)
                        continue
                    # If the speaker's stored preset already is our station, it
                    # plays it itself. Stale slots (e.g. the 0.4.x
                    # localhost:8080/N.json ones) are also LOCAL_INTERNET_RADIO,
                    # so compare the location, not just the source.
                    await asyncio.sleep(3)
                    with contextlib.suppress(BoseError, ET.ParseError):
                        np = ET.fromstring(await now_playing())
                        item = np.find("ContentItem")
                        if item is not None and item.get("location") == preset["location"]:
                            log.info("Preset %d: speaker is playing it natively", preset_id)
                            continue
                    log.info("Preset %d pressed → selecting %r ourselves", preset_id, preset["name"])
                    try:
                        await _post("/select", _content_item_xml(preset["location"], preset["name"]))
                    except BoseError as exc:
                        log.error("Preset %d select failed: %s", preset_id, exc)

        except asyncio.CancelledError:
            log.info("WS preset listener %s: cancelled", device_host)
            return
        except Exception as exc:
            log.warning("WS disconnected: %s — retry in %ds", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ── Diagnostic helper ─────────────────────────────────────────────────────────

async def diagnose() -> dict:
    """
    Probe the speaker's environment via the root shell.
    Useful for verifying the shell works and understanding the filesystem.
    Returns a dict of findings.
    """
    checks = {
        "hostname":   "hostname",
        "html_dir":   f"ls -la {_HTML_DIR}/ 2>&1 | head -30",
        "uptime":     "uptime",
        "df":         "df -h | head -10",
        "processes":  "ps | grep -v '\\[' | head -20",
        "interfaces": "ifconfig 2>/dev/null | grep -A1 'wlan\\|eth' | head -10",
        "curl_test":  f"curl -s -m 3 -o /dev/null -w '%{{http_code}}' http://localhost:{_PORT}/info",
        "base64_ok":  "echo dGVzdA== | base64 -d 2>/dev/null && echo OK || echo MISSING",
    }
    results = {}
    for key, cmd in checks.items():
        try:
            results[key] = await _shell.exec(cmd, timeout=10.0)
        except BoseError as exc:
            results[key] = f"ERROR: {exc}"
    return results


async def diagnose_bmx() -> dict:
    """
    Targeted BMX/source-registration diagnostic.

    Answers the three questions needed to debug 1005 UNKNOWN_SOURCE_ERROR:
      1. Is LOCAL_INTERNET_RADIO actually in the firmware's runtime source table?
      2. What does the on-disk cached registry look like?
      3. What sourceAccount format do existing presets use (TUNEIN reference)?

    Call GET /api/diagnose/bmx and paste the full JSON output when debugging.
    """
    checks = {
        # ── Source table (what the firmware actually knows about) ──────────
        # Port-17000 commands (sys source list, bmx get) don't exist in bash.
        # These are the bash equivalents that probe the same state.
        "sources_api": (
            f"curl -s -m 5 http://localhost:{_PORT}/sources"
        ),
        "presets_api": (
            f"curl -s -m 5 http://localhost:{_PORT}/presets"
        ),

        # ── On-disk registry cache ─────────────────────────────────────────
        # The speaker may cache the fetched registry in various locations.
        # Whichever returns non-empty content is the authoritative copy.
        "registry_nv": (
            "cat /mnt/nv/bmx/registry.json 2>/dev/null "
            "|| cat /mnt/nv/BoseApp-Persistence/1/bmxRegistry.json 2>/dev/null "
            "|| echo NOTFOUND"
        ),
        "registry_opt": (
            "find /opt/Bose -name 'registry*' -o -name '*bmx*' 2>/dev/null "
            "| head -10"
        ),
        "registry_tmp": (
            "find /tmp -name '*registry*' -o -name '*bmx*' 2>/dev/null | head -10"
        ),

        # ── SDK config — verify the override file is live ──────────────────
        "sdk_cfg_current": (
            f"grep -i 'bmxRegistry\\|bmxUrl\\|registryUrl' "
            f"{_SDK_CFG_PATH} 2>/dev/null | head -5"
        ),
        "sdk_override_current": (
            f"grep -i 'bmxRegistry\\|bmxUrl\\|registryUrl' "
            f"{_SDK_OVERRIDE_PATH} 2>/dev/null "
            "|| echo 'override file missing or no match'"
        ),

        # ── Sources.xml — persistent source config on NV storage ──────────
        "sources_xml": (
            "cat /mnt/nv/BoseApp-Persistence/1/Sources.xml 2>/dev/null "
            "|| echo NOTFOUND"
        ),

        # ── BMX-related binaries and running processes ─────────────────────
        "bmx_binaries": (
            "find /opt/Bose/bin /usr/bin /bin -name '*bmx*' -o -name '*source*' "
            "2>/dev/null | head -10"
        ),
        "bmx_processes": (
            "ps | grep -i 'bmx\\|source\\|radio' | grep -v grep | head -10"
        ),

        # ── Network: did the speaker actually call our server? ─────────────
        # Check if our FastAPI (BOSE_SERVER_URL) is reachable from the speaker
        "reach_fastapi": (
            f"curl -s -m 5 -o /dev/null -w '%{{http_code}}' "
            f"'{_SERVER_URL}/bmx/registry/v1/services'"
        ),
        "reach_fastapi_body": (
            f"curl -s -m 5 '{_SERVER_URL}/bmx/registry/v1/services' | head -c 300"
        ),

        # ── Test select variants ───────────────────────────────────────────
        # Try with and without sourceAccount; compare the error codes.
        "select_with_account": (
            f"curl -s -m 5 -X POST http://localhost:{_PORT}/select "
            f"-H 'Content-Type: application/xml' "
            f"-d '<ContentItem source=\"LOCAL_INTERNET_RADIO\" "
            f'type="stationurl" location="http://localhost:8080/ts_current.json" '
            f'sourceAccount="{_BMX_ANON_USER_ID}">'
            f"<itemName>Test</itemName></ContentItem>'"
        ),
        "select_no_account": (
            f"curl -s -m 5 -X POST http://localhost:{_PORT}/select "
            f"-H 'Content-Type: application/xml' "
            f"-d '<ContentItem source=\"LOCAL_INTERNET_RADIO\" "
            f'type="stationurl" location="http://localhost:8080/ts_current.json">'
            f"<itemName>Test</itemName></ContentItem>'"
        ),
        "select_empty_account": (
            f"curl -s -m 5 -X POST http://localhost:{_PORT}/select "
            f"-H 'Content-Type: application/xml' "
            f"-d '<ContentItem source=\"LOCAL_INTERNET_RADIO\" "
            f'type="stationurl" location="http://localhost:8080/ts_current.json" '
            f'sourceAccount="">'
            f"<itemName>Test</itemName></ContentItem>'"
        ),
    }

    results = {}
    for key, cmd in checks.items():
        try:
            results[key] = await _shell.exec(cmd, timeout=12.0)
        except BoseError as exc:
            results[key] = f"ERROR: {exc}"
    return results


async def diagnose_filesystem() -> dict:
    """
    Full filesystem discovery diagnostic for Sources.xml path hunting.

    Call GET /api/diagnose/filesystem when the assumed Sources.xml path is wrong.
    The output contains every XML file under /mnt and /opt, the full /mnt/nv
    directory tree, partition mount table, and the UpnpSource config file.

    Paste the complete JSON response when reporting a path problem.
    """
    checks = {
        # ── Partition mount table ─────────────────────────────────────────
        "mounts": "cat /proc/mounts 2>/dev/null | grep -v 'proc\\|sys\\|dev'",
        "df": "df -h 2>/dev/null",

        # ── Full /mnt/nv tree (the NV persistent volume) ─────────────────
        # -maxdepth 6 avoids runaway output on deeply nested filesystems.
        "mnt_nv_tree": (
            "find /mnt/nv -maxdepth 6 2>/dev/null | sort | head -100"
        ),

        # ── All XML files on the writable partition ───────────────────────
        "all_xml_mnt": (
            "find /mnt -name '*.xml' 2>/dev/null | sort | head -50"
        ),
        "all_xml_opt": (
            "find /opt -name '*.xml' 2>/dev/null | sort | head -30"
        ),

        # ── Direct Sources.xml probe (all candidates) ─────────────────────
        "sources_candidates": "\n".join(
            f"echo '--- {p}' ; test -f {p} && head -8 {p} || echo MISSING"
            for p in _SOURCES_XML_CANDIDATES
        ),

        # ── Dynamic discovery result ──────────────────────────────────────
        # Runs _find_sources_xml logic directly in shell (fast path only)
        "sources_find_fast": (
            "find /mnt /opt -maxdepth 8 "
            r"\( -name 'Sources.xml' -o -name 'sources.xml' \) "
            "2>/dev/null"
        ),

        # ── UpnpSource config — reveals actual UPnP control URL ───────────
        # This is the fallback playback path if Sources.xml is unmodifiable.
        "upnp_source_cfg": (
            "cat /opt/Bose/etc/UpnpSource-spotty.xml 2>/dev/null "
            "|| find /opt/Bose -name 'UpnpSource*' 2>/dev/null | head -5"
        ),

        # ── Writable partition check ──────────────────────────────────────
        "mnt_nv_writable": (
            "touch /mnt/nv/.ts_write_test 2>/dev/null "
            "&& echo WRITABLE && rm /mnt/nv/.ts_write_test "
            "|| echo READ_ONLY"
        ),

        # ── All running processes (find audio daemon name) ─────────────────
        "all_processes": "ps 2>/dev/null | head -60",
    }

    results = {}
    for key, cmd in checks.items():
        try:
            results[key] = await _shell.exec(cmd, timeout=20.0)
        except BoseError as exc:
            results[key] = f"ERROR: {exc}"

    # Run the full dynamic discovery and add its result
    try:
        found = await _find_sources_xml()
        results["_find_sources_xml_result"] = found or "NOT_FOUND"
    except BoseError as exc:
        results["_find_sources_xml_result"] = f"ERROR: {exc}"

    return results


async def diagnose_upnp() -> dict:
    """
    Pinpoint the AVTransport SOAP controlURL and the port it listens on.

    UpnpSource-spotty.xml on this firmware is just a feature flag
    (<isSsdpEnabled>true</isSsdpEnabled>) with no descriptionURL — the device
    descriptor must be fetched directly from the running UPnP renderer.

    Call GET /api/diagnose/upnp and inspect:
      - "listening_ports" — confirm which port(s) the renderer is actually on
        (it may not be 8091; some Bose builds run UPnP on a high ephemeral port).
      - "root_8091" / "common_paths_*" — raw descriptor bodies / HTTP codes.
      - "avtransport_files" + "avtransport_file_dump" — any on-disk XML that
        mentions AVTransport, dumped in full (these often ARE the SCPD/device
        descriptors with the real <controlURL>).
      - "controlurl_grep" — direct grep for <controlURL>/<SCPDURL> across
        /opt/Bose and /mnt.

    Once you have the real controlURL, set BOSE_UPNP_CONTROL_URL in
    docker-compose.yml (absolute, e.g. "http://localhost:8091/AVTransport/ctrl",
    or path-only, e.g. "/upnp/control/AVTransport1") to skip discovery and use
    it directly — no code change needed.
    """
    common_paths = (
        "/", "/description.xml", "/rootDesc.xml", "/upnp/desc.xml",
        "/MediaRenderer.xml", "/upnp/control/AVTransport1",
        "/AVTransport/ctrl", "/AVTransport/Control",
        "/MediaRenderer/AVTransport/Control", "/ctl/AVTransport",
        "/cd/AVTransport", "/AVTransport1/control",
    )

    checks = {
        # ── Which port(s) is the UPnP renderer actually listening on? ─────
        "listening_ports": (
            "netstat -tlnp 2>/dev/null || netstat -tln 2>/dev/null "
            "|| netstat -an 2>/dev/null || cat /proc/net/tcp"
        ),
        "upnp_processes": (
            "ps | grep -iE 'upnp|gupnp|dlna|renderer' | grep -v grep"
        ),

        # ── On-disk XML mentioning AVTransport — often the real descriptor ─
        "avtransport_files": (
            "find /opt/Bose /mnt -iname '*.xml' 2>/dev/null "
            "| xargs grep -liE 'avtransport' 2>/dev/null"
        ),
        "avtransport_file_dump": (
            "for f in $(find /opt/Bose /mnt -iname '*.xml' 2>/dev/null "
            "| xargs grep -liE 'avtransport' 2>/dev/null); do "
            "echo \"=== $f ===\"; cat \"$f\"; done | head -c 8000"
        ),
        "controlurl_grep": (
            "find /opt/Bose /mnt -iname '*.xml' 2>/dev/null "
            "| xargs grep -inE 'controlURL|SCPDURL|eventSubURL|serviceType' "
            "2>/dev/null | head -60"
        ),

        # ── Raw responses from the renderer on the assumed port 8091 ──────
        "root_8091": "curl -s -m 3 http://localhost:8091/ | head -c 2000",
        "root_8091_headers": (
            "curl -s -m 3 -D - -o /dev/null http://localhost:8091/ 2>/dev/null"
        ),
        "common_paths_8091": "\n".join(
            f"echo '--- {p}'; "
            f"curl -s -m 2 -o /dev/null -w '%{{http_code}}\\n' http://localhost:8091{p}"
            for p in common_paths
        ),
    }

    results = {}
    for key, cmd in checks.items():
        try:
            results[key] = await _shell.exec(cmd, timeout=20.0)
        except BoseError as exc:
            results[key] = f"ERROR: {exc}"

    return results


async def diagnose_upnp_control() -> dict:
    """
    Unmask why curl against the AVTransport controlURL (currently
    BOSE_UPNP_CONTROL_URL, default http://localhost:40010/AVTransport/Control)
    returns an empty string with `curl -s`.

    Re-runs the same SetAVTransportURI POST WITHOUT -s, with -v (verbose) and
    an explicit exit-code echo, so connection refusals, timeouts, HTTP error
    statuses, and SOAP <s:Fault> bodies are all visible — `curl -s` swallows
    all of these as silent empty output.

    Also checks:
      - whether port 40010 (0x9C4A) is actually in LISTEN state right now
        (the renderer may only bind it while in a specific mode)
      - the UpnpSource process state (/proc/<pid>/status, cmdline)
      - whether the UPNP "source" itself is marked available — if the
        firmware gates AVTransport on source-availability the same way it
        gates /select, SetAVTransportURI may be accepted at the socket level
        but ignored by the audio pipeline.

    Call GET /api/diagnose/upnp_control immediately after a play_url() attempt
    so the tmp_upnp_seturi.xml / tmp_upnp_play.xml files from that attempt are
    still on disk and reused here.
    """
    control_url = _upnp_control_url or _UPNP_CONTROL_URL_OVERRIDE or (
        f"http://localhost:{_UPNP_PORT}/AVTransport/Control"
    )
    tmp_seturi = f"{_HTML_DIR}/tmp_upnp_seturi.xml"

    # Minimal dummy SOAP body — used only if the real play_url() attempt
    # hasn't run yet and tmp_seturi.xml doesn't exist.
    dummy_soap = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body><u:SetAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
        '<InstanceID>0</InstanceID>'
        '<CurrentURI>http://example.com/test.mp3</CurrentURI>'
        '<CurrentURIMetaData></CurrentURIMetaData>'
        '</u:SetAVTransportURI></s:Body></s:Envelope>'
    )
    tmp_dummy = f"{_HTML_DIR}/tmp_upnp_dummy.xml"
    await _write_file(tmp_dummy, dummy_soap)

    checks = {
        # ── Is 40010 actually listening right now? ─────────────────────────
        "port_40010_listen": (
            "netstat -tlnp 2>/dev/null | grep -i 40010 "
            "|| netstat -an 2>/dev/null | grep -i 40010 "
            "|| grep -i ':9C4A' /proc/net/tcp"
        ),

        # ── UpnpSource process health ───────────────────────────────────
        "upnpsource_proc": (
            "for p in $(pgrep -f UpnpSource 2>/dev/null || "
            "ps | grep UpnpSource | grep -v grep | awk '{print $1}'); do "
            "echo \"=== pid $p ===\"; "
            "cat /proc/$p/status 2>/dev/null | head -5; "
            "cat /proc/$p/cmdline 2>/dev/null | tr '\\0' ' '; echo; done"
        ),

        # ── Full dump of UpnpSource's config file — look for binding rules,
        #    security/auth flags, or a "source must be active" gate ─────────
        "upnpsource_config_dump": (
            f"echo '--- {_UPNP_CFG_PATH} ---'; "
            f"cat {_UPNP_CFG_PATH} 2>/dev/null || echo MISSING; "
            "echo '--- related configs ---'; "
            "find /opt/Bose/etc /opt/Bose/Xml -iname '*upnp*' -o -iname '*avtransport*' "
            "-o -iname '*mediarenderer*' 2>/dev/null | while read f; do "
            "echo \"=== $f ===\"; cat \"$f\" 2>/dev/null; echo; done"
        ),

        # ── UpnpSource process environment — env vars sometimes gate
        #    feature activation (e.g. UPNP_ENABLED=0) ────────────────────────
        "upnpsource_environ": (
            "for p in $(pgrep -f UpnpSource 2>/dev/null || "
            "ps | grep UpnpSource | grep -v grep | awk '{print $1}'); do "
            "echo \"=== pid $p environ ===\"; "
            "cat /proc/$p/environ 2>/dev/null | tr '\\0' '\\n'; done"
        ),

        # ── Baseline connectivity — no SOAP, just GET / ────────────────────
        "curl_verbose_root": (
            f"curl -v -m 5 http://localhost:40010/ 2>&1 | head -c 3000; "
            "echo; echo EXIT=$?"
        ),

        # ── The real test: verbose POST with the tmp file from the last
        #    play_url() attempt (falls back to the dummy SOAP body) ────────
        "curl_verbose_seturi": (
            f"F={tmp_seturi}; test -f \"$F\" || F={tmp_dummy}; "
            f"curl -v -m 8 -X POST '{control_url}' "
            f"-H 'Content-Type: text/xml; charset=\"utf-8\"' "
            f"-H 'SOAPAction: \"urn:schemas-upnp-org:service:AVTransport:1#SetAVTransportURI\"' "
            f"-d \"@$F\" 2>&1 | head -c 4000; echo; echo EXIT=$?"
        ),

        # ── Same POST with `Expect:` disabled — the classic cause of
        #    "Empty reply from server" (52) on bodies > 1024 bytes against
        #    tiny embedded HTTP servers that don't implement 100-continue ───
        "curl_verbose_seturi_noexpect": (
            f"F={tmp_seturi}; test -f \"$F\" || F={tmp_dummy}; "
            f"echo \"body size: $(wc -c < $F) bytes\"; "
            f"curl -v -m 8 -X POST '{control_url}' "
            f"-H 'Content-Type: text/xml; charset=\"utf-8\"' "
            f"-H 'SOAPAction: \"urn:schemas-upnp-org:service:AVTransport:1#SetAVTransportURI\"' "
            f"-H 'Expect:' "
            f"-d \"@$F\" 2>&1 | head -c 4000; echo; echo EXIT=$?"
        ),

        # ── Same POST, --http1.0 (some libupnp miniservers mishandle
        #    HTTP/1.1 keep-alive and only respond cleanly on 1.0) ──────────
        "curl_verbose_seturi_http10": (
            f"F={tmp_seturi}; test -f \"$F\" || F={tmp_dummy}; "
            f"curl -v -m 8 --http1.0 -X POST '{control_url}' "
            f"-H 'Content-Type: text/xml; charset=\"utf-8\"' "
            f"-H 'SOAPAction: \"urn:schemas-upnp-org:service:AVTransport:1#SetAVTransportURI\"' "
            f"-H 'Expect:' "
            f"-d \"@$F\" 2>&1 | head -c 4000; echo; echo EXIT=$?"
        ),

        # ── Same POST with a SoundTouch-flavoured User-Agent, in case the
        #    renderer whitelists requests by identity ──────────────────────
        "curl_verbose_seturi_useragent": (
            f"F={tmp_seturi}; test -f \"$F\" || F={tmp_dummy}; "
            f"curl -v -m 8 -X POST '{control_url}' "
            f"-A 'SoundTouch/29 (SoundTouch 20)' "
            f"-H 'Content-Type: text/xml; charset=\"utf-8\"' "
            f"-H 'SOAPAction: \"urn:schemas-upnp-org:service:AVTransport:1#SetAVTransportURI\"' "
            f"-H 'Expect:' "
            f"-d \"@$F\" 2>&1 | head -c 4000; echo; echo EXIT=$?"
        ),

        # ── Confirm the tmp file from play_url() actually landed on disk ───
        "tmp_seturi_check": (
            f"ls -la {tmp_seturi} 2>/dev/null; wc -c < {tmp_seturi} 2>/dev/null "
            f"|| echo MISSING"
        ),

        # ── Is the UPNP source itself reported available? ──────────────────
        "sources_api": f"curl -s -m 5 http://localhost:{_PORT}/sources",

        # ── Any UPnP/renderer logging on disk? ──────────────────────────────
        "upnp_logs": (
            "grep -ril 'upnp\\|avtransport\\|setavtransport' /var/log 2>/dev/null "
            "| head -5; "
            "grep -i 'upnp\\|avtransport' /var/log/messages 2>/dev/null | tail -20"
        ),
    }

    results = {"control_url_used": control_url}
    for key, cmd in checks.items():
        try:
            results[key] = await _shell.exec(cmd, timeout=20.0)
        except BoseError as exc:
            results[key] = f"ERROR: {exc}"

    return results


async def diagnose_port17000() -> dict:
    """
    Explore the port-17000 diagnostic CLI for source-state / UPnP-activation
    commands.

    This is a different command interpreter from the BusyBox ash shell on
    port 23 — it's the `->`-prompt CLI used to send `remote_services on`.
    If the firmware exposes a way to force the UPNP source from UNAVAILABLE
    to READY (or to kick UpnpSource's SOAP server into an initialized state),
    it would be a command here, not a Linux binary.

    Sends a battery of likely command names and returns whatever each one
    echoes back. Most will return "Invalid command" or similar — look for
    any that return a source list, a usage string, or behave differently.

    Call GET /api/diagnose/port17000 and read every value; this is a fishing
    expedition, not a known-good command list.
    """
    probe_commands = [
        "help",
        "?",
        "sources",
        "source",
        "source list",
        "source status",
        "sourceutil",
        "stst",
        "avtransport",
        "renderer",
        "renderer status",
        "status",
        "info",
        # `upnp` and `upnp enable` returned "Invalid Command Option" — the
        # command exists but wants different arguments. Probe its syntax.
        "upnp help",
        "upnp -h",
        "upnp ?",
        "upnp",
        "upnp status",
        "upnp show",
        "upnp list",
        "upnp info",
        "upnp start",
        "upnp on",
        "upnp 1",
        "upnp enable 1",
        "upnp -e",
        "upnp -e 1",
        "upnp -s",
        "upnp init",
        "upnp restart",
        # The cliservercommand.uds IPC bus (APServer/STSCertified/BoseApp) is
        # hypothesized to share its command vocabulary with this port-17000
        # CLI. If so, "key PLAY" etc. work directly over TCP here — no
        # socat/perl/static-binary connector needed at all.
        "key PLAY",
        "key PAUSE",
        "key PRESET_1",
        "cli_help",
        "key",
        "key help",
        "play",
        "play_url",
        # BREAKTHROUGH: "key" is a real command — "key" alone returns
        # "Unknown key, need at least one argument" and every "key <X>"
        # returns "OK" after the error, meaning the command always executes.
        # "PLAY"/"PAUSE"/"PRESET_1" (the REST /key API names) aren't valid
        # key NAMES for this lower-level CLI. Probe Linux input-event style
        # names/codes and lowercase/numeric variants to find the real syntax.
        "key ?",
        "key -h",
        "key list",
        "key 0",
        "key 1",
        "key 2",
        "key 3",
        "key 10",
        "key 11",
        "key 100",
        "key 164",  # KEY_PLAYPAUSE in linux/input-event-codes.h
        "key 207",  # KEY_PLAY
        "key 119",  # KEY_PAUSE
        "key play",
        "key pause",
        "key playpause",
        "key KEY_PLAY",
        "key KEY_PLAYPAUSE",
        "key KEY_PAUSE",
        "key power",
        "key POWER",
        "key KEY_POWER",
        "key mute",
        "key MUTE",
        "key volup",
        "key VOLUME_UP",
        "key preset1",
        "key PRESET1",
        "key preset_1",
    ]
    try:
        return await _query_port17000(host(), probe_commands)
    except BoseError as exc:
        return {"ERROR": str(exc)}


async def diagnose_port17000_keys() -> dict:
    """
    Clean (isolated-connection) re-test of the port-17000 `key` command.

    The shared-connection diagnose_port17000() probe showed response bleed
    between consecutive "key <X>" calls (an async "Key sent\\n" for command N
    sometimes only appeared in the result for command N+1). This re-tests
    the most promising candidates — "play"/"pause"/"VOLUME_UP" returned
    "Key sent" — each on its own connection, plus a broader set of
    lowercase/Linux-input-style key names (stop, next, prev, voldown,
    preset1-6, source toggles) to map out the full vocabulary.

    Also probes non-"key" commands ("select", "source", "play_url", "url",
    "stream", "tunein" + lowercase variants with an AUX/SPOTIFY argument) —
    this is the syntax we actually need for stream injection, once the
    `key`-command vocabulary confirms the naming convention.
    """
    key_candidates = [
        "play", "pause", "playpause", "stop",
        "next", "prev", "previous",
        "volup", "voldown", "VOLUME_UP", "VOLUME_DOWN",
        "mute", "MUTE", "power", "POWER",
        "preset1", "preset2", "preset3", "preset4", "preset5", "preset6",
        "PRESET1", "PRESET2",
        "KEY_PLAYPAUSE", "KEY_POWER", "KEY_MUTE",
        "aux", "AUX", "bluetooth", "BLUETOOTH", "spotify", "SPOTIFY",
    ]
    other_candidates = [
        "select aux",
        "select AUX",
        "select spotify",
        "source aux",
        "source AUX",
        "source select aux",
        "play_url http://example.com/stream.mp3",
        "playurl http://example.com/stream.mp3",
        "url http://example.com/stream.mp3",
        "stream http://example.com/stream.mp3",
        "tunein s108635",
        "select tunein",
        "select internet_radio",
        "select LOCAL_INTERNET_RADIO",
    ]
    try:
        keys = await _query_port17000_isolated(
            host(), [f"key {c}" for c in key_candidates]
        )
        others = await _query_port17000_isolated(host(), other_candidates)
    except BoseError as exc:
        return {"ERROR": str(exc)}
    return {"keys": keys, "other_commands": others}


async def diagnose_internal_api() -> dict:
    """
    Strategy pivot away from UpnpSource (40010 = dead end, "Empty reply" on
    every request, source UNAVAILABLE and not actuatable via port 17000).

    Instead, map the internal-only API on port 8091 (owned by STSCertified,
    PID 1794) and the IPC surface of STSCertified / APServer (PID 1806).
    8091 returned 404 (not a connection drop) on earlier probes — a 404 means
    a real HTTP server parsed the request and routed it, which is a much
    better sign than 40010's instant-close. The hypothesis: 8091 is a
    loopback-only mirror of the public SoundTouch API (port 8090) that may
    accept /select or /key calls for sources the public API blocks (e.g.
    SPOTIFY re-targeting, or even LOCAL_INTERNET_RADIO without the 1005
    gate).

    Checks:
      - "api_paths_8091" — every known SoundTouch REST endpoint
        (/info, /now_playing, /select, /key, /sources, /volume, ...) probed
        on 8091 with both GET and HEAD, http_code only.
      - "select_spotify_8091" — POST a real /select XML body (source=SPOTIFY,
        the speaker's own active source) to 8091/select; compare the response
        to the same call against 8090/select.
      - "stscertified_fds" / "apserver_fds" — open file descriptors for PID
        1794 / 1806: look for AF_UNIX sockets, FIFOs, shared memory, or
        device nodes used for IPC.
      - "unix_sockets" — every AF_UNIX socket on the box (netstat -x, plus a
        find for *.sock / *.socket under /var/run, /tmp, /opt/Bose).
      - "sysv_ipc" — System V shared memory / message queue / semaphore
        segments (ipcs), often used by Bose's notification bus.

    Call GET /api/diagnose/internal_api and read every field — this is
    exploratory mapping, not a known-good path list.
    """
    api_paths = (
        "/info", "/now_playing", "/now_playing_active", "/volume",
        "/bass", "/bassCapabilities", "/presets", "/sources", "/select",
        "/key", "/name", "/networkInfo", "/getActiveWifiProfile",
        "/getZone", "/setZone", "/trackInfo", "/clockDisplay",
        "/introspect", "/swInfo", "/capabilities", "/listMediaServers",
    )

    select_spotify_xml = (
        '<ContentItem source="SPOTIFY" type="uri" location="" '
        'sourceAccount="" isPresetable="true"><itemName>Spotify</itemName>'
        '</ContentItem>'
    )
    tmp_select = f"{_HTML_DIR}/tmp_select_spotify.xml"
    await _write_file(tmp_select, select_spotify_xml)

    checks = {
        # ── Enumerate the 8091 API surface — 404 means it's a live router ──
        "api_paths_8091": "\n".join(
            f"echo '--- {p}'; "
            f"curl -s -m 2 -o /dev/null -w '%{{http_code}}\\n' http://localhost:8091{p}"
            for p in api_paths
        ),

        # ── Same paths against the KNOWN-WORKING 8090, for comparison ──────
        "api_paths_8090": "\n".join(
            f"echo '--- {p}'; "
            f"curl -s -m 2 -o /dev/null -w '%{{http_code}}\\n' http://localhost:8090{p}"
            for p in api_paths
        ),

        # ── Body of whatever 8091 returns for the paths that aren't 404 ────
        "api_bodies_8091": "\n".join(
            f"echo '--- {p}'; curl -s -m 3 http://localhost:8091{p} | head -c 1000; echo"
            for p in ("/info", "/now_playing", "/sources", "/select")
        ),

        # ── POST /select for SPOTIFY against 8091 vs 8090 ──────────────────
        "select_spotify_8091": (
            f"curl -v -m 5 -X POST http://localhost:8091/select "
            f"-d @{tmp_select} 2>&1 | head -c 2000; echo; echo EXIT=$?"
        ),
        "select_spotify_8090": (
            f"curl -v -m 5 -X POST http://localhost:8090/select "
            f"-d @{tmp_select} 2>&1 | head -c 2000; echo; echo EXIT=$?"
        ),

        # ── Open file descriptors for STSCertified (1794) — look for
        #    AF_UNIX sockets, FIFOs, shm, device nodes ───────────────────────
        "stscertified_fds": (
            "P=$(pgrep -f STSCertified 2>/dev/null | head -1); "
            "echo \"pid=$P\"; ls -la /proc/$P/fd 2>/dev/null"
        ),

        # ── Same for APServer (1806) ────────────────────────────────────────
        "apserver_fds": (
            "P=$(pgrep -f APServer 2>/dev/null | head -1); "
            "echo \"pid=$P\"; ls -la /proc/$P/fd 2>/dev/null"
        ),

        # ── Every AF_UNIX socket on the device ──────────────────────────────
        "unix_sockets": (
            "netstat -x 2>/dev/null || netstat -a 2>/dev/null | grep -i unix; "
            "find /var/run /tmp /opt/Bose -type s 2>/dev/null"
        ),

        # ── System V shared memory / message queues / semaphores ──────────
        "sysv_ipc": "ipcs 2>/dev/null || echo 'ipcs not available'",
    }

    results = {}
    for key, cmd in checks.items():
        try:
            results[key] = await _shell.exec(cmd, timeout=25.0)
        except BoseError as exc:
            results[key] = f"ERROR: {exc}"

    return results


# ── /tmp/cliservercommand.uds — central IPC command bus ───────────────────────
#
# Confirmed via diagnose_internal_api(): APServer, BoseApp, and STSCertified
# all hold connected fds to /tmp/cliservercommand.uds. This is almost
# certainly the same command bus that backs the port-17000 `->` CLI and the
# physical front-panel buttons. Rather than risk breaking an already-connected
# stream socket with a MITM proxy, we passively snoop it with `strace`
# (ptrace-based syscall tracing) attached to the relevant PIDs — non-invasive,
# doesn't touch the socket itself, and root access already gives us
# CAP_SYS_PTRACE.

_IPC_SNOOP_LOG = "/tmp/ts_ipc_snoop.log"


async def diagnose_ipc_bus() -> dict:
    """
    Map /tmp/cliservercommand.uds before attempting to snoop it.

    Checks:
      - "strace_available" / "ltrace_available" — is a ptrace-based tracer
        present? strace is the preferred snoop mechanism (non-invasive,
        doesn't disturb the existing connected sockets).
      - "uds_listing" — `ls -la /tmp/*.uds` (cliservercommand, btmgr,
        spotifyaudio, syscheck — all candidate command buses).
      - "uds_proc_net" — raw `/proc/net/unix` lines for cliservercommand.uds,
        including inode numbers.
      - "uds_pid_owners" — for each inode found above, which PIDs hold an fd
        to it (correlates sockets to APServer / STSCertified / BoseApp).
      - "candidate_pids" — pgrep for APServer, STSCertified, BoseApp (the
        three processes strace would attach to).

    Call GET /api/diagnose/ipc_bus, confirm strace is available and identify
    the PIDs, then use POST /api/diagnose/ipc_snoop/start before pressing
    PLAY / a preset button on the physical speaker.
    """
    checks = {
        "strace_available": "which strace 2>/dev/null; strace -V 2>&1 | head -1 || echo NOT_FOUND",
        "ltrace_available": "which ltrace 2>/dev/null || echo NOT_FOUND",

        "uds_listing": "ls -la /tmp/*.uds 2>/dev/null",

        "uds_proc_net": (
            "grep -i cliservercommand /proc/net/unix"
        ),

        "uds_pid_owners": (
            "for ino in $(grep cliservercommand.uds /proc/net/unix "
            "| awk '{print $7}' | sort -u); do "
            "echo \"=== inode $ino ===\"; "
            "for p in /proc/[0-9]*; do "
            "pid=$(basename $p); "
            "if ls -la $p/fd 2>/dev/null | grep -q \"socket:\\[$ino\\]\"; then "
            "echo \"pid=$pid  $(cat $p/cmdline 2>/dev/null | tr '\\0' ' ')\"; "
            "fi; done; done"
        ),

        "candidate_pids": (
            "echo \"APServer:     $(pgrep -f APServer 2>/dev/null)\"; "
            "echo \"STSCertified: $(pgrep -f STSCertified 2>/dev/null)\"; "
            "echo \"BoseApp:      $(pgrep -f BoseApp 2>/dev/null)\""
        ),
    }

    results = {}
    for key, cmd in checks.items():
        try:
            results[key] = await _shell.exec(cmd, timeout=20.0)
        except BoseError as exc:
            results[key] = f"ERROR: {exc}"

    return results


async def start_ipc_snoop() -> dict:
    """
    Attach `strace` to APServer, STSCertified, and BoseApp and log every
    read()/write()/send()/recv() syscall (with buffer contents, up to 400
    bytes) to /tmp/ts_ipc_snoop.log on the speaker.

    Runs strace in the background (shell `&`) so this call returns
    immediately. After starting, manually press PLAY or a preset button on
    the physical speaker, then call GET /api/diagnose/ipc_snoop/log to read
    the captured traffic, and POST /api/diagnose/ipc_snoop/stop when done.

    Non-invasive: does not touch /tmp/cliservercommand.uds itself, so existing
    connections are unaffected. strace does pause each traced process briefly
    on every syscall — on a slow embedded CPU this can add latency, so don't
    leave it running longer than needed.
    """
    pids_raw = await _shell.exec(
        "echo \"$(pgrep -f APServer 2>/dev/null) "
        "$(pgrep -f STSCertified 2>/dev/null) "
        "$(pgrep -f BoseApp 2>/dev/null)\"",
        timeout=10.0,
    )
    pids = [p for p in pids_raw.split() if p.isdigit()]
    if not pids:
        return {"ERROR": "Could not find PIDs for APServer/STSCertified/BoseApp"}

    p_flags = " ".join(f"-p {pid}" for pid in pids)
    cmd = (
        f"rm -f {_IPC_SNOOP_LOG}; "
        f"strace -tt -s 400 -e trace=read,write,recvfrom,sendto,connect,accept "
        f"{p_flags} -o {_IPC_SNOOP_LOG} >/dev/null 2>&1 & "
        f"echo STARTED pid=$!"
    )
    try:
        out = await _shell.exec(cmd, timeout=10.0)
    except BoseError as exc:
        return {"ERROR": str(exc)}

    return {"result": out, "traced_pids": pids, "log_file": _IPC_SNOOP_LOG}


async def stop_ipc_snoop() -> dict:
    """Kill the background strace started by start_ipc_snoop()."""
    try:
        out = await _shell.exec(
            "pkill -f 'strace.*ts_ipc_snoop' && echo STOPPED || echo NOT_RUNNING",
            timeout=10.0,
        )
    except BoseError as exc:
        return {"ERROR": str(exc)}
    return {"result": out}


async def get_ipc_snoop_log(lines: int = 300) -> dict:
    """Return the last N lines of the strace capture."""
    try:
        out = await _shell.exec(
            f"tail -n {int(lines)} {_IPC_SNOOP_LOG} 2>/dev/null || echo EMPTY",
            timeout=15.0,
        )
    except BoseError as exc:
        return {"ERROR": str(exc)}
    return {"log": out}


# ── IPC injection probe ────────────────────────────────────────────────────
#
# IMPORTANT: /tmp/cliservercommand.uds is a Unix domain socket inside the
# SPEAKER's filesystem/network namespace, not ours. Our FastAPI container has
# no direct path to it — asyncio.open_unix_connection() in this process would
# just raise FileNotFoundError. Every interaction with the speaker's
# filesystem and sockets goes through the root Telnet shell (_shell), the
# same as every other diagnose_*() in this module.
#
# So the "probe" runs on the speaker itself: we write the payload bytes to a
# tmp file via _write_file_bytes(), then pipe that file into the UDS using
# whatever connector binary the speaker's BusyBox provides (socat, or
# `nc -U`), capturing whatever comes back within a short timeout.
#
# diagnose_ipc_tools() must be called first to confirm a connector exists.

_IPC_UDS = "/tmp/cliservercommand.uds"

# Three framing hypotheses for the command bus:
#
# 1. "cli_*"    — plain-text line-oriented commands, same vocabulary as the
#                 port-17000 `->` CLI (e.g. "key PLAY"). cliservercommand.uds
#                 sounds exactly like the backend for that CLI, so its own
#                 syntax is the best first guess.
# 2. "json_*"   — JSON envelope, either a bare {"cmd": ...} or a JSON-RPC-ish
#                 {"jsonrpc":"2.0","method":...,"params":{...}} — common in
#                 newer Bose "FrontDoor"/Riviera-derived IPC.
# 3. "framed_*" — 4-byte big-endian length prefix + raw text command, a
#                 common framing for length-delimited Unix socket protocols
#                 when the payload isn't newline-terminated.
_IPC_PRESETS: dict[str, bytes] = {
    "cli_play":       b"key PLAY\r\n",
    "cli_preset1":    b"key PRESET_1\r\n",
    "cli_sources":    b"sources\r\n",
    "cli_help":       b"help\r\n",
    "json_cmd_play":  b'{"cmd":"play"}\n',
    "json_rpc_key":   b'{"jsonrpc":"2.0","method":"key","params":{"key":"PLAY"}}\n',
    "framed_key_play": struct.pack(">I", len(b"key PLAY")) + b"key PLAY",
}


async def diagnose_ipc_tools() -> dict:
    """
    Confirm a UDS connector exists on the speaker before calling ipc_probe().

    socat is preferred (UNIX-CONNECT:). Some BusyBox builds' `nc` also
    support `-U <path>` for AF_UNIX. If neither is present, ipc_probe()
    cannot run — call GET /api/diagnose/ipc_tools first.
    """
    checks = {
        "socat":          "which socat 2>/dev/null || echo NOT_FOUND",
        "nc_path":        "which nc 2>/dev/null || echo NOT_FOUND",
        "nc_help_full":   "nc --help 2>&1; echo '---'; nc -h 2>&1",
        "python":         "which python python3 python2 lua tclsh perl 2>/dev/null || echo NOT_FOUND",
        "uname":          "uname -a 2>/dev/null || echo NOT_FOUND",
        "bin_scan": (
            "ls /usr/bin /bin /sbin /usr/sbin /opt/Bose/bin 2>/dev/null "
            "| sort -u | grep -iE 'nc|socat|python|lua|perl|tcl|expect|curl|wget|busybox'"
        ),
        "busybox_applets": "busybox 2>&1 | grep -iE 'nc|socat|python|lua|perl|tcl'",
        "libperl_search": (
            "find /opt/Bose/lib /opt/Bose/lib64 /usr/lib /usr/local/lib /lib "
            "-name 'libperl.so*' 2>/dev/null"
        ),
        "libperl_search_full": (
            "find / -xdev -iname 'libperl*' 2>/dev/null; "
            "find /mnt /opt /var -iname 'libperl*' 2>/dev/null"
        ),
        "perl_variants": (
            "ls -la /usr/bin/perl* 2>/dev/null; "
            "echo '--- perl5.14.3 -e ---'; "
            "/usr/bin/perl5.14.3 -e 'print \"OK\\n\"' 2>&1; "
            "echo '--- perl -V short ---'; "
            "/usr/bin/perl -e 1 2>&1"
        ),
        "available_presets": "echo " + " ".join(_IPC_PRESETS.keys()),
    }
    results = {}
    for key, cmd in checks.items():
        try:
            results[key] = await _shell.exec(cmd, timeout=10.0)
        except BoseError as exc:
            results[key] = f"ERROR: {exc}"
    return results


# ── Python injector fallback ────────────────────────────────────────────────
#
# Neither socat nor `nc -U` is present on this firmware (confirmed via
# diagnose_ipc_tools()). The speaker DOES have a Python interpreter on its
# PATH, so we write a tiny AF_UNIX client script to /tmp/inject.py and run it
# with the payload file as argv[1]. Reading the payload from a file (rather
# than argv) avoids any shell-quoting/length limits on binary data.
_IPC_INJECTOR_PATH = "/tmp/inject.py"

_IPC_INJECTOR_PY = (
    "import socket, sys, base64, binascii\n"
    "with open(sys.argv[1], 'rb') as f:\n"
    "    p = f.read()\n"
    "s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
    "s.settimeout(3)\n"
    "s.connect('/tmp/cliservercommand.uds')\n"
    "s.sendall(p)\n"
    "try:\n"
    "    d = s.recv(4096)\n"
    "except socket.timeout:\n"
    "    d = b''\n"
    "print('SENT_HEX=' + binascii.hexlify(p).decode())\n"
    "print('RECV_HEX=' + binascii.hexlify(d).decode())\n"
    "print('RECV_TXT=' + repr(d))\n"
    "s.close()\n"
)

# ── Perl injector fallback ──────────────────────────────────────────────────
#
# Confirmed present on this firmware (BusyBox v1.19.4, armv7l, perl 5.14.3) —
# Python is NOT_FOUND, but IO::Socket::UNIX and IO::Select are core Perl
# modules. Same contract as the Python injector: payload read from argv[1]
# (binary-safe), 2s read timeout via IO::Select, hex + repr-ish output.
_IPC_INJECTOR_PL_PATH = "/tmp/inject.pl"

_IPC_INJECTOR_PL = (
    "use strict;\n"
    "use warnings;\n"
    "use IO::Socket::UNIX;\n"
    "use IO::Select;\n"
    "\n"
    "open(my $fh, '<', $ARGV[0]) or die \"open: $!\";\n"
    "binmode $fh;\n"
    "my $payload = do { local $/; <$fh> };\n"
    "close $fh;\n"
    "$payload = '' unless defined $payload;\n"
    "\n"
    "my $sock = IO::Socket::UNIX->new(Peer => '/tmp/cliservercommand.uds', Type => SOCK_STREAM)\n"
    "    or die \"connect: $!\";\n"
    "\n"
    "print $sock $payload;\n"
    "\n"
    "my $sel = IO::Select->new($sock);\n"
    "my $resp = '';\n"
    "while ($sel->can_read(2)) {\n"
    "    my $buf;\n"
    "    my $n = sysread($sock, $buf, 4096);\n"
    "    last unless $n;\n"
    "    $resp .= $buf;\n"
    "}\n"
    "\n"
    "print 'SENT_HEX=' . unpack('H*', $payload) . \"\\n\";\n"
    "print 'RECV_HEX=' . unpack('H*', $resp) . \"\\n\";\n"
    "print 'RECV_TXT=' . $resp . \"\\n\";\n"
)


async def ipc_probe(command: str | None = None, preset: str | None = None) -> dict:
    """
    Send one payload to /tmp/cliservercommand.uds and capture the response.

    Either `preset` (one of _IPC_PRESETS) or a raw `command` string (encoded
    as utf-8, "\\n" appended if no line ending present) may be given.

    Connector priority: socat > `nc -U` > Python AF_UNIX injector
    (/tmp/inject.py) > Perl AF_UNIX injector (/tmp/inject.pl), both written
    fresh on every call. Response (if any) is returned both as a hex dump
    and best-effort text/repr.
    """
    if preset is not None:
        if preset not in _IPC_PRESETS:
            return {"ERROR": f"unknown preset {preset!r}, choices: {list(_IPC_PRESETS)}"}
        payload = _IPC_PRESETS[preset]
    elif command is not None:
        payload = command.encode("utf-8")
        if not payload.endswith((b"\n", b"\r\n")):
            payload += b"\n"
    else:
        return {"ERROR": "must supply ?command=... or ?preset=..."}

    tmp_payload = f"{_HTML_DIR}/tmp_ipc_payload.bin"
    tmp_resp    = f"{_HTML_DIR}/tmp_ipc_resp.bin"
    await _write_file_bytes(tmp_payload, payload)
    await _write_file(_IPC_INJECTOR_PATH, _IPC_INJECTOR_PY)
    await _write_file(_IPC_INJECTOR_PL_PATH, _IPC_INJECTOR_PL)

    cmd = (
        f"if command -v socat >/dev/null 2>&1; then "
        f"  timeout -t 5 socat -t2 - UNIX-CONNECT:{_IPC_UDS} < {tmp_payload} > {tmp_resp} 2>&1; "
        f"  echo '--- hex ---'; xxd {tmp_resp} 2>/dev/null | head -40; "
        f"  echo '--- text ---'; cat {tmp_resp} 2>/dev/null | head -c 1000; "
        f"elif (nc -h 2>&1; nc --help 2>&1) | grep -q -- '-U'; then "
        f"  timeout -t 5 nc -U {_IPC_UDS} < {tmp_payload} > {tmp_resp} 2>&1; "
        f"  echo '--- hex ---'; xxd {tmp_resp} 2>/dev/null | head -40; "
        f"  echo '--- text ---'; cat {tmp_resp} 2>/dev/null | head -c 1000; "
        f"else "
        f"  PY=$(command -v python 2>/dev/null || command -v python3 2>/dev/null || command -v python2 2>/dev/null); "
        f"  PL=$(command -v perl 2>/dev/null); "
        f"  if [ -n \"$PY\" ]; then timeout -t 5 $PY {_IPC_INJECTOR_PATH} {tmp_payload} 2>&1; "
        f"  elif [ -n \"$PL\" ]; then "
        f"    LIBPERL=$(find /opt/Bose/lib /opt/Bose/lib64 /usr/lib /usr/local/lib /lib "
        f"-name 'libperl.so*' 2>/dev/null | head -1); "
        f"    LIBDIR=${{LIBPERL%/*}}; "
        f"    LD_LIBRARY_PATH=\"$LIBDIR:$LD_LIBRARY_PATH\" timeout -t 5 $PL {_IPC_INJECTOR_PL_PATH} {tmp_payload} 2>&1; "
        f"  else echo NO_CONNECTOR; fi; "
        f"fi"
    )
    try:
        out = await _shell.exec(cmd, timeout=20.0)
    except BoseError as exc:
        return {"ERROR": str(exc)}

    return {
        "payload_hex":  payload.hex(),
        "payload_repr": repr(payload),
        "response":     out,
    }

