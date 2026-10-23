import asyncio
import functools
import json
import logging
import os
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import app.alarm as alarm
import app.bose as bose
import app.library as library
import app.marge as marge
import app.setup as setup
import app.sleep as sleep
import app.songs as songs
from app.bose import BoseError
from app.version import VERSION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


async def _setup_device(d: bose.Device) -> None:
    """
    One-time setup of one speaker, then push its presets.

    Snapshot devices (the primary SoundTouch 20): order matters, snapshot the
    speaker's own Sources.xml (with its SPOTIFY/ALEXA secrets) BEFORE
    pointing margeServerUrl at us, since afterwards the speaker rebuilds it
    from marge.  ":fresh" devices are redirected through the port-17000
    console instead and get a minimal source list.
    """
    h = d.host
    try:
        with bose.use_device(h):
            if d.fresh:
                if await bose.setup_via_console(h):
                    await bose.reboot_and_wait(h)
            else:
                snapshot_ok = await marge.snapshot_sources(h)
                await bose.setup_speaker(redirect_marge=snapshot_ok)
            await library.sync_presets_for(h)
            log.info("Device %s: setup done", h)
    except Exception as exc:
        log.error("Device %s: setup failed (non-fatal): %s", h, exc)


_listeners: dict[str, asyncio.Task] = {}


def _start_listener(h: str) -> None:
    """Start device `h`'s preset-button listener (idempotent)."""
    if h not in _listeners or _listeners[h].done():
        _listeners[h] = asyncio.create_task(
            bose.run_preset_listener(functools.partial(marge.presets, h), h))


def _stop_listener(h: str) -> None:
    task = _listeners.pop(h, None)
    if task:
        task.cancel()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Setup runs in the background: a speaker rebooted during setup asks this
    # server (marge, BMX) for its account as it boots, so we must already be
    # answering by then.
    await bose.refresh_identities()
    async def setup_all():
        await asyncio.gather(*(_setup_device(d) for d in list(bose.DEVICES)))

    setup_task = asyncio.create_task(setup_all())
    alarm.start()
    for d in bose.DEVICES:
        _start_listener(d.host)
    # Speakers added or removed in the app (app/setup.py)
    setup.on_added, setup.on_removed = _start_listener, _stop_listener
    log.info("Devices: %s", ", ".join(f"{d.host}{' (fresh)' if d.fresh else ''}" for d in bose.DEVICES))
    try:
        yield
    finally:
        tasks = [setup_task, *_listeners.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await songs.stop_all()
        await sleep.stop_all()
        await alarm.stop_all()
        await bose.close_shells()
        log.info("Background tasks stopped")


api = FastAPI(title="TouchingSounds", version=VERSION, lifespan=lifespan)
api.include_router(marge.router)
api.include_router(library.router)
api.include_router(setup.router)


# ── Public access (Cloudflare tunnel) ────────────────────────────────────────
#
# The app is reachable both on the LAN (the speaker, BOSE_SERVER_URL)
# and from the internet through a Cloudflare tunnel.  Requests that came
# through Cloudflare carry a Cf-Connecting-Ip header.  Those may only use the
# web app; the speaker-facing cloud stand-ins (marge, BMX, relay) and the
# diagnostics, which run shell commands on the speaker, stay LAN-only.

_LAN_ONLY_PREFIXES = ("/marge", "/bmx", "/relay", "/api/diagnose", "/api/test", "/docs",
                      "/redoc", "/openapi.json")


# ── Device selection ─────────────────────────────────────────────────────────
#
# The web app names the speaker it means in an X-TS-Device header (its host,
# as listed by /api/devices); everything the request does then talks to that
# device.  Without the header: the primary device.

@api.middleware("http")
async def select_device(request: Request, call_next):
    h = request.headers.get("x-ts-device") or request.query_params.get("device")
    if not h:
        return await call_next(request)
    if not bose.is_known_host(h):
        return JSONResponse({"detail": f"Unknown device {h!r}"}, status_code=404)
    with bose.use_device(h):
        return await call_next(request)


@api.middleware("http")
async def lan_only_guard(request: Request, call_next):
    if "cf-connecting-ip" in request.headers and request.url.path.startswith(_LAN_ONLY_PREFIXES):
        return JSONResponse({"detail": "Not available over the internet"}, status_code=404)
    return await call_next(request)


# ── Request models ────────────────────────────────────────────────────────────

class VolumeBody(BaseModel):
    level: int

class SourceBody(BaseModel):
    source: str
    account: str = ""

class SleepBody(BaseModel):
    minutes: int

class AlarmBody(BaseModel):
    time: str | None = None          # "HH:MM", in tz
    days: list[int] | None = None    # 0 = Monday … 6 = Sunday; empty = once
    station: dict | None = None      # {name, url, favicon}
    volume: int | None = None
    enabled: bool | None = None
    tz: str | None = None            # IANA zone of the phone, e.g. "Europe/Istanbul"

class PlayUrlBody(BaseModel):
    url: str
    name: str = "Custom Stream"


# ── Helper ────────────────────────────────────────────────────────────────────

def _502(exc: Exception) -> HTTPException:
    log.error("502: %s", exc)
    return HTTPException(status_code=502, detail=str(exc))


# ── API routes ────────────────────────────────────────────────────────────────

@api.get("/api/status")
async def status():
    try:
        np_xml  = await bose.now_playing()
        vol_xml = await bose.volume()
    except (BoseError, Exception) as e:
        raise _502(e)

    try:
        np  = ET.fromstring(np_xml)
        vol = ET.fromstring(vol_xml)
    except ET.ParseError as e:
        log.error("XML parse error: %s", e)
        raise HTTPException(500, detail=f"Invalid XML from speaker: {e}")

    source = np.get("source", "")
    song = None
    if source == "LOCAL_INTERNET_RADIO":
        ci = np.find("ContentItem")
        station = bose.decode_orion_location(ci.get("location", "")) if ci is not None else None
        if station and station.get("streamUrl"):
            song = songs.current(station["streamUrl"], np.findtext("stationName", "") or station.get("name", ""))

    return {
        "source":      source,
        "playStatus":  np.findtext("playStatus", ""),
        "track":       np.findtext("track", ""),
        "artist":      np.findtext("artist", ""),
        "album":       np.findtext("album", ""),
        "stationName": np.findtext("stationName", ""),
        "art":         np.findtext("art", ""),
        "volume":      vol.findtext("actualvolume", "0"),
        "muted":       vol.findtext("muteenabled", "false"),
        "song":        song,
        "sleep":       sleep.state(bose.host()),
        "alarm":       alarm.summary(bose.host()),
    }


@api.post("/api/sleep")
async def set_sleep(body: SleepBody):
    """Standby after `minutes` (with a fade-out over the last minute); 0 cancels."""
    if body.minutes <= 0:
        sleep.cancel(bose.host())
        return {"sleep": None}
    return {"sleep": sleep.start(bose.host(), body.minutes)}


# ── Alarms (per device, see app/alarm.py) ─────────────────────────────────────

@api.get("/api/alarms")
async def list_alarms():
    return alarm.for_device(bose.host())


@api.post("/api/alarms")
async def add_alarm(body: AlarmBody):
    try:
        return alarm.add(bose.host(), body.model_dump(exclude_none=True))
    except (ValueError, BoseError) as e:
        raise HTTPException(400, detail=str(e))


@api.put("/api/alarms/{alarm_id}")
async def update_alarm(alarm_id: str, body: AlarmBody):
    try:
        return alarm.update(bose.host(), alarm_id, body.model_dump(exclude_none=True))
    except KeyError:
        raise HTTPException(404, detail="No such alarm")
    except (ValueError, BoseError) as e:
        raise HTTPException(400, detail=str(e))


@api.delete("/api/alarms/{alarm_id}")
async def delete_alarm(alarm_id: str):
    try:
        alarm.remove(bose.host(), alarm_id)
    except KeyError:
        raise HTTPException(404, detail="No such alarm")
    return {"ok": True}


@api.post("/api/alarms/{alarm_id}/test")
async def test_alarm(alarm_id: str):
    """Ring an alarm now, to hear it; its schedule stays as it is."""
    try:
        return alarm.test(bose.host(), alarm_id)
    except KeyError:
        raise HTTPException(404, detail="No such alarm")


@api.post("/api/key/{key_name}")
async def send_key(key_name: str):
    log.info("key -> %s", key_name.upper())
    try:
        await bose.key(key_name.upper())
    except (BoseError, Exception) as e:
        raise _502(e)
    return {"ok": True}


@api.post("/api/volume")
async def set_volume(body: VolumeBody):
    level = max(0, min(100, body.level))
    log.info("volume -> %d", level)
    try:
        await bose.set_volume(level)
    except (BoseError, Exception) as e:
        raise _502(e)
    return {"ok": True}


@api.post("/api/source")
async def select_source(body: SourceBody):
    log.info("source -> %s (account=%r)", body.source, body.account)
    try:
        await bose.select_source(body.source, body.account)
    except (BoseError, Exception) as e:
        raise _502(e)
    return {"ok": True}


@api.get("/api/sources")
async def list_sources():
    """The device's sources, one entry per source type (a READY entry wins).

    Bluetooth reports UNAVAILABLE while nothing is connected but can still be
    selected, so the app shows every source the device lists, whatever its status.
    """
    try:
        root = ET.fromstring(await bose.sources())
    except ET.ParseError as e:
        raise HTTPException(500, detail=f"Invalid XML from speaker: {e}")
    except (BoseError, Exception) as e:
        raise _502(e)
    out: dict[str, dict] = {}
    for item in root.iter("sourceItem"):
        src = item.get("source", "")
        entry = {"source": src, "account": item.get("sourceAccount", ""),
                 "status": item.get("status", "")}
        if src and (src not in out or (entry["status"] == "READY" and out[src]["status"] != "READY")):
            out[src] = entry
    return list(out.values())


@api.get("/api/diagnose/sources_raw")
async def diagnose_sources_raw():
    """Raw /sources XML — which sources are READY vs UNAVAILABLE right now."""
    try:
        return {"xml": await bose.sources()}
    except (BoseError, Exception) as e:
        raise _502(e)


@api.get("/api/diagnose/presets_raw")
async def diagnose_presets_raw():
    """Raw /presets XML — read the sourceAccount attribute on existing TUNEIN presets."""
    try:
        return {"xml": await bose.presets()}
    except (BoseError, Exception) as e:
        raise _502(e)


@api.post("/api/test/tunein_stationurl")
async def test_tunein_stationurl(body: PlayUrlBody, account: str = ""):
    """
    Alternate test: source=TUNEIN (already valid) + type="stationurl" +
    a direct HTTP stream URL in location, instead of a Bose-cloud
    /v1/playback/station/sXXXXX path. Pass ?account=<sourceAccount from
    GET /api/diagnose/presets_raw> to match an existing TUNEIN preset.
    """
    log.info("test tunein_stationurl -> %s (account=%r)", body.url, account)
    try:
        return {"response": await bose.select_tunein_stationurl(body.url, body.name, account)}
    except (BoseError, Exception) as e:
        raise _502(e)


@api.post("/api/test/local_internet_radio")
async def test_local_internet_radio(body: PlayUrlBody):
    """
    Definitive test: POST /select on the speaker with
    source=LOCAL_INTERNET_RADIO, type="stationurl", location=<url>,
    sourceAccount=<BMX anon user id>. Returns the raw speaker response
    (success or 1005 fault) regardless of HTTP status.
    """
    log.info("test local_internet_radio -> %s", body.url)
    try:
        return {"response": await bose.select_internet_radio(body.url, body.name)}
    except (BoseError, Exception) as e:
        raise _502(e)


@api.post("/api/play_url")
async def play_url(body: PlayUrlBody):
    log.info("play_url -> %s", body.url)
    try:
        await bose.play_stream(body.url, body.name)
    except (BoseError, Exception) as e:
        raise _502(e)
    return {"ok": True}


@api.get("/api/diagnose")
async def diagnose():
    """Run a suite of shell probes on the speaker and return the findings."""
    try:
        return await bose.diagnose()
    except (BoseError, Exception) as e:
        raise _502(e)


@api.get("/api/diagnose/filesystem")
async def diagnose_filesystem():
    """
    Full filesystem scan: /mnt/nv tree, all XML files, partition mounts,
    Sources.xml candidate probe, UpnpSource config, and writable-partition check.

    Call this when Sources.xml cannot be found at the assumed path.
    Paste the complete JSON in the bug report.
    """
    try:
        return await bose.diagnose_filesystem()
    except (BoseError, Exception) as e:
        raise _502(e)


@api.get("/api/diagnose/upnp")
async def diagnose_upnp():
    """
    Pinpoint the AVTransport controlURL and the port the UPnP renderer
    actually listens on (UpnpSource-spotty.xml on this firmware has no
    descriptionURL, so it can't be derived from config alone).

    Call this when play_url() fails with "Cannot extract AVTransport
    controlURL". Once you find the real path, set BOSE_UPNP_CONTROL_URL
    in docker-compose.yml to skip discovery entirely.
    """
    try:
        return await bose.diagnose_upnp()
    except (BoseError, Exception) as e:
        raise _502(e)


@api.get("/api/diagnose/upnp_control")
async def diagnose_upnp_control():
    """
    Unmask why curl against the AVTransport controlURL returns an empty
    string with -s: re-runs SetAVTransportURI with -v + exit-code echo,
    checks whether port 40010 is in LISTEN state, UpnpSource process health,
    and whether the UPNP source is reported available.

    Call this immediately after a play_url() attempt that logged empty
    SetAVTransportURI/Play responses.
    """
    try:
        return await bose.diagnose_upnp_control()
    except (BoseError, Exception) as e:
        raise _502(e)


@api.get("/api/diagnose/port17000")
async def diagnose_port17000():
    """
    Fishing expedition on the port-17000 diagnostic CLI for a command that
    forces the UPNP source from UNAVAILABLE to READY, or otherwise activates
    UpnpSource's SOAP server. Returns raw output for each probed command.
    """
    try:
        return await bose.diagnose_port17000()
    except (BoseError, Exception) as e:
        raise _502(e)


@api.get("/api/diagnose/port17000_keys")
async def diagnose_port17000_keys():
    """
    Clean (isolated-connection) re-test of port-17000's `key` command, which
    was confirmed real ("key play"/"key pause" returned "Key sent"). Also
    probes "select"/"source"/"play_url"/"url"-style commands using the same
    lowercase naming convention — this is the syntax we need for stream
    injection if found.
    """
    try:
        return await bose.diagnose_port17000_keys()
    except (BoseError, Exception) as e:
        raise _502(e)


@api.get("/api/diagnose/internal_api")
async def diagnose_internal_api():
    """
    Strategy pivot: UpnpSource (40010) is a dead end. Map the internal-only
    API on port 8091 (STSCertified) and the IPC surface of STSCertified /
    APServer, looking for a way to inject playback via an already-READY
    source (SPOTIFY/AUX) instead of the UNAVAILABLE UPNP source.
    """
    try:
        return await bose.diagnose_internal_api()
    except (BoseError, Exception) as e:
        raise _502(e)


@api.get("/api/diagnose/ipc_bus")
async def diagnose_ipc_bus():
    """
    Map /tmp/cliservercommand.uds — the IPC command bus shared by APServer,
    STSCertified, and BoseApp. Confirms strace availability and the PIDs to
    attach to before snooping.
    """
    try:
        return await bose.diagnose_ipc_bus()
    except (BoseError, Exception) as e:
        raise _502(e)


@api.post("/api/diagnose/ipc_snoop/start")
async def ipc_snoop_start():
    """
    Attach strace to APServer/STSCertified/BoseApp and log read/write/send/recv
    syscalls to /tmp/ts_ipc_snoop.log on the speaker. Press PLAY or a preset
    button on the physical speaker after starting, then read the log.
    """
    try:
        return await bose.start_ipc_snoop()
    except (BoseError, Exception) as e:
        raise _502(e)


@api.post("/api/diagnose/ipc_snoop/stop")
async def ipc_snoop_stop():
    """Stop the background strace capture."""
    try:
        return await bose.stop_ipc_snoop()
    except (BoseError, Exception) as e:
        raise _502(e)


@api.get("/api/diagnose/ipc_snoop/log")
async def ipc_snoop_log(lines: int = 300):
    """Read the captured IPC traffic."""
    try:
        return await bose.get_ipc_snoop_log(lines)
    except (BoseError, Exception) as e:
        raise _502(e)


@api.get("/api/diagnose/ipc_tools")
async def diagnose_ipc_tools():
    """Confirm socat/nc -U availability on the speaker before ipc_probe()."""
    try:
        return await bose.diagnose_ipc_tools()
    except (BoseError, Exception) as e:
        raise _502(e)


@api.get("/api/diagnose/ipc_probe")
async def diagnose_ipc_probe(command: str | None = None, preset: str | None = None):
    """
    Send one payload to /tmp/cliservercommand.uds and capture the response.

    Usage:
      GET /api/diagnose/ipc_probe?preset=cli_play
      GET /api/diagnose/ipc_probe?command=key+PLAY

    Available presets: cli_play, cli_preset1, cli_sources, cli_help,
    json_cmd_play, json_rpc_key, framed_key_play (see bose._IPC_PRESETS).
    Requires socat or `nc -U` on the speaker — check GET /api/diagnose/ipc_tools first.
    """
    try:
        return await bose.ipc_probe(command=command, preset=preset)
    except (BoseError, Exception) as e:
        raise _502(e)


@api.get("/api/diagnose/bmx")
async def diagnose_bmx():
    """
    Targeted BMX/source-registration diagnostic.

    Runs shell probes on the speaker to answer:
      - Is LOCAL_INTERNET_RADIO in the runtime source table?
      - What does the firmware's cached registry look like on disk?
      - What sourceAccount does an existing TUNEIN preset use (format reference)?
      - What BMX-related processes/files are present?

    Call this after a reboot and read every field before assuming anything
    about source registration state.
    """
    try:
        return await bose.diagnose_bmx()
    except (BoseError, Exception) as e:
        raise _502(e)


# ── BMX service registry ──────────────────────────────────────────────────────
#
# The speaker fetches bmxRegistryUrl at every boot to populate its internal
# source table.  LOCAL_INTERNET_RADIO (id.value=11) only exists in that table
# if this endpoint returns a valid registry JSON.  Without it every /select
# call with source=LOCAL_INTERNET_RADIO returns 1005 UNKNOWN_SOURCE_ERROR.
#
# JSON format confirmed from Soundploy's live registry server.
# The speaker calls this once at boot; the response is cached for askAgainAfter
# seconds (86400 = 24 h) so restarts of this container don't cause re-fetches.

_SERVER_URL = bose._SERVER_URL


@api.get("/bmx/registry/v1/services")
async def bmx_registry():
    return JSONResponse({
        "_links": {
            "bmx_services_availability": {"href": "../servicesAvailability"}
        },
        "askAgainAfter": 86400,
        "bmx_services": [
            {
                "_links": {
                    "bmx_token": {"href": "/token"},
                    "self":      {"href": "/"},
                },
                "askAdapter": False,
                "assets": {
                    "color":       "#1a1a2e",
                    "description": "TouchingSounds — local radio streams.",
                    "icons": {
                        "largeSvg":      "https://media.bose.io/bmx-icons/orion/monochrome.svg",
                        "monochromePng": "https://media.bose.io/bmx-icons/orion/monochrome_v2.png",
                        "monochromeSvg": "https://media.bose.io/bmx-icons/orion/monochrome.svg",
                        "smallSvg":      "https://media.bose.io/bmx-icons/orion/monochrome.svg",
                    },
                    "name": "TouchingSounds",
                },
                "authenticationModel": {
                    "anonymousAccount": {"autoCreate": True, "enabled": True}
                },
                # baseUrl is where the speaker browses for stations.
                # For preset /select calls the speaker resolves the location
                # URL directly, so this endpoint only matters for in-app browsing.
                "baseUrl":     f"{_SERVER_URL}/bmx/orion",
                "id":          {"name": "LOCAL_INTERNET_RADIO", "value": 11},
                "streamTypes": ["liveRadio"],
            }
        ],
    })


@api.get("/bmx/registry/v1/servicesAvailability")
async def bmx_services_availability():
    """Linked from the registry; must return 200 or the speaker marks the source offline."""
    return JSONResponse({"available": True})


@api.get("/bmx/orion/station")
async def bmx_orion_station(data: str):
    """
    Resolve a LOCAL_INTERNET_RADIO location ("/station?data=<base64 json>",
    built by bose.orion_location) into the real stream URL.  The speaker
    calls this for every custom-stream /select and preset press.
    Response shape: soundcork / AfterTouch BMX playback response.
    """
    try:
        station = bose.decode_orion_data(data)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    stream_url = station["streamUrl"]
    log.info("BMX: station %r -> %s", station.get("name", ""), stream_url)
    stream_url = bose.speaker_stream_url(stream_url)
    return JSONResponse({
        "audio": {
            "hasPlaylist": True,
            "isRealtime":  True,
            "streamUrl":   stream_url,
            "streams": [{"hasPlaylist": True, "isRealtime": True, "streamUrl": stream_url}],
        },
        "imageUrl":   station.get("imageUrl", ""),
        "name":       station.get("name", ""),
        "streamType": "liveRadio",
    })


_RELAY_HEADERS = {"Content-Type": "audio/mpeg", "Cache-Control": "no-cache"}


@api.api_route("/relay", methods=["GET", "HEAD"])
async def relay(url: str, request: Request):
    """
    Re-stream an HTTPS or HLS station as plain-HTTP MP3 for the speaker,
    whose firmware has expired TLS root certs and no HLS support.
    One ffmpeg process per listener, killed when the speaker disconnects.
    """
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, detail="url must be http(s)")
    if request.method == "HEAD":
        return Response(headers=_RELAY_HEADERS)

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-loglevel", "error",
        "-protocol_whitelist", "http,https,tcp,tls,crypto",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-i", url,
        "-vn", "-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    log.info("relay: start pid=%s %s", proc.pid, url)

    async def pump():
        try:
            while chunk := await proc.stdout.read(16384):
                yield chunk
        finally:
            # Runs inside a cancelled scope when the speaker disconnects, so
            # kill synchronously and don't await anything before the log line.
            if proc.returncode is None:
                proc.kill()
            log.info("relay: stop pid=%s", proc.pid)

    return StreamingResponse(pump(), headers=_RELAY_HEADERS)


@api.get("/bmx/orion/token")
@api.post("/bmx/orion/token")
async def bmx_token():
    """
    Auth token endpoint referenced in the registry _links.
    The speaker hits this after parsing the registry (anonymousAccount.autoCreate=true).
    Returns a static long-lived anonymous token.
    """
    return JSONResponse({
        "token":     "touchingsounds-local-anon",
        "expiresIn": 86400,
    })


# ── BMX account creation ───────────────────────────────────────────────────────
#
# When the registry contains anonymousAccount.autoCreate=true, the speaker makes
# an outbound POST/GET to create (or fetch) an anonymous account on our server.
# Without a 200 response to this call, the source stays unregistered → 1005.
#
# The exact path the speaker calls is undocumented, so we handle every plausible
# variant.  The critical response fields are userId (becomes sourceAccount in
# ContentItem XML) and token.  We use a fixed deterministic value so every
# restart returns the same userId — the speaker caches it across boots.

_BMX_ANON_USER_ID = "touchingsounds-anon-001"
_BMX_ANON_TOKEN   = "touchingsounds-local-anon"

_BMX_ACCOUNT_RESPONSE = {
    "userId":      _BMX_ANON_USER_ID,
    "accountId":   _BMX_ANON_USER_ID,
    "token":       _BMX_ANON_TOKEN,
    "accessToken": _BMX_ANON_TOKEN,
    "expiresIn":   86400,
    "anonymous":   True,
}


@api.get("/bmx/orion/account")
@api.post("/bmx/orion/account")
@api.get("/bmx/orion/account/anonymous")
@api.post("/bmx/orion/account/anonymous")
@api.get("/bmx/orion/accounts")
@api.post("/bmx/orion/accounts")
@api.post("/bmx/orion/accounts/anonymous")
async def bmx_account():
    """
    Anonymous account provisioning.  The speaker calls one of these paths
    after receiving anonymousAccount.autoCreate=true in the registry.
    The userId returned here becomes the sourceAccount attribute in ContentItem XML.
    """
    log.info("BMX: account provisioning request → returning userId=%s", _BMX_ANON_USER_ID)
    return JSONResponse(_BMX_ACCOUNT_RESPONSE)


@api.get("/bmx/orion/account/{path:path}")
@api.post("/bmx/orion/account/{path:path}")
async def bmx_account_catchall(path: str):
    """Catch-all for any account sub-path the speaker requests."""
    log.info("BMX: account sub-path /%s → returning account response", path)
    return JSONResponse(_BMX_ACCOUNT_RESPONSE)


# ── Static / frontend ─────────────────────────────────────────────────────────

api.mount("/static", StaticFiles(directory="app/static"), name="static")

@api.get("/")
async def root():
    return FileResponse("app/static/index.html", headers={"Cache-Control": "no-cache"})


@api.get("/sw.js")
async def service_worker():
    """Served from the root so its scope covers the whole app (PWA install)."""
    return FileResponse("app/static/sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})
