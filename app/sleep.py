"""
sleep.py — sleep timer: put a speaker in standby after N minutes.

Runs on the server, so it works with the phone locked or the app closed.
Over the last FADE seconds the volume steps down to nothing, then the
speaker goes to standby (POWER key, sent only if it isn't in standby
already) and the volume is put back, so the next morning doesn't start
silent.  One timer per device; setting a new one replaces it.  Timers live
in memory: a server restart (a release) cancels them.
"""

import asyncio
import logging
import time
import xml.etree.ElementTree as ET

import app.bose as bose

log = logging.getLogger("sleep")

FADE = 60           # seconds of fade-out before standby
FADE_STEPS = 12
MAX_MINUTES = 240

_timers: dict[str, dict] = {}     # host → {"ends": epoch seconds, "minutes": n, "task": Task}


async def _source() -> str:
    return ET.fromstring(await bose.now_playing()).get("source", "")


async def _volume() -> int:
    return int(ET.fromstring(await bose.volume()).findtext("actualvolume", "0") or 0)


async def _run(h: str, ends: float) -> None:
    with bose.use_device(h):
        try:
            await asyncio.sleep(max(0.0, ends - FADE - time.time()))
            if await _source() == "STANDBY":
                log.info("Sleep timer %s: already in standby", h)
                return
            start = await _volume()
            for i in range(1, FADE_STEPS + 1):
                await asyncio.sleep(FADE / FADE_STEPS)
                await bose.set_volume(round(start * (1 - i / FADE_STEPS)))
            if await _source() != "STANDBY":
                await bose.key("POWER")
            log.info("Sleep timer %s: standby", h)
            await asyncio.sleep(4)
            await bose.set_volume(start)
            # Setting the volume shouldn't wake the speaker; if it did, put it back.
            await asyncio.sleep(2)
            if await _source() != "STANDBY":
                log.warning("Sleep timer %s: speaker woke up on the volume reset, standby again", h)
                await bose.key("POWER")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("Sleep timer %s failed: %s", h, exc)
        finally:
            t = _timers.get(h)
            if t and t["task"] is asyncio.current_task():
                del _timers[h]


def cancel(h: str) -> None:
    t = _timers.pop(h, None)
    if t:
        t["task"].cancel()


def start(h: str, minutes: int) -> dict:
    cancel(h)
    minutes = max(1, min(int(minutes), MAX_MINUTES))
    ends = time.time() + minutes * 60
    _timers[h] = {"ends": ends, "minutes": minutes, "task": asyncio.create_task(_run(h, ends))}
    log.info("Sleep timer %s: %d min", h, minutes)
    return state(h)


def state(h: str) -> dict | None:
    t = _timers.get(h)
    if not t:
        return None
    return {"minutes": t["minutes"], "remaining": max(0, round(t["ends"] - time.time()))}


async def stop_all() -> None:
    tasks = [t["task"] for t in _timers.values()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _timers.clear()
