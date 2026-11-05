"""
clock.py — keep each speaker's display clock on the right local time.

The speakers' built-in time-zone data is from before 2016, so for some zones
it's out of date: a SoundTouch 20 set to Europe/Istanbul still switches to
UTC+2 every winter, although Türkiye has stayed on UTC+3 since 2016.  Bose's
cloud no longer corrects that.

So the server checks every speaker's clock now and then.  The speaker's UTC
time is right (it syncs it itself); only its local-time rule may be wrong.  We
compare the offset it shows with the real one for our time zone (from Python's
up-to-date zone data) and, if they differ, set the speaker to a fixed-offset
zone such as Etc/GMT-3, which every speaker has.  When summer/winter time
changes in a zone that has it, the next check moves the fixed offset along.
A speaker that already shows the right time is left alone.

The time zone is TS_TIME_ZONE (.env) if set, otherwise the zone of the phone
that last opened the app (stored in TS_DATA_DIR/clock.json).
"""

import asyncio
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter
from pydantic import BaseModel

import app.bose as bose
from app.bose import BoseError

log = logging.getLogger("clock")

_DATA_DIR = os.getenv("TS_DATA_DIR", "data")
_FILE = os.path.join(_DATA_DIR, "clock.json")
_ENV_ZONE = os.getenv("TS_TIME_ZONE", "").strip()

INTERVAL = 15 * 60     # seconds between checks
_SANE_UTC = 1_600_000_000   # a speaker that hasn't synced its clock yet reports less

router = APIRouter()
_task: asyncio.Task | None = None
_phone_zone = ""
_status: dict[str, dict] = {}      # host → result of its last check


def _valid(name: str) -> bool:
    try:
        ZoneInfo(name)
        return bool(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False


def _load() -> str:
    try:
        with open(_FILE, encoding="utf-8") as f:
            return str(json.load(f).get("phone_zone") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def _save() -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"phone_zone": _phone_zone}, f, indent=2)
    os.replace(tmp, _FILE)


def zone() -> str:
    """The time zone speakers should show, or "" if none is known yet."""
    if _ENV_ZONE and _valid(_ENV_ZONE):
        return _ENV_ZONE
    return _phone_zone if _valid(_phone_zone) else ""


def _offset_minutes(zone_name: str, at: float) -> int:
    off = datetime.fromtimestamp(at, ZoneInfo(zone_name)).utcoffset()
    return int(off.total_seconds() // 60)


def fixed_zone(minutes: int) -> str | None:
    """The Etc/GMT zone for a whole-hour offset (its sign is inverted: UTC+3 is Etc/GMT-3)."""
    if minutes % 60 or not -12 <= minutes // 60 <= 14:
        return None
    h = minutes // 60
    return "Etc/GMT" if h == 0 else f"Etc/GMT{-h:+d}"


def speaker_offset(clock_time_xml: str) -> tuple[int, float] | None:
    """(offset in minutes, UTC time) the speaker shows, from its /clockTime; None if unknown."""
    root = ET.fromstring(clock_time_xml)
    utc = float(root.get("utcTime") or 0)
    lt = root.find("localTime")
    if utc < _SANE_UTC or lt is None:
        return None
    a = {k: int(lt.get(k) or 0) for k in ("year", "month", "dayOfMonth", "hour", "minute", "second")}
    # month is 0-based on the speaker (November = 10)
    local = datetime(a["year"], a["month"] + 1, a["dayOfMonth"], a["hour"], a["minute"], a["second"],
                     tzinfo=timezone.utc).timestamp()
    return round((local - utc) / 900) * 15, utc


async def check(h: str) -> dict:
    """Check one speaker's clock and correct its time zone if needed."""
    want_zone = zone()
    result: dict = {"checked": time.time(), "zone": want_zone}
    try:
        if not want_zone:
            return result | {"state": "no-zone"}
        with bose.use_device(h):
            shown = speaker_offset(await bose.clock_time())
            if shown is None:
                return result | {"state": "not-synced"}
            have, utc = shown
            want = _offset_minutes(want_zone, utc)
            if have == want:
                return result | {"state": "ok", "offset": have}
            target = fixed_zone(want) or want_zone
            root = ET.fromstring(await bose.clock_display())
            cfg = root.find("clockConfig")
            if cfg is None:
                return result | {"state": "no-clock"}
            was = cfg.get("timezoneInfo")
            cfg.set("timezoneInfo", target)      # every other clock setting stays as it was
            await bose.set_clock_display(ET.tostring(root, encoding="unicode"))
            log.info("Device %s: clock showed %s, should be %s (%s); zone %s -> %s",
                     h, _utc(have), _utc(want), want_zone, was, target)
            return result | {"state": "fixed", "offset": want, "was": was, "now": target}
    except (BoseError, ET.ParseError) as exc:
        return result | {"state": "unreachable", "error": str(exc)}


def _utc(minutes: int) -> str:
    m = abs(minutes)
    return f"UTC{'-' if minutes < 0 else '+'}{m // 60}" + (f":{m % 60:02d}" if m % 60 else "")


async def _check_and_keep(h: str) -> None:
    r = await check(h)
    _status[h] = r


async def check_all() -> None:
    await asyncio.gather(*(_check_and_keep(d.host) for d in list(bose.DEVICES)))


async def _loop() -> None:
    await asyncio.sleep(60)          # let the startup setup finish first
    while True:
        try:
            await check_all()
        except Exception as exc:       # never let the clock check stop for good
            log.error("Clock check failed: %s", exc)
        await asyncio.sleep(INTERVAL)


def start() -> None:
    global _task, _phone_zone
    _phone_zone = _load()
    _task = asyncio.create_task(_loop())
    log.info("Clock: zone %s", zone() or "not known yet (opens with the app)")


async def stop_all() -> None:
    if _task:
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)


def on_added(h: str) -> None:
    """A speaker was just added: set its clock right away."""
    asyncio.create_task(_check_and_keep(h))


# ── API ──────────────────────────────────────────────────────────────────────

class ZoneBody(BaseModel):
    tz: str


@router.post("/api/clock/zone")
async def report_zone(body: ZoneBody):
    """The app reports its phone's time zone; speakers follow it unless TS_TIME_ZONE is set."""
    global _phone_zone
    if _valid(body.tz) and body.tz != _phone_zone:
        old_effective = zone()
        _phone_zone = body.tz
        _save()
        if zone() != old_effective:
            log.info("Clock: zone is now %s (from the app)", zone())
            asyncio.create_task(check_all())
    return {"zone": zone(), "from": "settings" if _ENV_ZONE and _valid(_ENV_ZONE) else "app"}


@router.get("/api/clock")
async def clock_status():
    """The zone speakers follow and each speaker's last clock check."""
    return {"zone": zone(), "devices": _status}
