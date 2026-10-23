# Architecture

## Network

```
 Phone / tablet / PC                         Internet
        │  https://touchingsounds.example.com
        ▼
 Cloudflare ──tunnel──► cloudflared (container on the Office Server)
                                │
                                ▼
 Office Server 192.168.100.51 ── touchingsounds container :8000
                                │            ▲
                    WireGuard   │            │  speaker calls back to us
                 "TouchingSounds-Office"     │  (marge, BMX, stream relay)
                                ▼            │
 Home network ─────────── SoundTouch 20  192.168.1.49
                          :8090 REST API   :8080 websocket   :17000 shell

 Office LAN ───────────── Wireless Link Adapter "OfficeSounds" 192.168.100.150
                          (same APIs; plays through the system it's plugged into)
```

LAN devices can also use the app directly at http://192.168.100.51:8000.

## Devices

| Device | Address | Firmware | Set up via | Sources |
|---|---|---|---|---|
| SoundTouch 20 "F&F Touching Sounds" (primary) | 192.168.1.49 (home, WireGuard) | 27.0.6 | root shell: `/mnt/nv/OverrideSdkPrivateCfg.xml` | snapshot of its own Sources.xml (keeps Spotify/Alexa) |
| Wireless Link Adapter "OfficeSounds" (`:fresh`) | 192.168.100.150 (office LAN) | 26.0.1 (no `remote_services`) | port-17000 console: `sys configuration` + `envswitch boseurls set` | AUX + internet radio (previous owner's accounts dropped) |

`data/devices.json` lists them (written by the speakers wizard, seeded from
`BOSE_DEVICES` on the first start); the first is the primary. Each API
request names its device in an `X-TS-Device` header (the app's picker); requests the
speakers make themselves (marge) are routed by account id / deviceID.

## Why a server is needed at all

At every boot the speaker asks two Bose cloud services how to behave:

| Service | Speaker setting | What it provides | Our stand-in |
|---|---|---|---|
| **marge** (account) | `margeServerUrl` | The account: which sources exist (Spotify, Alexa, internet radio…), presets, recents | `app/marge.py` under `/marge/streaming/…` |
| **BMX** (music services) | `bmxRegistryUrl` | The registry of streaming services and their endpoints | `/bmx/registry/…`, `/bmx/orion/…` in `app/main.py` |

Bose turned both off on 2026-05-06. Without marge the speaker never registers the
LOCAL_INTERNET_RADIO source, and every attempt to play a stream fails with error 1005.
Both URLs are redirected to our server on startup, per device: on the SoundTouch 20
through `/mnt/nv/OverrideSdkPrivateCfg.xml`, written over the root shell
(`bose.setup_speaker`); on the adapter, whose firmware has no root shell, through the
console commands `sys configuration` + `envswitch boseurls set` (`bose.setup_via_console`).
Setup runs in the background, so the server already answers when a rebooted speaker asks
for its account.

## Playing a station, end to end

1. The app calls `POST /api/play {name, url, favicon}`.
2. `bose.play_stream` sends `/select` to the speaker with a LOCAL_INTERNET_RADIO
   content item whose location is `/station?data=<base64 {name, imageUrl, streamUrl}>`
   (the "orion" format, the same one AfterTouch and soundcork use).
3. The speaker resolves that relative location against the BMX registry's base URL and
   calls `GET /bmx/orion/station?data=…` on our server.
4. We answer with the stream URL. Plain `http://` streams go to the speaker unchanged.
   `https://` and HLS (`.m3u8`) streams are rewritten to our **relay**,
   `GET /relay?url=…`: ffmpeg fetches the stream and re-serves it as plain-HTTP MP3,
   because the firmware has expired TLS root certificates and no HLS support.
5. The speaker plays. The app polls `GET /api/status` every 3 s for what's on.
6. For the song: `/api/status` decodes the stream URL from the now-playing location and
   asks `songs.current()`. The first ask starts a watcher for that stream (station feed,
   or the stream itself read for ICY titles); later asks just read what it found. A
   watcher stops 45 s after the last ask, so nothing is read while the app is closed.

Preset buttons store the same kind of location, so a button press goes through steps 3-5
on its own. As a safety net, `bose.run_preset_listener` watches the speaker's websocket.
If a press doesn't end up on our station within 3 s, it selects the station itself.

## Components (`app/`)

| File | Role |
|---|---|
| `server.py` | Entry point. Runs uvicorn with a transport wrapper that restores the `ETag` header's capitalisation; uvicorn lowercases it, and the firmware ignores `etag`. |
| `main.py` | FastAPI app: lifespan (speaker setup, preset sync, websocket listener), speaker control API (`/api/status`, `/api/key`, `/api/volume`, `/api/source`, `/api/sources`), BMX stand-in, relay, diagnostics, Cloudflare guard, static files. |
| `library.py` | The app's library: `/api/search` (radio-browser.info), `/api/favorites`, `/api/history`, `/api/presets`, `/api/play`, `/api/art` (logo proxy), `/api/version`. |
| `songs.py` | The song now playing on an internet-radio stream: station feeds (Power group, Radyo Voyage) or ICY `StreamTitle`, plus a cover search (Deezer, iTunes). Watchers run only while the app polls. |
| `sleep.py` | Sleep timer per device: fade out over the last minute, standby, volume back. In memory. |
| `alarm.py` | Alarms per device: a 15 s clock rings due alarms (quiet start, station, volume rise over 90 s, preset backups) and records the outcome. Stored in `data/alarms.json`; times in the zone of the phone that set them. |
| `marge.py` | marge stand-in, per device: account, sources, presets, recents, provider settings, the "no software update" reply. Takes the Sources.xml snapshot. |
| `setup.py` | The speakers wizard's API (`/api/setup/*`): discovery (port-8090 sweep), the speaker's Wi-Fi scan and join, the takeover job (console redirect + restart), finish (name, presets, test sound), remove. |
| `bose.py` | Device registry (`data/devices.json`, current device per request/task) and speaker access: REST (8090), console (17000), root shell (23), websocket (8080); setup, `/select`, `/storePreset`, orion locations. |
| `version.py` | Product version. |
| `static/` | The web app: one `index.html` (no build step), `sw.js`, manifest, icons. |

## Data (`data/`, a bind mount, gitignored)

The primary device (SoundTouch 20) uses the files directly in `data/`; every other
device has the same files in `data/devices/<ip>/`. Favorites and history are shared.

| File | Contents |
|---|---|
| `Sources.xml` | Snapshot of the speaker's original source list, **including the Spotify and Alexa logins**. Taken once, before marge is redirected; it's the only copy. Never delete. |
| `presets.json` | Presets saved from the app, or long-pressed on the speaker. Overrides the `PRESET_N_URL` defaults in `.env`. |
| `favorites.json` | Favorite stations. |
| `history.json` | Last 30 stations played from the app. |
| `alarms.json` | All alarms (every device; each names its device). Next ring time and last outcome included. |
| `recents.json` | Recents as reported by the speaker to marge. |
| `devices.json` | The speakers: address and setup route (`fresh`). The first is the primary. |
| `devices/<ip>/original_urls.json` | A wizard-added speaker's cloud URLs before the redirect, for *Remove* → put back. |
| `identities.json` | Each device's deviceID, name and account, to route marge requests after a restart. |

`ProdRelease.bat` copies the whole folder to `DataBackup\data_<timestamp>` before each
release. The server also backs it up every night as an encrypted 7z (see
[DEPLOYMENT.md](DEPLOYMENT.md#at-a-glance)).

## Internet exposure

Requests that come through Cloudflare carry a `Cf-Connecting-Ip` header. For those, the
app only serves the web app and its API. `/marge`, `/bmx`, `/relay`, `/api/diagnose*`,
`/api/test*` and the API docs return 404. Who may open the app at all is decided in
front of it, by Cloudflare Access (see [DEPLOYMENT.md](DEPLOYMENT.md#cloudflare-tunnel)).
