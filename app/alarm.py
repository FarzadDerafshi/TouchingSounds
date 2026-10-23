"""
alarm.py — wake-up alarms: start a station on a speaker at a set time.

Runs on the server, like the sleep timer, so it fires with the phone locked
or the app closed.  Each alarm belongs to one device and holds a time of day,
the weekdays it repeats on (none = once), a station, a wake-up volume and the
time zone of the phone that set it (the container itself runs on UTC).

At the alarm time: the volume goes down to a whisper, the station is
selected (which also wakes the speaker from standby), and the volume rises
to the wake-up volume over RAMP seconds.  If the station hasn't started
after START_WAIT seconds, the device's presets are tried in turn as a
backup.  The outcome, or why the alarm was missed (speaker unreachable,
server down at that time), is kept on the alarm for the app to show.

Alarms are stored in TS_DATA_DIR/alarms.json, so a release keeps them.
"""

import asyncio
import json
import logging
import os
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import app.bose as bose
import app.marge as marge

log = logging.getLogger("alarm")

_DATA_DIR = os.getenv("TS_DATA_DIR", "data")
_FILE = os.path.join(_DATA_DIR, "alarms.json")

RAMP = 90            # seconds from the start volume to the wake-up volume
RAMP_STEPS = 15
START_VOLUME = 5     # the whisper the ramp starts from
START_WAIT = 25      # seconds a station gets to start playing before the backup
GRACE = 300          # an alarm up to this late (server busy, just restarted) still fires
MAX_ALARMS = 10      # per device
TICK = 15            # seconds between checks

_alarms: list[dict] = []
_task: asyncio.Task | None = None
_running: dict[str, asyncio.Task] = {}      # alarm id → its firing


# ── Storage ──────────────────────────────────────────────────────────────────

def _load() -> list[dict]:
    try:
        with open(_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [a for a in data if isinstance(a, dict) and a.get("id")] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save() -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_alarms, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _FILE)


# ── Times ────────────────────────────────────────────────────────────────────

def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def valid_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def next_time(a: dict, after: float) -> float | None:
    """Epoch seconds of the alarm's next ring strictly after `after` (None if it can't)."""
    tz = _zone(a.get("tz", ""))
    hh, mm = (int(x) for x in a["time"].split(":"))
    start = datetime.fromtimestamp(after, tz)
    days = set(a.get("days") or [])
    for d in range(8):
        day = (start + timedelta(days=d)).date()
        if days and day.weekday() not in days:
            continue
        # Built as a wall-clock time in its zone; on a DST gap or overlap
        # zoneinfo picks a sensible instant, close enough for an alarm.
        at = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz).timestamp()
        if at > after:
            return at
    return None


def _schedule(a: dict, after: float) -> None:
    a["next"] = next_time(a, after) if a.get("enabled") else None


# ── The alarm itself ─────────────────────────────────────────────────────────

async def _status() -> tuple[str, str]:
    np = ET.fromstring(await bose.now_playing())
    return np.get("source", ""), np.findtext("playStatus", "")


async def _playing(timeout: float) -> bool:
    """Whether the speaker is playing internet radio within `timeout` seconds."""
    ends = time.time() + timeout
    while time.time() < ends:
        await asyncio.sleep(2.5)
        try:
            src, play = await _status()
        except Exception:
            continue
        if src == "LOCAL_INTERNET_RADIO" and play == "PLAY_STATE":
            return True
    return False


def _backups(h: str, url: str) -> list[dict]:
    """The device's preset stations other than `url`, as a fallback when the alarm's won't play."""
    out, seen = [], {url}
    presets = marge.presets(h)
    for slot in sorted((k for k in presets if k.isdigit()), key=int):
        st = bose.decode_orion_location(presets[slot].get("location", "")) or {}
        if st.get("streamUrl") and st["streamUrl"] not in seen:
            seen.add(st["streamUrl"])
            out.append({"name": st.get("name") or presets[slot].get("name", ""),
                        "url": st["streamUrl"], "favicon": st.get("imageUrl", "")})
    return out


def _unreachable(exc: Exception) -> bool:
    """A BoseError from not reaching the speaker at all (vs. the speaker refusing a station)."""
    m = str(exc)
    return "Cannot connect" in m or "timed out" in m or "did not respond" in m


def _result(a: dict, ok: bool, msg: str, at: float) -> None:
    a["last"] = {"at": round(at), "ok": ok, "msg": msg}


async def _ring(a: dict, at: float) -> None:
    h = a["device"]
    st = a["station"]
    target = max(1, min(100, int(a.get("volume", 25))))
    with bose.use_device(h):
        try:
            # Quiet first, so neither the last volume nor the new station blasts.
            await bose.set_volume(min(START_VOLUME, target))
            played = None
            for cand in [st, *_backups(h, st["url"])][:3]:
                try:
                    await bose.play_stream(cand["url"], cand["name"], cand.get("favicon", ""))
                except bose.BoseError as exc:
                    if _unreachable(exc):
                        raise
                    log.warning("Alarm %s: %s won't select: %s", a["id"], cand["name"], exc)
                    continue
                await bose.set_volume(min(START_VOLUME, target))
                if await _playing(START_WAIT):
                    played = cand
                    break
                log.warning("Alarm %s: %s didn't start", a["id"], cand["name"])
            if not played:
                _result(a, False, "No station would start", at)
                return
            log.info("Alarm %s on %s: playing %s", a["id"], h, played["name"])
            vol = min(START_VOLUME, target)
            for i in range(1, RAMP_STEPS + 1):
                await asyncio.sleep(RAMP / RAMP_STEPS)
                src, play = await _status()
                if src != "LOCAL_INTERNET_RADIO" or play not in ("PLAY_STATE", "BUFFERING_STATE"):
                    break          # stopped, switched off or to something else: leave it be
                new = round(vol + (target - vol) * i / RAMP_STEPS)
                await bose.set_volume(new)
            msg = "Played" if played is st else f"{st['name']} didn't start, played {played['name']}"
            _result(a, True, msg, at)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("Alarm %s on %s failed: %s", a["id"], h, exc)
            _result(a, False, "Speaker unreachable" if _unreachable(exc) else f"Failed: {exc}", at)
        finally:
            _running.pop(a["id"], None)
            _save()


def _fire(a: dict, at: float) -> None:
    if a["id"] in _running:
        return
    _running[a["id"]] = asyncio.create_task(_ring(a, at))


async def _loop() -> None:
    while True:
        now = time.time()
        changed = False
        for a in _alarms:
            at = a.get("next")
            if not a.get("enabled") or not at or at > now:
                continue
            if now - at <= GRACE:
                log.info("Alarm %s on %s: ringing (%s)", a["id"], a["device"], a["time"])
                _fire(a, at)
            else:
                log.warning("Alarm %s on %s: missed (%s), server was not running", a["id"], a["device"], a["time"])
                _result(a, False, "Missed: server was off", at)
            if not a.get("days"):
                a["enabled"] = False       # a one-off alarm rings once
            _schedule(a, max(now, at))
            changed = True
        if changed:
            _save()
        await asyncio.sleep(TICK)


# ── API for main.py ──────────────────────────────────────────────────────────

def start() -> None:
    """Load the alarms and start the clock (lifespan startup)."""
    global _task
    _alarms[:] = _load()
    now = time.time()
    for a in _alarms:
        # Keep a stored "next" that's due now or was missed while the server was
        # down, so the loop rings or reports it; otherwise recompute (time zone
        # rules, edits by hand).
        if not (a.get("enabled") and a.get("next") and a["next"] <= now):
            _schedule(a, now)
    _save()
    _task = asyncio.create_task(_loop())
    log.info("Alarms: %d loaded", len(_alarms))


async def stop_all() -> None:
    tasks = [t for t in [_task, *_running.values()] if t]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _running.clear()


def _view(a: dict) -> dict:
    return {k: a.get(k) for k in ("id", "time", "days", "station", "volume", "enabled", "tz", "next", "last")} | \
           {"ringing": a["id"] in _running}


def for_device(h: str) -> list[dict]:
    return [_view(a) for a in sorted((a for a in _alarms if a["device"] == h), key=lambda a: a["time"])]


PROBLEM_SHOWN = 12 * 3600    # how long the player flags an alarm that didn't play


def summary(h: str) -> dict | None:
    """For the player: the device's next alarm and any recent one that didn't play."""
    mine = [a for a in _alarms if a["device"] == h]
    up = [a for a in mine if a.get("enabled") and a.get("next")]
    nxt = min(up, key=lambda a: a["next"]) if up else None
    bad = [a["last"] for a in mine if a.get("last") and not a["last"]["ok"]
           and time.time() - a["last"]["at"] < PROBLEM_SHOWN]
    if not nxt and not bad:
        return None
    return {
        "next": {"id": nxt["id"], "time": nxt["time"], "at": nxt["next"]} if nxt else None,
        "problem": max(bad, key=lambda l: l["at"]) if bad else None,
        "ringing": any(a["id"] in _running for a in mine),
    }


def _clean(data: dict) -> dict:
    t = str(data.get("time", ""))
    try:
        hh, mm = (int(x) for x in t.split(":"))
        assert 0 <= hh < 24 and 0 <= mm < 60
    except (ValueError, AssertionError):
        raise ValueError("Time must be HH:MM")
    days = sorted({int(d) for d in data.get("days") or [] if 0 <= int(d) <= 6})
    st = data.get("station") or {}
    if not st.get("url") or not st.get("name"):
        raise ValueError("Pick a station")
    bose.validate_playable_url(st["url"])
    tz = data.get("tz") or "UTC"
    if not valid_zone(tz):
        raise ValueError(f"Unknown time zone {tz!r}")
    return {
        "time": f"{hh:02d}:{mm:02d}",
        "days": days,
        "station": {"name": str(st["name"]), "url": str(st["url"]), "favicon": str(st.get("favicon") or "")},
        "volume": max(1, min(100, int(data.get("volume", 25)))),
        "enabled": bool(data.get("enabled", True)),
        "tz": tz,
    }


def add(h: str, data: dict) -> dict:
    if sum(a["device"] == h for a in _alarms) >= MAX_ALARMS:
        raise ValueError(f"At most {MAX_ALARMS} alarms per speaker")
    a = {"id": uuid.uuid4().hex[:10], "device": h, **_clean(data), "last": None}
    _schedule(a, time.time())
    _alarms.append(a)
    _save()
    log.info("Alarm %s on %s: added %s days=%s", a["id"], h, a["time"], a["days"])
    return _view(a)


def _find(h: str, alarm_id: str) -> dict:
    for a in _alarms:
        if a["id"] == alarm_id and a["device"] == h:
            return a
    raise KeyError(alarm_id)


def update(h: str, alarm_id: str, data: dict) -> dict:
    a = _find(h, alarm_id)
    if set(data) <= {"enabled"}:          # just the switch
        a["enabled"] = bool(data.get("enabled"))
    else:
        a.update(_clean({**_view(a), **data}))
    _schedule(a, time.time())
    _save()
    return _view(a)


def remove(h: str, alarm_id: str) -> None:
    a = _find(h, alarm_id)
    _alarms.remove(a)
    t = _running.pop(alarm_id, None)
    if t:
        t.cancel()
    _save()


def test(h: str, alarm_id: str) -> dict:
    """Ring an alarm now (the app's "Try it" button); doesn't move its schedule."""
    a = _find(h, alarm_id)
    _fire(a, time.time())
    return _view(a)
