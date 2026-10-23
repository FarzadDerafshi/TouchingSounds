"""
library.py — the web app's radio library: station search, favorites,
listening history, preset editing, version info and a station-logo proxy.

Search uses the free, community-run radio-browser.info directory.  Favorites
and history live in TS_DATA_DIR next to marge's state, so every device that
opens the app sees the same lists.  Presets are written both to the speaker
(/storePreset) and to marge's data/presets.json, so the buttons, the UI and
the account marge serves at boot all agree.
"""

import hashlib
import json
import logging
import os
import time
import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

import app.bose as bose
import app.marge as marge
from app.bose import BoseError
from app.version import VERSION

log = logging.getLogger("library")
router = APIRouter()

_DATA_DIR       = os.getenv("TS_DATA_DIR", "data")
_FAVORITES_FILE = os.path.join(_DATA_DIR, "favorites.json")
_HISTORY_FILE   = os.path.join(_DATA_DIR, "history.json")
_HISTORY_MAX    = 30

# Mirrors of the same directory; tried in order until one answers.
_RADIO_BROWSER = (
    "https://de1.api.radio-browser.info",
    "https://de2.api.radio-browser.info",
    "https://fi1.api.radio-browser.info",
)
_USER_AGENT = f"TouchingSounds/{VERSION}"

_ART_MAX_BYTES = 2 * 1024 * 1024


class Station(BaseModel):
    """A playable station as the UI knows it (search result, favorite, history)."""
    id: str = ""
    name: str
    url: str
    favicon: str = ""
    country: str = ""
    tags: str = ""
    codec: str = ""
    bitrate: int = 0
    homepage: str = ""


class PresetBody(BaseModel):
    name: str
    url: str
    favicon: str = ""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_list(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write_list(path: str, items: list[dict]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def saved_stations() -> list[dict]:
    """Favorites, then recently played: stations known to play."""
    return [s for s in _read_list(_FAVORITES_FILE) + _read_list(_HISTORY_FILE) if s.get("url")]


def station_id(st: Station) -> str:
    """radio-browser's stationuuid when known, else a stable hash of the URL."""
    return st.id or "url-" + hashlib.sha1(st.url.encode()).hexdigest()[:12]


def _station_dict(st: Station) -> dict:
    d = st.model_dump()
    d["id"] = station_id(st)
    return d


def _from_radio_browser(s: dict) -> dict:
    tags = ", ".join(t for t in (s.get("tags") or "").split(",")[:3] if t)
    return {
        "id": s.get("stationuuid", ""),
        "name": (s.get("name") or "").strip(),
        "url": s.get("url_resolved") or s.get("url") or "",
        "favicon": s.get("favicon") or "",
        "country": s.get("country") or s.get("countrycode") or "",
        "tags": tags,
        "codec": s.get("codec") or "",
        "bitrate": s.get("bitrate") or 0,
        "homepage": s.get("homepage") or "",
    }


def _preset_view(slot: str, p: dict) -> dict:
    station = bose.decode_orion_location(p.get("location", "")) or {}
    return {
        "id": slot,
        "name": p.get("name", ""),
        "source": p.get("source", ""),
        "url": station.get("streamUrl", ""),
        "favicon": station.get("imageUrl", "") or p.get("art", ""),
    }


# ── Version ──────────────────────────────────────────────────────────────────

@router.get("/api/version")
async def version():
    return {"version": VERSION, "commit": os.getenv("GIT_COMMIT", "dev")}


# ── Search ───────────────────────────────────────────────────────────────────

@router.get("/api/search")
async def search(q: str, limit: int = 40):
    """Search radio-browser.info by station name, most-listened first."""
    q = q.strip()
    if len(q) < 2:
        return []
    params = {
        "name": q, "hidebroken": "true", "order": "clickcount",
        "reverse": "true", "limit": str(max(1, min(limit, 100))),
    }
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=8, headers={"User-Agent": _USER_AGENT}) as c:
        for base in _RADIO_BROWSER:
            try:
                r = await c.get(f"{base}/json/stations/search", params=params)
                r.raise_for_status()
                # The directory lists many stations more than once; keep the
                # most-listened entry per stream URL.
                seen, results = set(), []
                for s in map(_from_radio_browser, r.json()):
                    if s["url"] and s["name"] and s["url"] not in seen:
                        seen.add(s["url"])
                        results.append(s)
                return results
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                log.warning("radio-browser %s failed: %s", base, exc)
    raise HTTPException(502, detail=f"Station search is unavailable right now: {last_exc}")


# ── Playback ─────────────────────────────────────────────────────────────────

@router.post("/api/play")
async def play(st: Station):
    """Play a station on the speaker and record it in the history."""
    try:
        await bose.play_stream(st.url, st.name, st.favicon)
    except (BoseError, Exception) as exc:
        log.error("play failed: %s", exc)
        raise HTTPException(502, detail=str(exc))
    entry = _station_dict(st)
    entry["playedAt"] = int(time.time())
    history = [h for h in _read_list(_HISTORY_FILE) if h.get("id") != entry["id"]]
    _write_list(_HISTORY_FILE, [entry] + history[: _HISTORY_MAX - 1])
    return {"ok": True}


@router.get("/api/history")
async def history():
    return _read_list(_HISTORY_FILE)


@router.delete("/api/history")
async def clear_history():
    _write_list(_HISTORY_FILE, [])
    return {"ok": True}


# ── Favorites ────────────────────────────────────────────────────────────────

@router.get("/api/favorites")
async def favorites():
    return _read_list(_FAVORITES_FILE)


@router.post("/api/favorites")
async def add_favorite(st: Station):
    entry = _station_dict(st)
    entry["addedAt"] = int(time.time())
    favs = [f for f in _read_list(_FAVORITES_FILE) if f.get("id") != entry["id"]]
    _write_list(_FAVORITES_FILE, favs + [entry])
    return entry


@router.delete("/api/favorites/{fav_id}")
async def remove_favorite(fav_id: str):
    favs = _read_list(_FAVORITES_FILE)
    _write_list(_FAVORITES_FILE, [f for f in favs if f.get("id") != fav_id])
    return {"ok": True}


# ── Presets ──────────────────────────────────────────────────────────────────

@router.get("/api/presets")
async def get_presets():
    """
    The speaker's six preset slots.  Station details (stream URL, logo) come
    from the slot's orion location; slots holding something else, e.g. a
    Spotify playlist stored on the speaker itself, show name and source only.
    """
    try:
        xml = await bose.presets()
    except (BoseError, Exception) as exc:
        raise HTTPException(502, detail=str(exc))
    items = []
    for preset in ET.fromstring(xml).findall("preset"):
        ci = preset.find("ContentItem")
        if ci is None:
            continue
        items.append(_preset_view(preset.get("id", ""), {
            "name": ci.findtext("itemName", ""),
            "source": ci.get("source", ""),
            "location": ci.get("location", ""),
            "art": ci.findtext("containerArt", "") or "",
        }))
    return items


@router.put("/api/presets/{slot}")
async def put_preset(slot: int, body: PresetBody):
    """Save a station to preset button 1-6, on the speaker and in presets.json."""
    if not 1 <= slot <= 6:
        raise HTTPException(400, detail="Preset slot must be 1-6")
    try:
        bose.validate_playable_url(body.url)
        location = bose.orion_location(body.url, body.name, body.favicon)
        await bose.store_preset(slot, location, body.name)
    except (BoseError, Exception) as exc:
        log.error("preset %d save failed: %s", slot, exc)
        raise HTTPException(502, detail=str(exc))
    marge.save_preset(bose.host(), str(slot), body.name, location, body.favicon)
    return {"ok": True}


async def sync_presets_for(h: str) -> None:
    """Push device `h`'s preset set (saved presets, plus env defaults on the primary)."""
    with bose.use_device(h):
        await bose.sync_presets({int(k): v for k, v in marge.presets(h).items() if k.isdigit()})


# ── Devices ──────────────────────────────────────────────────────────────────

@router.get("/api/devices")
async def devices():
    """
    The configured speakers, for the app's device picker.  `id` is what the
    app sends back in the X-TS-Device header.
    """
    await bose.refresh_identities()
    return [
        {
            "id": d.host,
            "name": bose.identity(d.host).get("name") or d.host,
            "type": bose.identity(d.host).get("type", ""),
            "online": bose.is_online(d.host),
            "primary": d.primary,
        }
        for d in bose.DEVICES
    ]


# ── Station logos ────────────────────────────────────────────────────────────

@router.get("/api/art")
async def art(url: str):
    """
    Fetch a station logo for the browser.  Many are plain http, which an
    https page (the Cloudflare hostname) isn't allowed to load directly.
    Only images, at most 2 MB; cached by the browser for a day.
    """
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, detail="url must be http(s)")
    try:
        async with httpx.AsyncClient(timeout=4, follow_redirects=True,
                                     headers={"User-Agent": _USER_AGENT}) as c:
            r = await c.get(url)
            r.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(404, detail=f"Logo unavailable: {exc}")
    ctype = r.headers.get("content-type", "").split(";")[0].strip()
    if not ctype.startswith("image/") or len(r.content) > _ART_MAX_BYTES:
        raise HTTPException(404, detail="Not an image")
    # The CSP and nosniff keep an SVG logo from running script on our origin.
    return Response(r.content, media_type=ctype, headers={
        "Cache-Control": "public, max-age=86400",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        "X-Content-Type-Options": "nosniff",
    })
