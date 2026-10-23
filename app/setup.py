"""
setup.py — the "Add a speaker" wizard's server side (1.5).

The web app walks the user to a speaker the server can reach on port 8090;
from there this module does the rest without anyone touching the speaker:

  1. Discovery: a sweep of port 8090 (/info) over the subnets the server can
     route to.  mDNS/SSDP would be quicker, but multicast doesn't cross
     Docker's bridge network or the WireGuard tunnel to the home network.
  2. Wi-Fi (speakers plugged in with a cable, or online ones changing
     network): the speaker scans for networks itself
     (performWirelessSiteSurvey) and saves the chosen one
     (addWirelessProfile).  The password goes phone -> server -> speaker and
     is never stored or logged.
  3. Takeover: the port-17000 console redirect from 1.1.0 (no root needed),
     one reboot, then name, presets and a quiet test sound.

Speakers in setup mode (their own access point, 192.0.2.1) are out of the
server's reach; the web app hands their Wi-Fi details over from the phone.
"""

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import time
import uuid
import xml.etree.ElementTree as ET

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import app.alarm as alarm
import app.bose as bose
import app.library as library
import app.marge as marge
import app.sleep as sleep
from app.bose import BoseError

log = logging.getLogger("setup")

router = APIRouter()

# Set by app.main: start/stop a device's background tasks (the preset listener).
on_added = None    # callable(host) -> None
on_removed = None  # callable(host) -> None


# ── Discovery ────────────────────────────────────────────────────────────────

# Subnets to sweep, comma-separated CIDRs.  Default: the /24 of every
# configured speaker (home network over WireGuard, office LAN).
_SCAN_SUBNETS = os.getenv("TS_SCAN_SUBNETS", "").strip()
_PROBE_CONNECT = 0.8
_SWEEP_CACHE_S = 4.0

_last_sweep: tuple[float, list[dict]] = (0.0, [])
_sweep_lock = asyncio.Lock()


def _subnets() -> list[ipaddress.IPv4Network]:
    raw = [x.strip() for x in _SCAN_SUBNETS.split(",") if x.strip()] or \
          [f"{d.host}/24" for d in bose.DEVICES]
    nets: list[ipaddress.IPv4Network] = []
    for item in raw:
        with contextlib.suppress(ValueError):
            net = ipaddress.ip_network(item, strict=False)
            if net.version == 4 and net.num_addresses <= 1024 and net not in nets:
                nets.append(net)
    return nets


async def _port_open(ip: str) -> bool:
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(ip, int(bose._PORT)), _PROBE_CONNECT)
    except (OSError, asyncio.TimeoutError):
        return False
    w.close()
    with contextlib.suppress(Exception):
        await w.wait_closed()
    return True


async def probe(ip: str) -> dict | None:
    """What answers on `ip`:8090, or None.  Includes how it's connected."""
    if not await _port_open(ip):
        return None
    with bose.use_device(ip):
        try:
            root = ET.fromstring(await bose.info())
        except (BoseError, ET.ParseError):
            return None
        net = {"wifi": None, "ethernet": ""}
        with contextlib.suppress(BoseError, ET.ParseError):
            net = await bose.network_state()
    wifi = net["wifi"]
    return {
        "host": ip,
        "deviceID": root.get("deviceID", ""),
        "name": root.findtext("name", "") or ip,
        "type": root.findtext("type", ""),
        "known": bose.is_known_host(ip),
        "connection": "ethernet" if net["ethernet"] == ip else "wifi",
        "wifi": wifi["ssid"] if wifi else "",
        "signal": wifi["signal"] if wifi else "",
    }


async def sweep() -> list[dict]:
    """Every speaker answering on the swept subnets (cached for a few seconds)."""
    global _last_sweep
    async with _sweep_lock:
        at, found = _last_sweep
        if time.monotonic() - at < _SWEEP_CACHE_S:
            return found
        sem = asyncio.Semaphore(128)

        async def one(ip: str):
            async with sem:
                return await probe(ip)

        ips = [str(ip) for net in _subnets() for ip in net.hosts()]
        started = time.monotonic()
        results = await asyncio.gather(*(one(ip) for ip in ips))
        found = [r for r in results if r]
        log.info("Sweep of %d addresses found %d speaker(s) in %.1f s",
                 len(ips), len(found), time.monotonic() - started)
        _last_sweep = (time.monotonic(), found)
        return found


def _check_host(h: str) -> str:
    try:
        return str(ipaddress.ip_address(h.strip()))
    except ValueError:
        raise HTTPException(400, detail="That isn't an IP address, e.g. 192.168.1.60")


@router.get("/api/setup/discover")
async def discover():
    """Speakers on the network; `known` ones are in the app already."""
    found = await sweep()
    known_ids = {bose.identity(d.host).get("deviceID", "").upper() for d in bose.DEVICES}
    for f in found:
        f["known"] = f["known"] or f["deviceID"].upper() in known_ids
    return {"speakers": found, "subnets": [str(n) for n in _subnets()]}


@router.get("/api/setup/probe")
async def probe_one(host: str):
    """The 'enter an address' fallback."""
    found = await probe(_check_host(host))
    if not found:
        raise HTTPException(404, detail="No speaker answers at that address.")
    return found


@router.get("/api/setup/find")
async def find(device_id: str):
    """Where speaker `device_id` is now (after it moved from the cable to Wi-Fi)."""
    global _last_sweep
    _last_sweep = (0.0, [])
    for f in await sweep():
        if f["deviceID"].upper() == device_id.upper():
            return f
    return {"host": ""}


# ── Wi-Fi ────────────────────────────────────────────────────────────────────

class JoinBody(BaseModel):
    host: str
    ssid: str = Field(min_length=1, max_length=32)
    password: str = Field(default="", max_length=63)
    security: str = "wpa_or_wpa2"


@router.get("/api/setup/wifi/networks")
async def wifi_networks(host: str):
    h = _check_host(host)
    with bose.use_device(h):
        try:
            nets = await bose.wifi_survey()
        except BoseError as exc:
            raise HTTPException(502, detail=str(exc))
        current = None
        with contextlib.suppress(BoseError, ET.ParseError):
            current = (await bose.network_state())["wifi"]
    return {"networks": nets, "current": current["ssid"] if current else ""}


@router.post("/api/setup/wifi/join")
async def wifi_join(body: JoinBody):
    """
    Save the network on the speaker, then wait up to ~40 s for it to join.
    On a cable the speaker may only switch to Wi-Fi once it's unplugged, so
    `joined: false` isn't a failure: the app then asks to unplug the cable
    and looks for the speaker by its deviceID.
    """
    h = _check_host(body.host)
    if body.security != "none" and len(body.password) < 8:
        raise HTTPException(400, detail="Wi-Fi passwords are at least 8 characters.")
    with bose.use_device(h):
        try:
            await bose.add_wifi_profile(body.ssid, body.password, body.security)
        except BoseError as exc:
            raise HTTPException(502, detail=str(exc))
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            await asyncio.sleep(4)
            with contextlib.suppress(BoseError, ET.ParseError):
                wifi = (await bose.network_state())["wifi"]
                if wifi and wifi["ssid"] == body.ssid and wifi["ip"]:
                    return {"joined": True, "ip": wifi["ip"]}
    return {"joined": False, "ip": ""}


# ── Takeover job ─────────────────────────────────────────────────────────────
#
# The job does the slow part on its own (console redirect, restart, about a
# minute); meanwhile the user names the speaker and picks its presets, which
# /api/setup/finish applies before the test sound.

_STEPS = [
    ("redirect", "Connecting it to TouchingSounds", "Radio and presets will come from your server"),
    ("reboot",   "Restarting the speaker",          "About a minute. Its lights may go off."),
]

_JOBS: dict[str, dict] = {}


class AddBody(BaseModel):
    host: str


def _step(job: dict, key: str, state: str, detail: str | None = None) -> None:
    for s in job["steps"]:
        if s["key"] == key:
            s["state"] = state
            if detail is not None:
                s["detail"] = detail


def _originals_file(h: str) -> str:
    return os.path.join(marge.data_dir(h), "original_urls.json")


async def _test_sound(h: str, volume: int) -> str:
    """Play something quietly on `h`; returns what's playing."""
    station = None
    for _, p in sorted(marge.presets(h).items()):
        st = bose.decode_orion_location(p.get("location", ""))
        if st and st.get("streamUrl"):
            station = (st["streamUrl"], p.get("name") or st.get("name") or "Radio")
            break
    if station is None:
        envp = bose.env_presets()
        if envp:
            first = envp[min(envp)]
            station = (first["url"], first["name"])
    if station is None:
        for st in library.saved_stations():
            station = (st["url"], st["name"])
            break
    if station is None:
        raise BoseError("No station to test with: play or save a station first.")
    with bose.use_device(h):
        await bose.set_volume(volume)
        await bose.play_stream(*station)
    return station[1]


async def _takeover(job: dict) -> None:
    h = job["host"]
    current = "redirect"
    try:
        _step(job, "redirect", "active")
        await bose.refresh_identity(h)
        with contextlib.suppress(BoseError):
            orig = await bose.console_cloud_urls(h)
            if orig["marge"] and orig != bose.our_cloud_urls() and not os.path.exists(_originals_file(h)):
                marge._write_text(_originals_file(h), json.dumps(orig, indent=2))
        needs_reboot = await bose.setup_via_console(h)
        _step(job, "redirect", "done")

        current = "reboot"
        if needs_reboot:
            _step(job, "reboot", "active")
            if not await bose.reboot_and_wait(h):
                raise BoseError("The speaker didn't come back after the restart. "
                                "Unplug its power for 10 seconds, then try again.")
            _step(job, "reboot", "done", "Done")
        else:
            _step(job, "reboot", "done", "Not needed: it already used your server")
        await bose.refresh_identity(h)
        if on_added:
            on_added(h)
        job["state"] = "done"
    except Exception as exc:
        log.error("Device %s: setup failed at %s: %s", h, current, exc)
        _step(job, current, "failed", str(exc))
        job["state"] = "failed"
        job["error"] = str(exc)
        if current == "redirect" and job.get("added_now"):
            with contextlib.suppress(BoseError):
                bose.remove_device(h)


@router.post("/api/setup/add")
async def add(body: AddBody):
    """Add the speaker at `host` and start the automatic takeover; poll /api/setup/jobs/{id}."""
    h = _check_host(body.host)
    for j in _JOBS.values():
        if j["host"] == h and j["state"] == "running":
            return _public(j)
    found = await probe(h)
    if not found:
        raise HTTPException(404, detail="The speaker doesn't answer at that address any more.")
    added_now = not bose.is_known_host(h)
    bose.add_device(h, fresh=True)
    job = {
        "id": uuid.uuid4().hex[:12], "host": h, "state": "running", "error": "",
        "name": found["name"], "type": found["type"], "wifi": found["wifi"],
        "added_now": added_now,
        "steps": [{"key": k, "label": label, "detail": detail, "state": "pending"}
                  for k, label, detail in _STEPS],
    }
    _JOBS[job["id"]] = job
    job["task"] = asyncio.create_task(_takeover(job))
    return _public(job)


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "task"}


@router.get("/api/setup/jobs/{job_id}")
async def job_status(job_id: str):
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, detail="Unknown setup job (the server restarted?)")
    return _public(job)


class FinishBody(BaseModel):
    host: str
    name: str = Field(default="", max_length=60)
    copy_presets_from: str = ""


@router.post("/api/setup/finish")
async def finish(body: FinishBody):
    """After the takeover: name it, set its presets, play the test sound."""
    h = _check_host(body.host)
    if not bose.is_known_host(h):
        raise HTTPException(404, detail="Unknown speaker")
    try:
        name = body.name.strip()
        if name and name != bose.identity(h).get("name"):
            with bose.use_device(h):
                await bose.set_name(name)
        ident = await bose.refresh_identity(h)
        src = body.copy_presets_from
        copied = 0
        if src and bose.is_known_host(src) and src != h:
            for slot, p in marge.presets(src).items():
                if slot.isdigit() and p.get("location"):
                    marge.save_preset(h, slot, p["name"], p["location"], p.get("art", ""))
                    copied += 1
        await library.sync_presets_for(h)
        playing = await _test_sound(h, 15)
    except BoseError as exc:
        raise HTTPException(502, detail=str(exc))
    return {"name": ident.get("name") or name or h, "type": ident.get("type", ""),
            "copied": copied, "playing": playing}


class TestBody(BaseModel):
    host: str
    louder: bool = False


@router.post("/api/setup/test")
async def test_sound(body: TestBody):
    """'No, nothing yet': play again, louder, and say what the speaker reports."""
    h = _check_host(body.host)
    if not bose.is_known_host(h):
        raise HTTPException(404, detail="Unknown speaker")
    try:
        playing = await _test_sound(h, 30 if body.louder else 15)
        await asyncio.sleep(4)
        with bose.use_device(h):
            np = ET.fromstring(await bose.now_playing())
    except (BoseError, ET.ParseError) as exc:
        raise HTTPException(502, detail=str(exc))
    return {"playing": playing, "source": np.get("source", ""),
            "playStatus": np.findtext("playStatus", "")}


# ── Remove ───────────────────────────────────────────────────────────────────

class RemoveBody(BaseModel):
    host: str
    undo_redirect: bool = False


@router.post("/api/setup/remove")
async def remove(body: RemoveBody):
    """
    Take a speaker out of the app.  With undo_redirect, its original cloud
    URLs (saved when it was added) go back through the console and it
    restarts; Bose's servers are gone, so that's only for handing it on.
    """
    h = _check_host(body.host)
    if not bose.is_known_host(h):
        raise HTTPException(404, detail="Unknown speaker")
    if bose.device(h).primary:
        raise HTTPException(400, detail="The first speaker can't be removed: it holds the original data.")
    undone = False
    if body.undo_redirect:
        try:
            with open(_originals_file(h), encoding="utf-8") as f:
                orig = json.load(f)
            if await bose.redirect_via_console(h, orig["marge"], orig["bmx"]):
                with contextlib.suppress(BoseError, OSError):
                    await bose._query_port17000(h, ["sys reboot"])
            undone = True
        except (OSError, ValueError, KeyError):
            raise HTTPException(409, detail="Its original settings weren't saved, so they can't be put back.")
        except BoseError as exc:
            raise HTTPException(502, detail=str(exc))
    if on_removed:
        on_removed(h)
    sleep.cancel(h)
    for a in alarm.for_device(h):
        with contextlib.suppress(Exception):
            alarm.remove(h, a["id"])
    bose.remove_device(h)
    return {"ok": True, "undone": undone}
