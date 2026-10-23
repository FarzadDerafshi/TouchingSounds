"""
songs.py — what song a radio station is playing, with its cover.

The speaker only knows the station: for internet radio its now_playing
reports the station name as the track.  So the server finds the song itself,
from one of these, per stream:

  * the station's own now-playing feed, for stations known to have one
    (the Power group, Radyo Voyage), whose streams carry no song data;
  * ICY metadata ("StreamTitle") in the stream, which most Icecast and
    Shoutcast stations send: we open the stream with `Icy-MetaData: 1` and
    read the title blocks between the audio.

Covers come from the feed when it has one, else from a Deezer search (iTunes
as fallback) for "artist title".

A watcher runs only while someone looks: /api/status asks for the current
song, which starts the watcher for that stream, and a watcher nobody asked
about for IDLE_STOP seconds stops.  So a reading stream costs bandwidth only
while the app is open, not all night while the radio plays.
"""

import asyncio
import logging
import re
import time
import unicodedata

import httpx

from app.version import VERSION

log = logging.getLogger("songs")

IDLE_STOP = 45          # seconds without a request before a watcher stops
FEED_POLL = 30          # default seconds between polls of a station feed
_UA = f"TouchingSounds/{VERSION}"


# ── Song titles ──────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\x00", "")).strip(" -–|")


def split_title(text: str) -> tuple[str, str]:
    """'Artist - Title' → (artist, title); no separator → ('', text)."""
    text = _clean(text)
    for sep in (" - ", " – ", " — ", " | "):
        if sep in text:
            a, t = text.split(sep, 1)
            return _clean(a), _clean(t)
    return "", text


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.casefold())
    return re.sub(r"[^\w]+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


def is_real_song(artist: str, title: str, station: str) -> bool:
    """False for empty titles and for the station announcing itself."""
    if not title or len(title) < 2:
        return False
    st = _norm(station)
    if st and (_norm(title) == st or _norm(f"{artist} {title}") == st or _norm(artist) == st):
        return False
    return True


# ── Covers ───────────────────────────────────────────────────────────────────

_cover_cache: dict[str, str] = {}      # "artist|title" → cover URL ("" = none found)
_COVER_CACHE_MAX = 500


def _words(s: str) -> set[str]:
    return {w for w in _norm(s).split() if len(w) > 1}


def _matches(want_artist: str, want_title: str, artist: str, title: str) -> bool:
    """Loose check that a search hit is the song asked for, not a namesake."""
    wt, t = _words(want_title), _words(title)
    if not wt or not (wt & t):
        return False
    wa = _words(want_artist)
    return not wa or bool(wa & _words(artist))


async def find_cover(artist: str, title: str) -> str:
    key = f"{_norm(artist)}|{_norm(title)}"
    if key in _cover_cache:
        return _cover_cache[key]
    url = ""
    term = f"{artist} {title}".strip()
    async with httpx.AsyncClient(timeout=6, headers={"User-Agent": _UA}) as c:
        try:
            r = await c.get("https://api.deezer.com/search", params={"q": term, "limit": 5})
            for hit in r.json().get("data") or []:
                if _matches(artist, title, (hit.get("artist") or {}).get("name", ""), hit.get("title", "")):
                    url = (hit.get("album") or {}).get("cover_big") or ""
                    if url:
                        break
        except (httpx.HTTPError, ValueError) as exc:
            log.info("Deezer lookup failed: %s", exc)
        if not url:
            try:
                r = await c.get("https://itunes.apple.com/search",
                                params={"term": term, "media": "music", "entity": "song", "limit": 5})
                for hit in r.json().get("results") or []:
                    if _matches(artist, title, hit.get("artistName", ""), hit.get("trackName", "")):
                        url = (hit.get("artworkUrl100") or "").replace("100x100bb", "600x600bb")
                        if url:
                            break
            except (httpx.HTTPError, ValueError) as exc:
                log.info("iTunes lookup failed: %s", exc)
    if len(_cover_cache) >= _COVER_CACHE_MAX:
        _cover_cache.pop(next(iter(_cover_cache)))
    _cover_cache[key] = url
    return url


# ── Station feeds ────────────────────────────────────────────────────────────
#
# Each returns (artist, title, cover, seconds until the next poll), or None
# when the feed has nothing.

_POWER = re.compile(r"^https?://(?:listen|live)\.powerapp\.com\.tr/([a-z0-9]+)/", re.I)


async def _power_feed(c: httpx.AsyncClient, slug: str):
    r = await c.post(f"https://api.powergroup.com.tr/v3/Route/get?url=/radios/{slug}",
                     data={"client": "web", "lang": "tr", "version": "23",
                           "devicePlatform": "1", "deviceType": "0"})
    data = (r.json() or {}).get("data") or {}
    now = (data.get("timeline") or [None])[0]
    if not now:
        return None
    img = now.get("image") or {}
    cover = f"{img['prefix']}400x400{img['suffix']}" if img.get("prefix") and img.get("suffix") else ""
    left = now.get("remainingSeconds")
    wait = min(max(int(left) + 3, 8), 90) if isinstance(left, (int, float)) and left > 0 else FEED_POLL
    return _clean(now.get("artistTitle", "")), _clean(now.get("songTitle", "")), cover, wait


async def _voyage_feed(c: httpx.AsyncClient, _):
    r = await c.get("https://www.radyovoyage.com/umbraco/surface/Player/VoyagePlayerSong",
                    headers={"Accept": "application/json"})
    artist, title = split_title((r.json() or {}).get("NowPlayingArtist") or "")
    return artist, title, "", FEED_POLL


def feed_for(url: str):
    """The station feed for a stream URL, as (function, argument), or None."""
    m = _POWER.match(url)
    if m:
        return _power_feed, m.group(1).lower()
    if re.match(r"^https?://(voyagewmp\.radyotvonline\.com|live\.dgn\.net\.tr/stream_origin/voyage)/", url, re.I):
        return _voyage_feed, None
    return None


# ── Watchers ─────────────────────────────────────────────────────────────────

class _Watcher:
    def __init__(self, url: str, station: str):
        self.url = url
        self.station = station
        self.song: dict | None = None
        self.wanted = time.monotonic()
        self.task = asyncio.create_task(self._run())

    def idle(self) -> bool:
        return time.monotonic() - self.wanted > IDLE_STOP

    async def _set(self, artist: str, title: str, cover: str, via: str) -> None:
        if not is_real_song(artist, title, self.station):
            self.song = None
            return
        if self.song and (self.song["artist"], self.song["title"]) == (artist, title):
            return
        self.song = {"artist": artist, "title": title, "cover": cover, "via": via}
        log.info("%s: %s - %s", self.station, artist, title)
        if not cover:
            found = await find_cover(artist, title)
            if self.song and self.song["title"] == title:
                self.song = {**self.song, "cover": found}

    async def _run(self) -> None:
        try:
            feed = feed_for(self.url)
            if feed:
                await self._run_feed(*feed)
            elif not re.search(r"\.m3u8(\?|$)", self.url, re.I):
                await self._run_icy()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Song watcher for %s stopped: %s", self.station, exc)

    async def _run_feed(self, fn, arg) -> None:
        fails = 0
        async with httpx.AsyncClient(timeout=8, headers={"User-Agent": _UA}) as c:
            while not self.idle():
                wait = FEED_POLL
                try:
                    res = await fn(c, arg)
                    fails = 0
                    if res:
                        artist, title, cover, wait = res
                        await self._set(artist, title, cover, "feed")
                    else:
                        self.song = None
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    fails += 1
                    wait = min(FEED_POLL * fails, 300)
                    log.info("Feed for %s failed (%d): %s", self.station, fails, exc)
                await self._sleep_while_wanted(wait)

    async def _run_icy(self) -> None:
        backoff = 5
        while not self.idle():
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=30), follow_redirects=True,
                                             headers={"User-Agent": _UA, "Icy-MetaData": "1"}) as c:
                    async with c.stream("GET", self.url) as r:
                        r.raise_for_status()
                        metaint = int(r.headers.get("icy-metaint") or 0)
                        if not metaint:
                            log.info("%s sends no ICY metadata", self.station)
                            return
                        await self._read_icy(r, metaint)
                        backoff = 5
            except (httpx.HTTPError, ValueError) as exc:
                log.info("ICY read for %s failed: %s", self.station, exc)
            if not self.idle():
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    async def _read_icy(self, r: httpx.Response, metaint: int) -> None:
        buf = bytearray()
        async for chunk in r.aiter_raw():
            if self.idle():
                return
            buf += chunk
            while len(buf) > metaint:
                size = buf[metaint] * 16
                if len(buf) < metaint + 1 + size:
                    break
                meta = bytes(buf[metaint + 1: metaint + 1 + size])
                del buf[: metaint + 1 + size]
                if size:
                    m = re.search(rb"StreamTitle='(.*?)';", meta, re.S)
                    if m:
                        raw = m.group(1)
                        try:
                            text = raw.decode("utf-8")
                        except UnicodeDecodeError:
                            text = raw.decode("latin-1")
                        await self._set(*split_title(text), "", "stream")

    async def _sleep_while_wanted(self, secs: float) -> None:
        end = time.monotonic() + secs
        while time.monotonic() < end and not self.idle():
            await asyncio.sleep(min(2, end - time.monotonic()))


_watchers: dict[str, _Watcher] = {}


def current(url: str, station: str) -> dict | None:
    """
    The song now playing on stream `url`, if known.  Starts a watcher for the
    stream on first use; watchers nobody asks about stop by themselves.
    """
    # A finished watcher stays while it's still asked about, so a stream
    # without song data isn't reopened on every poll.
    for u in [u for u, w in _watchers.items() if w.task.done() and w.idle()]:
        del _watchers[u]
    if not url.startswith(("http://", "https://")):
        return None
    w = _watchers.get(url)
    if w is None:
        w = _watchers[url] = _Watcher(url, station)
    w.wanted = time.monotonic()
    w.station = station or w.station
    return w.song


async def stop_all() -> None:
    tasks = [w.task for w in _watchers.values()]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _watchers.clear()
