# Decisions

Why things are built the way they are, not what they do (that's
[ARCHITECTURE.md](ARCHITECTURE.md)). Newest first.

---

## 2026-10-22 — 1.5.0: the speakers wizard

**Ask:** connect a speaker to Wi-Fi and to the server in a guided way a non-technical
person can follow. Setup mode differs per model and is hard even for technical users.

**Decision:** an in-app wizard that tries the easiest way in first (already on Wi-Fi →
cable → setup mode), then the same automatic takeover for all. Speakers move from
`BOSE_DEVICES` to `data/devices.json`, so adding one needs no release. Proposal and
mock-up: the "1.5 proposal: guided speaker setup" doc (2026-10-22).

**Why these parts:**
- **Port-8090 sweep, not mDNS/SSDP:** multicast doesn't cross Docker's bridge network
  or the WireGuard tunnel to the home network; a sweep of a /24 takes about 2–4 s.
- **The speaker scans for Wi-Fi, not the phone:** on a cable the server can ask the
  speaker for the networks it hears, so the user only picks one and types the password.
- **Console redirect for new speakers:** it needs no root shell and works on every
  firmware we've met (1.1.0). The root-shell route stays for a speaker whose own
  Spotify/Alexa logins are worth keeping.
- **Naming and presets while it restarts:** the restart takes about a minute; the user
  fills that time instead of waiting.
- **The phone hand-over is a top-level form post:** in setup mode the speaker sits at
  `http://192.0.2.1:8090`, which an https page can't `fetch`. A `text/plain` form can
  reach it, with the XML split across the field's name and value. Kept as *Try it*
  with Bose's app as the fallback until it's tried on an iPhone.
- **Button drawings per model as data:** each model's keys, as laid out on the device,
  are a short list of boxes, so a correction is a one-line change.

---

## 2026-10-21 — 1.3.0: songs from station feeds and ICY, read only while the app is open

**Ask:** show the song playing on the radio, with its cover.

**Findings:** the speaker reports only the station. Of the user's stations, only some
Icecast ones put a title in the stream (ICY); Radyo Voyage, Europa Plus, Детское радио
(101.ru) send empty titles, and the HLS ones (Power FM, Number One) none at all.
Power FM and Radyo Voyage do have now-playing feeds behind their own web players.

**Decision:** per stream, a known station feed first, else ICY. Feeds are undocumented
endpoints of the stations' sites, so they may change; a failing feed just means no
song. Watchers run only while someone has the app open (started by `/api/status`,
stopped 45 s after the last poll), so an ICY read doesn't double the server's radio
traffic all night. Covers from Deezer (better hit rate than iTunes in a quick test),
iTunes as fallback, with a word-overlap check on artist and title. Audio fingerprinting
(Shazam-style) was not considered worth it.

---

## 2026-10-22 — 1.4.0: sharing a song via the share sheet, plus a WhatsApp button

**Ask:** tap the cover to enlarge it; share the picture and song via WhatsApp or the
phone's share feature.

**Decision:** the Web Share API with the cover as a file is the one way to send the
picture itself (WhatsApp's `wa.me` link takes text only), so *Share* is the main button.
The image is fetched (through `/api/art`) when the viewer opens, because iOS only
allows the share sheet straight from a tap, not after a download. A separate WhatsApp
button covers the common case in one tap, text only. The text carries a YouTube search
link (works for anyone, no account) rather than the app's address, which is private.
Search links rather than exact track links: the stations' feeds give no track IDs.

---

## 2026-10-21 — 1.3.0: alarms on the server, stored, in the phone's time zone

**Ask:** turn the speaker on at a set time. Added to 1.3.0 at the user's request.

**Findings:** the SoundTouch 20 has no alarm of its own we can use; selecting a source
or station wakes it from standby.

**Decision:** the server rings alarms, like the sleep timer, but stores them in
`data/alarms.json` (a release mustn't lose tomorrow's alarm). Each alarm keeps the
IANA time zone of the phone that set it, since the container runs on UTC and setting
`TZ` in docker-compose would tie it to one place. A 15 s clock rings anything due; up
to 5 min late still rings (a restart around the time), later is recorded as missed
rather than waking someone at 07:20. Start at volume 5 and rise over 90 s so the
previous volume never blasts. A station that hasn't started in 25 s falls back to the
device's presets (two at most). Outcomes are kept per alarm and the player flags a
failed one for 12 h, so a silent morning has an explanation.

---

## 2026-10-21 — 1.3.0: sleep timer on the server

**Decision:** the timer runs in the server, not the page, so it fires with the phone
locked. At the end: fade over 60 s, POWER (only if not in standby already), then the
volume back to where it was, so the next start isn't silent; if that wakes the speaker,
POWER again. Kept in memory only; a restart (release) cancels it, which is rare and
visible (the button stops showing the time left).

---

## 2026-10-19 — Office Bluetooth speaker: hardware, not software

**Ask:** play OfficeSounds' radio on a Bluetooth speaker in the office.

**Decision:** not done in software. SoundTouch devices are Bluetooth receivers only (no
transmit), and no setting or firmware command changes that. The way to do it is the
adapter's wired output into the speaker's AUX input, or into a Bluetooth transmitter.
Having the Office Server play to the speaker itself was set aside: it needs Bluetooth on
the server plus a separate player, and it bypasses the adapter. The user chose to leave
it for now.

---

## 2026-10-19 — 1.1.0: one app for several speakers, picker at the top

**Ask:** control the newly added Wireless Link Adapter too, from the same app, choosing
the device at the top (rather than a second copy of the app on another port).

**Decision:** a device registry (`BOSE_DEVICES`), a per-request "current device"
(context variable, set from the app's `X-TS-Device` header), and marge serving each
device its own account by account id / deviceID. Presets and the player are per device;
favorites, history and search are shared, since they're about stations, not speakers.

**Why not a second container:** two apps to open, two sets of favorites, two releases.
The context variable kept most of the speaker code unchanged: functions still say "the
speaker", and the request decides which one.

**Data layout:** the primary device keeps `data/` exactly as before (its Sources.xml
snapshot is the only copy of the Spotify login, so nothing gets moved); other devices
get `data/devices/<ip>/`. Keyed by IP because the IPs are reserved and listed in
`docker-compose.yml` anyway.

---

## 2026-10-19 — The adapter: console setup and a fresh source list

**Problem:** the Wireless Link Adapter (FW 26.0.1) has no `remote_services`, so our
root-shell method can't write `OverrideSdkPrivateCfg.xml` or snapshot Sources.xml. It
also still carries the previous owner's Bose account and linked Spotify/Amazon/Pandora
logins.

**Decision:** for devices marked `:fresh`, set the URLs through the port-17000 console
(`sys configuration` ×4, then `envswitch boseurls set` to persist, then `sys reboot`),
the method AfterTouch documents and field-tested on this adapter model. marge then
serves such a device AUX + internet radio only.

**Why:** it needs no root access and no third-party software on the device. Replacing
the source list is the point for a second-hand unit: the previous owner's accounts
aren't ours to keep. Factory-resetting instead was rejected: it would also unpair the
device's Bose account, and pairing a new one needs Bose's dead cloud. The adapter keeps
the previous owner's account *id*, which is just a number our marge answers
for.

---

## 2026-10-19 — 1.0.0: web app (PWA) rather than native apps

**Ask:** "build the application on other platforms", responsive for mobile, tablet and web.

**Decision:** one responsive web app, installable as an app (PWA) on Android, iOS,
Windows and macOS once it's served over https (the Cloudflare hostname). No native apps.

**Why:** the app is a remote control for one speaker; it needs no offline data, no
background audio and no device APIs that a browser lacks. A PWA gives a home-screen icon,
full-screen window and the same code everywhere, with deployment being the existing
`ProdRelease.bat`. Native apps would mean three codebases and app-store accounts for one
household. Revisit if a feature needs something browsers can't do (e.g. lock-screen media
controls for the speaker).

**Also:** still a single `index.html` with no framework or build step, like 0.5.x. It
keeps the Docker image and the release trivial; the UI is small enough that a framework
would add more than it saves.

---

## 2026-10-19 — Favorites and history on the server, not in the browser

**Decision:** `data/favorites.json` and `data/history.json`, served by `/api/favorites`
and `/api/history`.

**Why:** browser storage is per device and per browser. Favorites saved on a phone should
show up on the tablet and on the installed desktop app. The household is one user, so
there's no per-person split.

---

## 2026-10-19 — Station search via radio-browser.info, proxied by our server

**Decision:** `GET /api/search` calls radio-browser.info's public API (with mirror
fallback) and returns trimmed results.

**Why:** radio-browser is free, community-run, has no API key and lists 50,000+ stations
with direct stream URLs and logos. TuneIn's API is closed and its stream URLs are
short-lived. Going through our server keeps one place to switch mirrors, removes
duplicates, and sends a proper User-Agent as their API asks.

---

## 2026-10-19 — The internet-facing app hides speaker and diagnostic endpoints

**Problem:** the Cloudflare tunnel makes the app reachable from the internet. The same
app also serves the endpoints the speaker uses (marge, BMX, the ffmpeg relay) and
diagnostics that run shell commands on the speaker.

**Decision:** a middleware returns 404 for `/marge`, `/bmx`, `/relay`, `/api/diagnose*`,
`/api/test*` and the API docs when the request carries Cloudflare's `Cf-Connecting-Ip`
header. Authentication itself is done by **Cloudflare Access** in front of the hostname,
not by the app.

**Why:** the speaker only ever talks to the LAN address, so nothing legitimate needs
those paths from outside, and the relay would otherwise be an open ffmpeg proxy. Cloudflare
Access gives a proper login (email code, Google, …) for free, without building accounts,
passwords and sessions into a one-household app.

---

## 2026-10-19 — Presets: one source of truth in `data/presets.json`

**Decision:** presets saved from the app are written to the speaker (`/storePreset`) and
to `data/presets.json`. The `PRESET_N_URL` values in `docker-compose.yml` are only
defaults for slots that were never saved.

**Why:** three places read presets: the startup sync, the marge account the speaker
fetches at boot, and the button-press fallback. Before 1.0.0 they read the environment
only, so a preset changed in the app would have been undone by the next restart.

---

## 2026-10-19 — HTTPS/HLS stations through an ffmpeg relay

**Problem:** the firmware's TLS root certificates are expired, and it can't play HLS.
Some stations (e.g. Number One FM) only stream HLS over https.

**Decision:** `/relay?url=…` runs ffmpeg per listener and re-serves the stream as
plain-HTTP 128 kbps MP3. The station resolver hands the speaker the relay URL whenever
the stream is https or `.m3u8`; plain http streams stay direct.

**Why:** it makes every station playable without touching the speaker's certificate
store (read-only rootfs, and a firmware update would undo it). Cost: about 250 MB more
Docker image and some CPU per listener, which is fine for one speaker.

---

## 2026-10-18 — Our own marge stand-in instead of installing AfterTouch

**Problem:** with Bose's cloud gone, LOCAL_INTERNET_RADIO never registers (error 1005).
Two open-source projects (AfterTouch, STR) fix this.

**Decision:** emulate the needed marge endpoints in our own app (`app/marge.py`) and
redirect `margeServerUrl` via `/mnt/nv/OverrideSdkPrivateCfg.xml`, the same mechanism we
already used for `bmxRegistryUrl`.

**Why:** we already had the speaker access, the BMX stand-in, the web UI and the WireGuard
setup; the only missing piece was marge. Doing it ourselves kept everything in one
container we understand, with no third-party binary on the speaker. AfterTouch stays
documented as plan B in [AFTERTOUCH_INSTALL_PLAN.md](AFTERTOUCH_INSTALL_PLAN.md). The
Sources.xml snapshot gate (marge isn't redirected unless the speaker's original source
list was saved first) protects the Spotify login.
