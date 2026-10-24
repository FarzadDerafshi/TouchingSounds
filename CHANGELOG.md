# Changelog

All notable changes to TouchingSounds are documented here.

---

## [1.5.3] — 2026-10-24  **New app icon and logo: Pulse Bars**

### Changed
- New app icon: five rounded peach bars, centred and balanced, on charcoal (the old icon's
  four bars sat high and off-centre). Regenerated icon-512, icon-192, apple-touch-icon and
  the 16/32 favicons. The user picked it from six concepts.
- The top bar shows the new logo: the Pulse Bars mark plus the wordmark "Touching" (light)
  "Sounds" (bold, accent colour), in place of the plain dot.
- Service-worker cache bumped to `ts-shell-v2` so installed apps pick up the new icons.
  iPhone home-screen icons may only change after the app is removed and added again.

---

## [1.5.2] — 2026-10-23  **Search: the bottom bars no longer float mid-screen on iPhone**

Live 2026-10-23 (`9e73f9a`). The user confirmed the fix on the iPhone (installed app).

### Fixed
- On the iPhone, scrolling search results with the keyboard open left the mini-player
  and tab bar floating in the middle of the screen, drifting as the list moved
  (reported with a screen recording). Cause: results appear while typing, so the
  keyboard stayed up; iOS doesn't shrink the page for the keyboard but slides the visible
  part over it, and the two bars are fixed to the bottom of the full page, not of what's
  visible. Now:
  - the keyboard closes when the results are dragged or tapped, and when Search is
    pressed (as in native apps);
  - while any text field has focus (keyboard up), the mini-player and tab bar are hidden
    (`body.typing`), and they come back when it closes.

### Tested
Headless Edge at 390×844 against a fake speaker, live search "Russian" (37 results):
field focused → both bars hidden; drag on a result → field loses focus, bars back;
drag on the field itself → stays focused; Search key → unfocused, bars back; focus
moved to a result's button → bars back; other screens unaffected. No page errors. The
iOS keyboard itself can't be reproduced here: to be confirmed on the iPhone.

---

## [1.5.1] — 2026-10-23  **Public release: settings in `.env`, MIT licence**

Live 2026-10-23 (`568e80a`), released from Telegram `/release`. The first run wrote
`.env` from the running container as planned.

### Changed
- The repo is public under the MIT licence, with a root `README.md` (what it is,
  what you need, quick start, what it changes on the speaker, security) and `LICENSE`.
- **Settings moved to `.env`** (git-ignored; template `.env.example`):
  `BOSE_DEVICES`, `BOSE_SERVER_URL`, `TS_SCAN_SUBNETS`, `BOSE_PORT` and the
  `PRESET_N_URL`/`_NAME` defaults. `docker-compose.yml` holds no addresses any more and
  refuses to start without `BOSE_DEVICES` and `BOSE_SERVER_URL`.
- The app no longer falls back to the author's addresses: without a speaker or
  `BOSE_SERVER_URL` it stops at startup with a message naming the missing setting.
  An empty `PRESET_N_NAME` now means "Preset N", as an unset one did.
- `ProdRelease.bat`: if `.env` is missing, it writes it from the running container's
  settings (`docker inspect`) before rebuilding, so the first release after this change
  needs nothing done on the server. It stops with instructions if it can't.
- Docs: personal identifiers (account and device IDs, public hostname) replaced; the
  WireGuard setup PDF and the design hand-off zip removed from the repo.

### Tested
`ProdRelease.bat`'s new step in real cmd with a stub `docker`: no `.env` + container →
`.env` written with the 9 settings, no BOM; `.env` present → left alone; no `.env` and
no container → stops with the instructions, exit 1. `docker compose config` with that
`.env` gives the same environment as before; without `.env` it refuses with the
message. App import: stops with the right message for each missing setting, starts
with both.

---

## [1.5.0] — 2026-10-22  **Add a speaker from the app**

Live 2026-10-22 (`d6f5831`, includes 1.4.1). Both speakers carried over into
`data/devices.json`. Not yet tried by the user on a real speaker.

### Added
- **Add or manage speakers** at the bottom of the speaker menu (the speaker button is
  always tappable now). A wizard tries the easiest way in first:
  - **Already on Wi-Fi:** the server finds it (a sweep of port 8090 on the subnets it
    can reach) and you tap **Add**.
  - **On a cable:** plug it into the router; the speaker itself lists the Wi-Fi
    networks it hears (`performWirelessSiteSurvey`), you pick one and type the
    password (`addWirelessProfile`), then unplug the cable. The app finds it again on
    Wi-Fi by its deviceID.
  - **No cable:** setup mode, with a drawing of that model's buttons laid out as on
    the device (SoundTouch 20: presets 1–3 over 4–6, volume + over − to their
    right), a 10-second countdown and the light to wait for. Models: SoundTouch 20, 30,
    10, the SoundTouch 300 soundbar (on its remote) and the Link Adapter. The phone then hands the
    Wi-Fi details to the speaker. Marked *Try it*: not yet tried on a real speaker.
  - **Enter its address** as a last resort.
- **Automatic takeover** after Wi-Fi: console redirect (the 1.1.0 route, no root
  needed), one restart, then the name you chose, presets copied from another speaker
  (or its own kept), and a quiet test sound: "Do you hear music?" *No* plays it again,
  louder, and says what the speaker reports.
- **Change Wi-Fi** for a speaker that's online: it remembers a further network and
  joins whichever it reaches first, e.g. ahead of a new router.
- **Remove a speaker** (not the first one), with its alarms. Optionally puts back its
  original cloud URLs, saved when it was added, for handing it on.
- Speakers live in `data/devices.json`. `BOSE_DEVICES` only seeds it on the first start;
  adding a speaker no longer needs a config edit or a release.
- `TS_SCAN_SUBNETS` (optional): the subnets to search; default the /24 of every speaker.

### Security
- The Wi-Fi password goes phone → server → speaker and is never stored or logged
  (checked: absent from the log after a test join).

### Tested
Against two fake speakers (one known, one new on a cable, with a fake port-17000
console) and headless Edge at phone and desktop size, dark and light: discovery
(254 addresses in 1.8 s), the speaker's network list, join, the unplug-and-find path,
redirect, restart, name, test sound, "No" (louder), done, Change Wi-Fi, Remove. The
phone hand-over's form encoding was checked against the fake: a password with `"`, `&`,
`<` and `x=` arrived intact. The real SoundTouch 20's `/networkInfo` was read (read-only)
to confirm the format. **Not yet tried on a real speaker:** the Wi-Fi scan and join,
and the hand-over from a phone in setup mode.

---

## [1.4.1] — 2026-10-22  **The cover can't open the previous station's song**

Live 2026-10-22 with 1.5.0 (`d6f5831`).

### Fixed
- While a newly chosen station was still "Tuning in…", the cover stayed tappable and
  opened the previous station's song (e.g. "on Radyo Voyage" with Power FM already
  chosen). `startTuning()` now clears the shown song, so the art isn't tappable until
  the new station's song arrives. Found while recording the LinkedIn demo video.

### Tested
Headless Edge against the fake speaker: Power FM with a song, tap preset 1 → during
tuning the art has no `can-open` and a tap opens nothing; once Radyo Voyage's song
shows, a tap opens the viewer with that song ("on Radyo Voyage"). No page errors.

---

## [1.4.0] — 2026-10-22  **Cover viewer and sharing; top bar fits on phones**

Live 2026-10-22 (`d32dd47`), released from Telegram `/release`. The user confirmed the
full-screen cover on the iPhone (installed app): "all is well".

### Added
- **Tap the cover** (while a song shows) to see it full screen: the cover at 1000 px
  (the same CDNs as the thumbnail, Power group, Deezer and iTunes, serve that size), on
  a blurred backdrop of itself, with the song, artist and station.
  - **Share** opens the phone's share sheet (WhatsApp, Messages, Mail, …) with the
    **cover image** plus the text; where a browser can't share files, the text only.
    No share sheet at all (e.g. Firefox on a PC): *Copy* puts the text on the clipboard.
  - **WhatsApp** opens WhatsApp straight away with the text (WhatsApp's own link,
    `wa.me`, takes text only, no picture).
  - **Find this song:** search links for Spotify, YouTube and Apple Music.
  - The shared text: `🎵 Title — Artist`, `📻 on Station`, and a YouTube search link so
    the recipient can listen. The app's own address is never shared.
  - Close with ✕, a tap beside the cover, swipe down, or Esc. Long-press the cover to
    save it (phone's own image menu). The viewer keeps the song you tapped even if the
    radio moves on to the next one.
  - Stations without a song (e.g. Europa Plus) aren't tappable; a song without a cover
    shows the station logo, and Share sends the text.

### Fixed
- On a phone, a long speaker name (e.g. "F&F Touching Sounds") pushed the speaker button
  under the theme button. The button now shrinks with the top bar and the name ends in
  "…"; wider screens show the full name as before.

### Tested
Headless Edge with "F&F Touching Sounds": a 10 px gap before the theme button at 320,
375, 390 and 430 px and on desktop (before: 8 px overlap at 390, 46 px at 320). No
horizontal scroll, no page errors. Spotted by Claude in the user's iPhone screenshot.
Cover viewer, against the fake speaker with real Power FM ("Popular", The Weeknd &
…): art tappable, viewer opens with the 1000 px cover (400 px before), image file ready
for sharing (296 KB JPEG), search links and message text as above, closes on a tap
beside it; fits at 320 and 390 px (light and dark) and on desktop. Deezer and iTunes
1000 px URLs checked to load. Europa Plus (no song): art not tappable. **Not tested:**
the share sheet itself and WhatsApp (an OS dialog; needs the phone).

---

## [1.3.1] — 2026-10-22  **The theme follows the phone again**

Live 2026-10-22 (`0ae1c9e`). The user confirmed it in the installed app on an iPhone:
light theme in light mode, without touching the theme button.

### Fixed
- **Light/dark didn't follow the phone** in the installed app (PWA). Versions before
  1.2.0 saved a fixed `light` or `dark` whenever the theme button was tapped, and that
  saved choice kept overriding 1.2.0's "same as this device". The theme is now kept
  under a new key (`ts-theme-mode`) and the old one is removed, so every phone starts
  on *same as this device* once. Picking light or dark by hand still sticks.
- The page no longer shows dark for a moment before switching to light: the theme is set
  in `<head>`, before the first paint.
- The status-bar colour (`theme-color`) is given per scheme, so it matches before the
  script runs too; the app also checks the phone's theme again when it comes back to
  the foreground.

### Tested
Headless Edge at 390 px, with a pre-1.2.0 `ts-theme = dark` saved and the phone in light:
light, old key gone. Phone switched to dark and back: followed live; reload while dark:
dark. Forced dark: kept on reload while the phone is light; back to auto: light again.
No page errors. Not changed: the splash screen shown while an installed app starts uses
the manifest's single (dark) background colour.

---

## [1.3.0] — 2026-10-21  **Song titles, covers, a sleep timer and alarms**

Live 2026-10-21 (`5348e3b`). Checked by the user on the real speaker the same evening:
song titles and covers show, the sleep timer fades out and goes to standby, and an
alarm wakes the speaker. The first real morning alarm, 2026-10-22, worked as planned.

### Added
- **What song is playing**, for internet radio. The speaker itself only knows the
  station, so the server finds the song (new `app/songs.py`):
  - from the station's own now-playing feed, for **Power FM** (and the other
    `powerapp.com.tr` stations; their feed also gives the cover) and **Radyo Voyage**,
    whose streams carry no song data;
  - from **ICY metadata** (`StreamTitle`) in the stream, for the many Icecast/Shoutcast
    stations that send it (e.g. Детское радио Baby FM).
  The player then shows the station name in the badge, the song as the title and the
  artist under it; the mini-player shows *song* / *artist · station*. The station's own
  "title" (its name, or a programme name such as NPO Radio 1's) is not shown as a song.
- **Song covers.** From the station feed when it has one, else a Deezer search for
  "artist title" (iTunes as fallback), with a loose artist/title match so a namesake
  isn't shown. The cover fills the art square, the station logo sits in a badge at its
  corner, and the glow takes the cover's colour. No cover found: the station logo as before.
- **Sleep timer** (moon button next to stop): 15, 30, 45 min, 1, 1.5 or 2 hours. Runs
  on the server (new `app/sleep.py`), so it works with the phone locked or the app
  closed. Over the last minute the volume steps down to 0, then the speaker goes to
  standby and the volume is put back. While it runs the button shows the time left
  (e.g. `23m`); tap it to change or turn it off. One timer per speaker.
- **Alarms** (alarm-clock button next to the moon): wake up to a station at a set time,
  once or on chosen weekdays, per speaker (up to 10 each). Pick the station from
  presets, favorites or recent, and a wake-up volume. Runs on the server (new
  `app/alarm.py`): at the time it turns the volume down to 5, selects the station (which
  wakes the speaker from standby) and raises the volume to the wake-up level over 90 s.
  If the station hasn't started after 25 s, the next preset is tried (up to two
  backups). Alarms are kept in `data/alarms.json`, so a release keeps them. Times are
  wall-clock in the phone's time zone, saved with each alarm (the container runs on UTC).
  The button shows the next alarm time; a red dot and a one-time message flag an alarm
  that didn't play in the last 12 h (*Speaker unreachable*, *Missed: server was off*,
  *No station would start*). *Try it* rings an alarm straight away.
- `/api/status` has two new fields, `song` (`artist`, `title`, `cover`, `via`) and
  `sleep` (`minutes`, `remaining` seconds); `POST /api/sleep {minutes}` (0 turns it off).
- `/api/status` also has `alarm` (`next`, `problem`, `ringing`). Alarm API:
  `GET/POST /api/alarms`, `PUT/DELETE /api/alarms/{id}`, `POST /api/alarms/{id}/test`.
- `requirements.txt`: `tzdata`, so time zones work whatever the base image has.

### How it costs
A song watcher runs only while the app is open: `/api/status` asks for the song, which
starts a watcher for that stream, and one nobody asked about for 45 s stops. An ICY
watcher reads the stream (its bitrate, e.g. 128 kbit/s) for as long as the app is open;
a feed watcher makes one small request per song or every 30 s. Covers are cached in
memory (500 songs).

### Not covered
- **Number One FM** and **Europa Plus** send no song data in the stream, and no feed was
  found for Number One; Europa Plus's site has an API whose playlist call came back
  empty. They show the station as before.
- HLS streams in general (no ICY): only those with a known feed.
- Sleep timers live in memory, so a release (server restart) cancels a running one.
- An alarm can't wake a speaker that is unplugged or off the network (or, at home, when
  the WireGuard link is down); the app then shows it as not played. An alarm more than
  5 minutes late (server down at the time) is reported as missed, not rung late.
- No snooze; stop or standby from the app or the speaker as usual. Pressing stop or
  switching source during the 90 s rise ends the rise.

### Tested
On the dev PC against the fake speakers, with **real** stations: Power FM (feed, cover),
Radyo Voyage (feed, Deezer cover), Baby FM (ICY title, no cover found, logo kept),
Europa Plus and NPO Radio 1 (no song: station view). In Chrome (desktop) and headless
Edge (390 px): song view, station badge, mini-player, fallback, sleep sheet, set,
change, turn off. A 1-minute timer on the fake speaker: volume 40 → 0 in 12 steps,
POWER → standby, volume back to 40 while staying in standby. No page errors.
After the release, on the real SoundTouch 20: song titles and covers, the sleep timer
(fade, standby) and an alarm test (wakes from standby) all worked.
Alarms, against the fake speaker: a one-off alarm with the speaker in standby rang 4 s
after the minute (15 s clock), volume 5 → 30 over 90 s, then switched itself off; with
the speaker down: *Speaker unreachable*; an overdue weekday alarm on server start:
*Missed* and rescheduled to the next weekday; bad time and time zone rejected; the
other speaker's list stays empty. In Chrome: list, edit (days, station, time), switch
off/on, *Try it*, delete, red dot and message; headless Edge at 360 and 390 px, light
and dark. 
---

## [1.2.0] — 2026-10-21  **UI polish: the app reacts to what the speaker is doing**

Live 2026-10-21 (`bcf7c90`), released from Telegram `/release`.

### Added
- **Speaker unreachable state.** After two failed polls in a row (at once for a speaker
  that hasn't answered since the page loaded), the player says "*<name>* is unreachable"
  with a likely cause and a *Try again* button. Transport, volume, presets and source are
  greyed out instead of showing an error message on every tap. A server that can't be
  reached gets its own message. Recovers by itself on the next good poll.
- **Empty presets pick a station.** Tapping an empty slot (now dashed, with a +) opens
  Search with a "Choose a station for preset N" banner; the next station tapped in
  Search, Favorites or Recent is saved to that slot. The slot being filled is
  highlighted, and the rows' play and save-to-preset buttons are hidden meanwhile (a tap
  anywhere on a row saves it).
- **`GET /api/sources`**: the device's sources, one per type, a READY entry preferred.
- **Logo-coloured player.** The glow behind the player takes the station logo's main
  colour (saturation-weighted average, read from the same-origin `/api/art` image on a
  canvas) and fades between stations.

### Changed
- **Source buttons come from the device** (`/api/sources`): the SoundTouch 20 shows AUX
  and Bluetooth (it has no TV/optical input; that button never worked), the adapter AUX.
  Bluetooth is shown even when `UNAVAILABLE`, since that only means nothing is connected.
- **Previous/next only for sources with tracks** (Spotify, Bluetooth, AirPlay, …); hidden
  for internet radio and AUX.
- **Instant feedback.** Choosing a station or a preset shows it in the player at once
  ("Tuning in…", logo pulsing) until the speaker reports it playing, or 15 s. Play/pause
  flips its icon on tap.
- **Theme: auto by default.** The button cycles auto (follows the device) → light → dark.
  A theme chosen before 1.2.0 stays as it was.
- **Logo framing.** Opaque logos sit on a plate of their own background colour (no grey
  frame); transparent ones on a light plate with a hint of their colour, with some
  padding. Small logos (under 128 px) are shown at 1.5× their size (at least 64 px)
  instead of being stretched to 168 px and blurred.
- The line under the station name no longer shows the bare stream URL when nothing else
  is known.

### Tested
On the dev PC: the app against two fake speakers (a SoundTouch 20 with AUX, Bluetooth
and Spotify, an adapter with AUX only; scripted delays, and a switch that makes a
speaker stop answering) in headless Edge, at phone (390 px) and desktop (1280 px), dark
and light. Checked: source buttons per device, skip buttons for Bluetooth only, tuning
state then the real state, logo plate and tint for an opaque, a small transparent, a
colourless and a broken logo, the unreachable state and recovery (including switching to
a device that's already down), assigning an empty preset from Favorites, auto theme
following the system setting; no page errors.
Then in Chrome on the desktop (same fake speakers): presets, assigning, theme cycle,
logo glow in light and dark, adapter sources, unreachable and recovery, no console
errors. This found that empty preset tiles were 127 px tall instead of 73: their `empty`
class collided with the empty-list message style (34 px padding); renamed to `unset`.

---

## ProdRelease fix — 2026-10-20 (live, `2cdc61f`; script only, the app is still 1.1.0)

### Fixed
- **`ProdRelease.bat` could report success when GitHub couldn't be reached.** The
  `:git_failed` label existed twice. A failed `git fetch` jumped to the first copy, which
  re-ran the checkout of the *already present* `origin/main`, so the old code was rebuilt
  and the release ended with "Release Successful". The duplicate block (and the
  unreachable `:git_pull` leftover) is removed, so a failed fetch now stops with
  "[ERROR] Git failed!". (Found while preparing releases from the Telegram bot, which
  runs this script unattended.)

---

## [1.1.0] — 2026-10-19 (live, `9209bac`)  **Several speakers in one app; Wireless Link Adapter support**

### Added
- **Device picker** at the top of the app. Presets, player, volume and source follow
  the selected speaker; favorites, recent and search are shared. The choice is
  remembered per browser.
- **`BOSE_DEVICES`** in `docker-compose.yml` lists the speakers
  (`192.168.1.49,192.168.100.150:fresh`); `BOSE_HOST` alone still works. `GET
  /api/devices` lists them with name, type and online state; the app sends the chosen
  one in an `X-TS-Device` header (unknown devices get 404).
- **Setup through the port-17000 console** for devices marked `:fresh`, for firmware
  without `remote_services` (the Wireless Link Adapter on 26.0.1): reads
  `getpdo CurrentSystemConfiguration`, writes all four `sys configuration` URLs, commits
  them with `envswitch boseurls set`, verifies, reboots (`sys reboot`) and waits for the
  device. Idempotent: does nothing when the URLs already point here. Method and pacing
  from AfterTouch (MIT).
- **Fresh source list** for `:fresh` devices: marge serves AUX + LOCAL_INTERNET_RADIO
  only, which also drops a previous owner's linked Spotify/Amazon/Pandora accounts.

### Changed
- **marge serves each device separately**: requests are routed by account id / deviceID
  (learned from each device's `/info`, saved in `data/identities.json` so a restart can
  route a booting speaker); unknown accounts get 404 instead of the primary's data.
  Non-primary devices keep their state in `data/devices/<ip>/`; the primary keeps the
  existing `data/` files untouched.
- **Speaker setup runs in the background** after the server starts, so a speaker that
  setup reboots finds the server already answering when it asks for its account at boot.
  `ProdRelease.bat`'s version check now answers within seconds.
- One preset-button listener, root shell and preset sync per device.

### Tested
Two fake speakers on the dev PC (one SoundTouch 20, one adapter with a fake console):
console sequence and order, persisted URLs, reboot wait, idempotent restart (only a
`getpdo`), per-device marge routing (adapter: AUX + internet radio; unknown account
404), per-device presets and playback, unknown device header 404, picker in Chrome at
phone and tablet widths. The original single-speaker marge suite: 21/21. Read-only
checks on the real adapter (192.168.100.150, FW 26.0.1): `/info`, `/sources`, port 17000
open, `remote_services` absent, `getpdo` and `sys configuration` present.
**After the release, on the real adapter:** redirected and rebooted by the app, then
LOCAL_INTERNET_RADIO READY, previous owner's accounts gone, http and https/HLS stations play.

---

## [1.0.0] — 2026-10-19 (live, `238ee51`)  **Radio library, responsive app, one-click releases**

First numbered release of the working product: internet radio is back on
the SoundTouch 20 (0.6.0), and the web app is now the way to find, keep and
assign stations, on phone, tablet and desktop.

### Added
- **Station search** (`GET /api/search`): searches the free radio-browser.info
  directory (50,000+ stations) by name, most-listened first, with duplicate
  streams removed. Pasting a stream URL plays it directly.
- **Favorites** (`/api/favorites`): heart any station to keep it. Stored on
  the server (`data/favorites.json`), so every device sees the same list.
- **Recent** (`/api/history`): the last 30 stations played from the app.
- **Save to preset** (`PUT /api/presets/{slot}`): put any station on speaker
  button 1-6. Written to the speaker (`/storePreset`) and to
  `data/presets.json`, which now overrides the `PRESET_N_URL` defaults
  everywhere (startup sync, marge account, button fallback).
- **Station logos** in results, presets and the player, through
  `GET /api/art` (an https page can't load the many http logos directly).
  Images only, max 2 MB, with a CSP so an SVG can't run script.
- **`POST /api/play`** (station object, records history), alongside the
  older `/api/play_url`.
- **`GET /api/version`**: version and git commit (Docker build arg
  `GIT_COMMIT`), shown in the app footer.
- **Installable app (PWA)**: service worker (`/sw.js`) and an updated
  manifest. Installing needs https, i.e. the Cloudflare hostname.
- **`ProdRelease.bat`**: one-click release on the Office Server, in the same
  style as the other projects: backs up `data\`, pulls `main` (turning the
  old manual-copy folder into a git checkout on its first run), rebuilds with
  the commit baked in, then checks the version and the speaker.
- **`ProjectDocs/`**: README, ARCHITECTURE, DECISIONS, DEPLOYMENT,
  USER-MANUAL, ROADMAP; the 0.6.0 deploy notes and the AfterTouch plan
  moved here from `docs/`.

### Changed
- **New responsive UI.** Phones get one view at a time with a bottom tab bar
  (Player, Search, Favorites, Recent) and a mini-player; from 900 px wide
  (tablet landscape, desktop) the player and the library sit side by side.
  The outdated "TuneIn Station" card is gone.
- **Requests that arrive through Cloudflare** (`Cf-Connecting-Ip` header)
  can't reach the speaker-facing endpoints (`/marge`, `/bmx`, `/relay`), the
  diagnostics (`/api/diagnose*`, `/api/test*`, which run shell commands on
  the speaker) or the API docs. The LAN is unaffected.
- The preset-button fallback now uses the same preset set as the UI instead
  of only the environment defaults.

### Fixed (during the first release, 2026-10-19)
- `ProdRelease.bat` finds git when it isn't on PATH (Git for Windows, GitHub Desktop).
- `ProdRelease.bat` makes the folder match `origin/main` (`checkout -f`) instead of
  `git pull`, which refused because of the old checkout plus manual copies. Server-side
  edits are saved as `DataBackup\server_edits_<ts>.patch` first.
- `safe.directory` passed via `GIT_CONFIG_*` env vars; cmd split the `=` of
  `git -c safe.directory=…` inside `for /f`.

### Tested
Against a fake speaker on the dev PC, in Chrome at desktop (1152 px), tablet
(720 px) and phone (390 px) widths: search (live radio-browser), play,
favorite, save to preset (sheet on phone, dialog on desktop), mini-player,
logo fallback, Cloudflare guard (blocked paths 404 with the header, 200
without), and the earlier marge checks. `ProdRelease.bat` was run in real cmd
on a scratch copy (Docker stubbed): first run converted the folder to a git
checkout and kept `data\`, second run took the normal pull path.

---

## [0.6.0] — 2026-10-19 (deployed, radio confirmed playing)  **Local marge server: the real fix for 1005**

### Root cause (supersedes the [0.4.24] conclusion)
At boot the speaker gets its source table from Bose's **marge** account
server (`margeServerUrl` = `https://streaming.bose.com`), which has been
dead since 2026-05-06. In 0.4.x we redirected `bmxRegistryUrl` and TuneIn
and patched `Sources.xml`, but never marge, so LOCAL_INTERNET_RADIO never
registered and every `/select` returned 1005. Community projects
(AfterTouch, soundcork, STR) confirmed this mechanism.

### Added
- **`app/marge.py`**: a local marge stand-in. It serves the full account
  (devices, presets, recents, sources), source providers, the preset
  PUT/DELETE calls, recents POST, provider settings, the software-update
  "no update" reply and power_on. Unknown `/marge/*` calls are logged as
  `marge: UNHANDLED` and get a 404. Response formats follow soundcork and
  AfterTouch (both MIT).
- **Sources snapshot**: before redirecting marge, the speaker's own
  `Sources.xml` (including SPOTIFY/ALEXA secrets) is copied to
  `data/Sources.xml`. **marge is only redirected once that snapshot
  exists**, so Spotify can't be lost to an empty source list.
- **`GET /bmx/orion/station?data=…`**: resolves LOCAL_INTERNET_RADIO
  locations to the real stream URL.
- **`app/server.py`**: uvicorn entry point that keeps the `ETag` header's
  casing. uvicorn lowercases it, and the firmware ignores `etag`
  (soundcork #129). The Dockerfile now runs `python -m app.server`.
- `docker-compose.yml`: `./data:/app/data` volume and `TS_DATA_DIR`.

### Changed
- `OverrideSdkPrivateCfg.xml` now redirects **`margeServerUrl`** as well as
  `bmxRegistryUrl` (`_configure_cloud_urls`, replacing
  `_configure_bmx_registry`). Stats and software-update URLs are left
  alone. Setup issues one reboot instead of two.
- `Sources.xml` LOCAL_INTERNET_RADIO patch: the canonical
  `{"serial":"local-internet-radio"}` token with an empty account, replacing
  the 0.4.x blank token and `touchingsounds-anon-001` account.
- `/api/play_url` and the preset-button listener now use
  **`bose.play_stream`** (`/select` LOCAL_INTERNET_RADIO with a relative
  Orion location) instead of the dead UPnP path. The listener skips presets
  the speaker already played by itself.
- `tunein-proxy` is now opt-in (`--profile tunein-proxy`). It's obsolete,
  and its port-80 conflict on the Office Server broke `docker compose up`.

### Fixed (during deploy, 2026-10-19)
- **Wrong server address.** `BOSE_SERVER_URL` and the code defaults pointed
  at `192.168.100.135`, which the speaker can't reach (HTTP 000 from the
  speaker's shell). The Office Server is `192.168.100.51`. This probably
  also explains why the 0.4.x BMX redirect never took effect.
- **Preset button fallback** now compares the playing station's location,
  not just the source. The speaker's stale 0.4.x preset slots
  (`localhost:8080/N.json`) are LOCAL_INTERNET_RADIO too, so the old check
  skipped them.
- **Dead preset URLs replaced.** All five old `PRESET_N_URL`s failed to
  resolve. Presets are now 1 Radyo Voyage, 2 Number One FM, 3 Europa Plus,
  each verified live.

### Added (during deploy)
- **`/relay?url=…`**: re-streams HTTPS and HLS (`.m3u8`) stations as
  plain-HTTP 128 kbps MP3 via ffmpeg, one process per listener, killed when
  the speaker disconnects. The station resolver hands the speaker the relay
  URL automatically, so `/api/play_url` and presets now accept https and
  `.m3u8` (needed for Number One FM, which only streams HLS over https).
  ffmpeg was added to the image.
- **`bose.sync_presets()`** at startup writes `PRESET_N_URL`s into the
  speaker's preset slots (`/storePreset`) if they differ, so the buttons
  play our stations natively and show the right names.

### Tested
- Locally against a fake speaker: marge endpoints (with the literal `ETag:`
  header on the wire), setup/snapshot-gate checks, the relay with the real
  Number One HLS stream (valid MP3, 128 kbps), ffmpeg cleanup on
  disconnect, and rejection of non-http(s) relay URLs.
- **On the real speaker (before the relay/preset changes):** after the IP
  fix, the speaker reached our server, LOCAL_INTERNET_RADIO showed READY,
  Spotify/Alexa stayed READY, and `/api/play_url` with NPO Radio 1 reached
  PLAY_STATE.

---

## [0.5.1] — 2026-07-19  **Add-to-Home-Screen (PWA manifest + icons)**

### Added
- **`app/static/manifest.json`** + **`app/static/icons/`** (favicon 16/32,
  `apple-touch-icon.png` 180x180, `icon-192.png`, `icon-512.png`) — amber
  equalizer-bar glyph on dark background, matching the 0.5.0 redesign's
  accent color and the hero card's `.eq` pulse motif.
- `index.html` `<head>`: `<link rel="manifest">`, `theme-color` meta,
  favicon links, and `apple-mobile-web-app-*` / `apple-touch-icon` tags for
  iOS Safari's Add to Home Screen.

### Notes
No service worker — the app is served over plain HTTP on the LAN/
WireGuard tunnel, not a secure context, so SW registration isn't possible
(and isn't useful for a remote control that always needs a live
connection). Install is manual: Safari Share sheet → **Add to Home
Screen**, or Chrome ⋮ menu → **Add to Home screen** — not an automatic
install banner.

---

## [0.5.0] — 2026-07-19  **Front-end redesign (Claude Design handoff)**

### Changed
- **`app/static/index.html`** fully re-skinned from a Claude Design mockup
  (handed off as a project export, implemented directly): oklch-based
  dark/light theme with a toggle persisted to `localStorage`, hero "Now
  Playing" card with an animated equalizer pulse while playing, segmented
  source control with a sliding indicator, redesigned transport/volume/
  preset controls.
- Every existing `/api/*` call (3s status polling, `key`, `volume`,
  `source`, `play_url`, `presets`) kept byte-identical to the pre-redesign
  version — pure markup/CSS/JS-structure change, zero backend impact.

### Deployment note
This project is deployed to the Office Server by **manual file copy**, not
`git pull` — `D:\TouchingSounds\` is not a git working tree there. After
copying updated files, a full rebuild (`docker compose up -d --build`) is
required, not just `docker compose restart`, since `app/` is baked into
the image at build time on that machine.

---

## [0.4.24] — 2026-06-13 (unreleased)  **CONCLUSION: no source on this firmware accepts arbitrary stream URLs — root cause identified**

### Found — GET /sources is the ground truth, and it ends the investigation
```
READY:       AUX (hardware line-in), SPOTIFY (owner's login, Spotify catalog
              only), ALEXA (Alexa skills only)
UNAVAILABLE: BLUETOOTH, AIRPLAY, QPLAY x2, UPNP, STORED_MUSIC_MEDIA_RENDERER,
              NOTIFICATION
NOT PRESENT: TUNEIN, LOCAL_INTERNET_RADIO  (not even listed as UNAVAILABLE)
```
`/select` rejects (1005) any source not in this table — independent of
Sources.xml patches, BMX registry, sourceAccount, or `location` content.
[0.4.22]/[0.4.23] both failed for this reason, not a URL/syntax problem.

### Root cause
This table is populated at boot via a Bose-cloud handshake that's dead.
SPOTIFY/ALEXA/AUX are READY only because they're either hardware-local or
authenticate directly with their own (non-Bose) cloud — same reason
TUNEIN/LOCAL_INTERNET_RADIO never register, regardless of any on-device
config we change. **No currently-READY source accepts an arbitrary stream
URL** (AUX = physical cable, SPOTIFY = Spotify catalog, ALEXA = Alexa
skills). This makes "play any fmstream.org URL" impossible via `/select`
on this firmware as-is.

### Status — closes the LOCAL_INTERNET_RADIO / UPnP / IPC investigation
[0.4.10]–[0.4.24] (this entire session) confirms three independent
playback-injection mechanisms are all dead ends:
1. `/select` source=LOCAL_INTERNET_RADIO or TUNEIN — 1005, not in source table
2. UPnP AVTransport (port 40010 / 8091) — UpnpSource process unresponsive
3. `/tmp/cliservercommand.uds` IPC / port 17000 — remote-button simulator only

### Only remaining theoretical path (separate, larger effort — not attempted)
TUNEIN might become READY if its boot-time cloud check succeeds — this is
what the original TuneIn-proxy plan (DNS-intercept opml.radiotime.com,
blocked on a port-80 conflict on the office server) was for. That's a
*registration-time* fix, not a per-request one, and is a distinct task from
everything tried this session.

---

## [0.4.23] — 2026-06-13 (unreleased)  **Result: LOCAL_INTERNET_RADIO confirmed 1005 — new test: TUNEIN + stationurl direct location**

### Found — 1005 confirmed live, with a real stream URL
`POST /api/test/local_internet_radio` with `location=http://streaming.
radiovoyage.com/radiovoyage` returned HTTP 500 /
`<errors><error value="1005" name="UNKNOWN_SOURCE_ERROR">1005</error>
</errors>`. The 1005 fires on the SOURCE NAME before `location` is even
parsed — confirms the bose.py header comment, `LOCAL_INTERNET_RADIO` is
hardcoded-blocked in the runtime source table regardless of the Sources.xml
patch. Combined with [0.4.21] (port-17000 IPC dead end for stream
injection), the LOCAL_INTERNET_RADIO + UPnP/IPC investigation is closed.

### Added — one more untried angle: TUNEIN + stationurl + direct location
`TUNEIN` is already a valid/registered source (existing presets use it
successfully for play/pause/volume). Presets use `type="stationurl"` with a
Bose-cloud path (`/v1/playback/station/sXXXXX`) that needs cloud resolution
— but if `type="stationurl"` plays whatever's literally in `location`
without cloud involvement, pointing it at a direct HTTP stream URL might
just work, since TUNEIN won't trigger 1005.
- **`select_tunein_stationurl(stream_url, name, account)`** — POSTs
  `<ContentItem source="TUNEIN" type="stationurl" location="<url>"
  sourceAccount="<account>" isPresetable="true">...`.
- `POST /api/test/tunein_stationurl?account=<...>` `{"url": "...", "name": "..."}`.
- `GET /api/diagnose/presets_raw` — raw `/presets` XML, to read the
  `sourceAccount` value on existing TUNEIN presets before testing.

---

## [0.4.22] — 2026-06-13 (unreleased)  **Definitive test: /select source=LOCAL_INTERNET_RADIO with a real stream URL**

### Added
- **`select_internet_radio(stream_url, name)`** in bose.py — POSTs
  `<ContentItem source="LOCAL_INTERNET_RADIO" type="stationurl"
  location="<url>" sourceAccount="touchingsounds-anon-001"
  isPresetable="true"><itemName>...</itemName></ContentItem>` to `/select`
  and returns the raw response body (success or 1005 fault) regardless of
  HTTP status — unlike `select_source()`, which raises on non-2xx.
- `POST /api/test/local_internet_radio` `{"url": "...", "name": "..."}`.

### Why now
bose.py's own header comment claims this was already tried post-Sources.xml-
patch and returns 1005 — but that's a prior-session note, not re-verified
this session. Testing with a REAL stream URL (one of the working preset
URLs) and a clean endpoint gives a definitive, current answer.

---

## [0.4.21] — 2026-06-13 (unreleased)  **Result: port 17000 `key` = remote-button simulator, not stream injection**

### Found — clean isolated probe results
Valid key names (`"Key sent\n->OK\n->"`): `play`, `pause`, `stop`, `next`,
`prev`, `mute`, `power`, `aux`. Everything else tried (`bluetooth`,
`spotify`, `preset1`-`preset6`, `volup`/`voldown`, `VOLUME_UP`,
`KEY_PLAYPAUSE`, etc.) returned `"Invalid key unable to send key"` — wrong
exact name, but these are all still hardware-button equivalents.

Critically: `select`, `source`, `play_url`, `playurl`, `url`, `stream`,
`tunein`, `select LOCAL_INTERNET_RADIO` all returned `"Command not found"`
(not `"Invalid..."`) — these command NAMES don't exist on this CLI at all.

### Decision — port 17000 / cliservercommand.uds IPC pivot is a dead end for stream injection
The IPC command bus is a remote-button simulator (play/pause/stop/next/
prev/mute/power/aux confirmed working), not a mechanism for setting an
arbitrary playback URL. It cannot replace AVTransport SetAVTransportURI or
the TuneIn `/select` flow. This closes out the multi-session IPC
investigation that started at [0.4.10].

---

## [0.4.20] — 2026-06-13 (unreleased)  **Diag: isolated port-17000 probe — clean key-name + play_url syntax test**

### Found — response bleed between consecutive port-17000 commands
`diagnose_port17000()`'s shared-connection probe gave noisy/contradictory
results: `"key play"` → `"Key sent\n->OK\n->"` but `"key pause"` →
`"Key sent\n->"` (no trailing `OK`), `"key power"` →
`"Invalid key unable to send key\n->"` while `"key POWER"` → `"OK\n->"`.
This pattern (async confirmation for command N arriving only while reading
command N+1's response) makes the shared-connection results unreliable for
mapping the exact key-name vocabulary.

### Added
- **`_query_port17000_isolated()`** — opens a fresh TCP connection per
  command, reads until the first `->` prompt, then drains 1.5s of any
  late-arriving async output before closing. Eliminates cross-command bleed.
- **`diagnose_port17000_keys()`** — re-tests `key play`/`key pause` plus a
  broader lowercase/Linux-input-style key vocabulary (`stop`, `next`,
  `prev`, `voldown`, `preset1`-`preset6`, `aux`, `spotify`, etc.) cleanly,
  AND probes non-`key` commands (`select aux`, `play_url <url>`, `url
  <url>`, `stream <url>`, `tunein s108635`, `select LOCAL_INTERNET_RADIO`)
  — the syntax we actually need for stream injection if the `key` vocabulary
  confirms a naming convention.
- `GET /api/diagnose/port17000_keys` → `{"keys": {...}, "other_commands": {...}}`.

---

## [0.4.19] — 2026-06-13 (unreleased)  **Diag: port-17000 `key` command confirmed real — probe key-name syntax**

### Found — `key` is a live command on port 17000
`key` alone → `"Unknown key, need at least one argument\n->OK\n->"`.
`key PLAY` / `key PAUSE` / `key PRESET_1` / `key help` all → `"Invalid key
unable to send key\n->OK\n->"`. Every variant ends in `OK` — the command
always executes, it just rejects the key NAME. The REST-API key names
(`PLAY`, `PAUSE`, `PRESET_1`) aren't recognized by this lower-level CLI; it
likely expects Linux input-event key names/codes (`KEY_PLAY`, numeric
codes from `linux/input-event-codes.h`) or a different casing convention.

### Added
- **`diagnose_port17000()`** — `probe_commands` extended with ~25 key-name
  candidates: numeric codes (`0`-`3`, `10`, `11`, `100`, `164`=KEY_PLAYPAUSE,
  `207`=KEY_PLAY, `119`=KEY_PAUSE), lowercase (`play`, `pause`, `power`,
  `mute`, `preset1`), `KEY_`-prefixed Linux input names, and `key
  ?`/`key -h`/`key list` to fish for a usage string.

---

## [0.4.18] — 2026-06-13 (unreleased)  **Diag: probe port-17000 CLI for cliservercommand.uds syntax**

### Found — perl confirmed fully dead, all on-device connectors exhausted
`libperl_search_full` (whole-filesystem) found no `libperl.so*` anywhere;
`perl` is a symlink to `perl5.14.3`, and the underlying binary itself fails
with the same missing-library error. socat/`nc -U`/Python/Perl are all
unusable — the on-device-connector approach to `/tmp/cliservercommand.uds`
is exhausted.

### Decision — pivot to port 17000 instead of uploading binaries
`_query_port17000()` already opens a raw TCP connection to the speaker's
port-17000 `->`-prompt CLI directly from this container — no on-device
tooling required. That CLI accepts at least one real system command
(`remote_services on`) and is hypothesized to share its command vocabulary
with the `cliservercommand.uds` bus (same underlying Bose CLI-server stack,
different transports). Testing this is free and carries none of the risk of
cross-compiling a static ARM binary or transplanting `libperl.so.5`.

### Added
- **`diagnose_port17000()`** — `probe_commands` extended with
  `key PLAY`, `key PAUSE`, `key PRESET_1`, `cli_help`, `key`, `key help`,
  `play`, `play_url` — the `_IPC_PRESETS`-style syntax we hypothesized for
  the UDS, now tried directly over TCP:17000.

---

## [0.4.17] — 2026-06-13 (unreleased)  **Diag: broaden libperl.so search + check perl5.14.3 variant**

### Found — `libperl.so*` not in any of the standard search paths
`/opt/Bose/lib`, `/opt/Bose/lib64`, `/usr/local/lib` don't exist on this
filesystem; `/usr/lib` and `/lib` exist but contain no `libperl.so*`. The
[0.4.16] `find` came back empty everywhere it could actually search — the
0.4.15/0.4.16 fixes did not change the `ipc_probe()` outcome, confirming
perl's shared lib is genuinely missing from the searched locations.

### Added
- **`diagnose_ipc_tools()`** — two new checks:
  - `libperl_search_full`: `find / -xdev -iname 'libperl*'` (whole root
    filesystem, same-device only) plus an explicit `find /mnt /opt /var
    -iname 'libperl*'` to catch separate mount points the `-xdev` root scan
    would skip.
  - `perl_variants`: `ls -la /usr/bin/perl*` (bin_scan showed both `perl`
    and `perl5.14.3` as distinct files in `/usr/bin` — if `perl` is a thin
    wrapper/symlink and `perl5.14.3` is a separately-linked or static
    binary, invoking it directly may sidestep the missing-`libperl.so.5`
    error) plus a direct `perl5.14.3 -e 'print "OK\n"'` smoke test and a
    bare `perl -e 1` to capture the exact current error.

### Decision
If `libperl_search_full` also comes up empty and `perl5.14.3 -e` fails the
same way, perl is a dead end on this firmware regardless of
`LD_LIBRARY_PATH` — the on-device-interpreter approach (Python, Perl) is
exhausted (socat=NOT_FOUND, `nc -U`=unsupported, Python=NOT_FOUND, Perl
unusable). The next pivot would be uploading a static-linked armv7l AF_UNIX
client binary to `/tmp` via the existing `_write_file_bytes()` mechanism —
to be raised with the user before implementing.

---

## [0.4.16] — 2026-06-13 (unreleased)  **Fix: perl missing libperl.so.5 — locate + LD_LIBRARY_PATH**

### Found — perl binary present but its shared lib isn't on the default path
`/usr/bin/perl` exists but fails with `error while loading shared libraries:
libperl.so.5: cannot open shared object file: No such file or directory` —
the dynamic linker's default search path doesn't include wherever Bose
installed `libperl.so.5` (likely under `/opt/Bose/lib*`, which isn't in
`/etc/ld.so.conf` on this firmware).

### Added
- **`diagnose_ipc_tools()`** — new `libperl_search` check:
  `find /opt/Bose/lib /opt/Bose/lib64 /usr/lib /usr/local/lib /lib -name
  'libperl.so*'`.

### Changed
- **`ipc_probe()`** — the Perl-injector branch now runs the same `find` at
  invocation time, takes the first match, strips the filename with
  `${LIBPERL%/*}` to get the containing directory, and prepends it to
  `LD_LIBRARY_PATH` before exec'ing perl:
  `LD_LIBRARY_PATH="$LIBDIR:$LD_LIBRARY_PATH" timeout -t 5 $PL
  /tmp/inject.pl <payload>`. If no `libperl.so*` is found anywhere, `LIBDIR`
  is empty and perl runs with an unmodified `LD_LIBRARY_PATH` (same failure
  as before, but at least falls through cleanly rather than erroring on bad
  syntax).

---

## [0.4.15] — 2026-06-13 (unreleased)  **Fix: BusyBox `timeout` syntax (`-t SECS`, not `timeout SECS`)**

### Found — every ipc_probe() branch failed with "can't execute '5'"
BusyBox v1.19.4's `timeout` applet requires `-t SECS PROG ARGS` — bare
`timeout 5 cmd` makes it try to exec `"5"` as the program. This broke the
Perl-injector path immediately (`cli_help`/`cli_play` both returned
`timeout: can't execute '5': No such file or directory` before Perl even ran).

### Changed
- **`ipc_probe()`** — all four `timeout N ...` invocations (socat, `nc -U`,
  Python injector, Perl injector) changed to `timeout -t N ...`.

---

## [0.4.14] — 2026-06-13 (unreleased)  **Perl AF_UNIX injector — Python also unavailable**

### Found — no Python on this firmware, but Perl 5.14.3 is present
Expanded `diagnose_ipc_tools()` (added `nc_help_full`, `uname`, `bin_scan`,
`busybox_applets`) confirmed: BusyBox v1.19.4, armv7l. `nc` is the BusyBox
applet with NO `-U`/AF_UNIX support at all (confirmed via full `--help`
dump — `-e/-l/-p/-w/-i/-f` only). `python`/`python3`/`python2` are all
NOT_FOUND. `/usr/bin/perl` (5.14.3) IS present — `IO::Socket::UNIX` and
`IO::Select` are core modules, so Perl can do everything the Python injector
was meant to do.

### Added
- **`_IPC_INJECTOR_PL`** / **`_IPC_INJECTOR_PL_PATH`** (`/tmp/inject.pl`) —
  Perl equivalent of the Python injector: reads the payload from argv[1]
  (binary-safe), connects to `/tmp/cliservercommand.uds` via
  `IO::Socket::UNIX`, `print`s the payload, then uses `IO::Select->can_read(2)`
  + `sysread` to drain the response with a 2s timeout. Prints
  `SENT_HEX=`/`RECV_HEX=`/`RECV_TXT=` like the Python version.
- **`ipc_probe()`** — connector priority is now socat > `nc -U` > Python
  injector > **Perl injector**. Both `/tmp/inject.py` and `/tmp/inject.pl`
  are written on every call; the shell picks whichever interpreter exists.

No endpoint changes — `GET /api/diagnose/ipc_probe?preset=...` now resolves
to the Perl path on this firmware.

---

## [0.4.13] — 2026-06-13 (unreleased)  **Python AF_UNIX injector — socat/nc -U both unavailable**

### Found — no standard UDS connector on this firmware
`diagnose_ipc_tools()` confirmed: `nc` is present but lacks `-U` (no AF_UNIX
support), `socat` is missing entirely. `ipc_probe()`'s existing socat/`nc -U`
paths can never fire on this device.

### Added — `/tmp/inject.py`, written and run via the existing root shell
- **`_IPC_INJECTOR_PY`** / **`_IPC_INJECTOR_PATH`** — a small `socket.AF_UNIX`
  client script. Reads the payload from a file (argv[1]) — not argv text —
  so binary/framed payloads survive intact. Connects to
  `/tmp/cliservercommand.uds`, `sendall()`s the payload, `recv(4096)` with a
  3s timeout, prints `SENT_HEX=`/`RECV_HEX=`/`RECV_TXT=`.
- **`ipc_probe()`** — now writes `/tmp/inject.py` fresh on every call (via
  `_write_file`) and, when socat/`nc -U` are both absent, runs
  `$PY /tmp/inject.py tmp_ipc_payload.bin` where `$PY` is whichever of
  `python`/`python3`/`python2` is on the speaker's PATH (confirmed present by
  `diagnose_ipc_tools()`'s new `python` check).

No endpoint changes — `GET /api/diagnose/ipc_probe?preset=...` now works
end-to-end on this firmware via the Python fallback automatically.

---

## [0.4.12] — 2026-06-13 (unreleased)  **From sniffing to injection: `ipc_probe()` on /tmp/cliservercommand.uds**

### Found — strace blocked (ptrace restricted)
`strace -p` on APServer/STSCertified/BoseApp produced no output — the
firmware's kernel almost certainly has `kernel.yama.ptrace_scope` locked
down, or strace itself isn't a real ptrace-capable build. Passive snooping
is off the table; pivoting to active injection.

### Clarified — the UDS lives on the speaker, not in our container
`/tmp/cliservercommand.uds` is a path inside the speaker's BusyBox root
filesystem. Our FastAPI container has no namespace access to it —
`asyncio.open_unix_connection()` from inside this container would just raise
FileNotFoundError. Every interaction has to go through the existing root
Telnet shell (`_shell`), same as all other diagnose_*() functions: write the
payload to a tmp file on the speaker via base64, then pipe it into the UDS
using a connector binary that runs ON the speaker (`socat` or `nc -U`).

### Added
- **`_write_file_bytes()`** — binary-safe variant of `_write_file()` for
  arbitrary payload bytes (framed/binary probes, not just utf-8 text).
- **`diagnose_ipc_tools()`** / **`GET /api/diagnose/ipc_tools`** — checks for
  `socat` and `nc -U` (AF_UNIX support) on the speaker; call this first.
- **`_IPC_PRESETS`** — three framing hypotheses for cliservercommand.uds:
  - `cli_*` — plain-text line commands matching the port-17000 `->` CLI
    vocabulary (`key PLAY\r\n`, `key PRESET_1\r\n`, `sources\r\n`, `help\r\n`)
    — cliservercommand.uds sounds like the literal backend for that CLI.
  - `json_cmd_play` / `json_rpc_key` — bare `{"cmd":"play"}` and
    JSON-RPC-style `{"jsonrpc":"2.0","method":"key","params":{"key":"PLAY"}}`,
    in case the bus speaks FrontDoor-style JSON.
  - `framed_key_play` — 4-byte big-endian length prefix + `"key PLAY"`, in
    case the bus uses length-delimited framing instead of newlines.
- **`ipc_probe()`** / **`GET /api/diagnose/ipc_probe?preset=...`** (or
  `?command=...` for raw text) — writes the payload to
  `tmp_ipc_payload.bin`, pipes it through `socat -t2 - UNIX-CONNECT:...`
  (falling back to `nc -U`) into `/tmp/cliservercommand.uds`, and returns the
  response as both hex dump and text.

### Workflow
1. `GET /api/diagnose/ipc_tools` — confirm socat or `nc -U` exists.
2. `GET /api/diagnose/ipc_probe?preset=cli_help` — sanity check: does the bus
   echo a usage string for an unrecognized/help command?
3. `GET /api/diagnose/ipc_probe?preset=cli_play` — try the play command.
4. Iterate with `?command=...` for custom strings once a framing responds.

---

## [0.4.11] — 2026-06-13 (unreleased)  **IPC bus pivot: passive `strace` snoop on /tmp/cliservercommand.uds**

### Found — 8091 IS a loopback API mirror, but gated identically to 8090
`api_paths_8091` returns 404 for every path (vs 200/500/etc on 8090) —
8091 doesn't mirror 8090's route table at all, so it's not a usable
bypass. `select_spotify_8090` confirms 8090 still hard-blocks with 1005
UNKNOWN_SOURCE_ERROR regardless of source name.

### Found — /tmp/cliservercommand.uds is the central command bus
`unix_sockets` shows `/tmp/cliservercommand.uds` with many STREAM CONNECTED
fds shared across APServer (1800), STSCertified (1794), and BoseApp (1805) —
almost certainly the same bus that backs the port-17000 `->` CLI and the
physical front-panel button handlers. Other candidate buses present:
`/tmp/btmgr.uds`, `/tmp/spotifyaudio.uds`, `/tmp/syscheck.uds`.

### Decision — passive `strace` snoop instead of a MITM proxy
A man-in-the-middle proxy (rename the socket, splice traffic through our own
listener) risks breaking the existing connected fds and could require a
speaker reboot to recover. Since we have root, `strace -p <pid>` (ptrace) can
log every `read`/`write`/`recvfrom`/`sendto` syscall — including buffer
contents — on APServer/STSCertified/BoseApp without touching the socket
itself. Non-invasive; the only cost is added per-syscall latency while
attached.

### Added
- **`diagnose_ipc_bus()`** / **`GET /api/diagnose/ipc_bus`** — checks
  `strace`/`ltrace` availability, lists all `/tmp/*.uds` buses, dumps
  `/proc/net/unix` entries for `cliservercommand.uds`, correlates socket
  inodes to owning PIDs, and pgreps APServer/STSCertified/BoseApp.
- **`start_ipc_snoop()`** / **`POST /api/diagnose/ipc_snoop/start`** —
  backgrounds `strace -tt -s 400 -e trace=read,write,recvfrom,sendto,connect,accept`
  attached to all three PIDs, logging to `/tmp/ts_ipc_snoop.log`.
- **`get_ipc_snoop_log()`** / **`GET /api/diagnose/ipc_snoop/log`** — tails
  the capture.
- **`stop_ipc_snoop()`** / **`POST /api/diagnose/ipc_snoop/stop`** — kills the
  background strace.

### Workflow
1. `GET /api/diagnose/ipc_bus` — confirm strace is present and note PIDs.
2. `POST /api/diagnose/ipc_snoop/start`.
3. Press PLAY / a preset button on the physical speaker.
4. `GET /api/diagnose/ipc_snoop/log` — read the captured command bytes.
5. `POST /api/diagnose/ipc_snoop/stop`.

---

## [0.4.10] — 2026-06-13 (unreleased)  **Strategy pivot: hijack 8091/STSCertified instead of forcing UPnP**

### Found — UpnpSource (40010) is a dead end
"Empty reply (52)" on every request (even baseline GET /), source reported
UNAVAILABLE, and the port-17000 `upnp` command rejects every flag variant
tried so far. Forcing UPNP to READY is shelved.

### Pivot rationale
Port 8091 (owned by STSCertified, PID 1794) returned 404 on earlier probes —
a 404 means a real HTTP server parsed and routed the request, unlike 40010's
instant connection drop. Working hypothesis: 8091 is a loopback-only mirror
of the public SoundTouch API (8090) that may not enforce the same source
gating (1005 UNKNOWN_SOURCE_ERROR) as the public-facing API.

### Added
- **`diagnose_internal_api()`** / **`GET /api/diagnose/internal_api`**:
  - `api_paths_8091` / `api_paths_8090` — every known SoundTouch REST
    endpoint (`/info`, `/now_playing`, `/select`, `/key`, `/sources`,
    `/volume`, etc.) probed on both ports via HTTP status code, for
    comparison.
  - `api_bodies_8091` — full response bodies for `/info`, `/now_playing`,
    `/sources`, `/select` on 8091.
  - `select_spotify_8091` / `select_spotify_8090` — POST a real `/select`
    body (`source="SPOTIFY"`) to both ports with `-v` + exit code, to see
    if 8091 accepts source-redirect calls the public API blocks.
  - `stscertified_fds` / `apserver_fds` — `/proc/<pid>/fd` listing for
    STSCertified (1794) and APServer (1806), looking for AF_UNIX sockets,
    FIFOs, or shared-memory fds usable for direct IPC.
  - `unix_sockets` — every AF_UNIX socket on the device (`netstat -x` +
    `find -type s`).
  - `sysv_ipc` — `ipcs` dump (System V shm/msgqueue/semaphores), often used
    by Bose's internal notification bus.

---

## [0.4.9] — 2026-06-13 (unreleased)  **Expect:100-continue theory disproven; `upnp` CLI command found**

### Found — Empty reply (52) is NOT an Expect:100-continue issue
The `_noexpect`/`_http10`/`_useragent` variants from 0.4.8, AND a baseline
`curl -v http://localhost:40010/` (GET, no body at all), ALL return identical
"Empty reply from server (52)" — the socket accepts the TCP connection and
drops it instantly, before reading even the HTTP request line. This rules out
request-header/body-size causes entirely. The most likely explanation: the
HTTP listener on 40010 is a placeholder socket bound during `UpnpSource`
process init, with the real SOAP request-handling logic only wired up once
the UPNP source transitions out of UNAVAILABLE.

### Found — `upnp` is a real command on the port-17000 CLI
While every other guess returned `Command not found`, `upnp` and
`upnp enable` both returned `Invalid Command Option` — proving `upnp` is a
recognized command on the port-17000 `->`-prompt CLI that needs the correct
flags/arguments. This is the most promising lead yet for forcing the UPNP
source to READY and waking up the AVTransport listener.

### Added
- **`diagnose_upnp_control()`** — two new checks: `upnpsource_config_dump`
  (full `cat` of `UpnpSource-spotty.xml` plus any other `*upnp*`,
  `*avtransport*`, `*mediarenderer*` files under `/opt/Bose/etc` and
  `/opt/Bose/Xml`) and `upnpsource_environ` (dump of `UpnpSource`'s process
  environment via `/proc/<pid>/environ`) — looking for binding rules,
  security/auth flags, or an explicit "source must be active" gate.
- **`diagnose_port17000()`** — expanded `probe_commands` with `upnp`-specific
  syntax guesses: `upnp help`, `upnp -h`, `upnp ?`, `upnp` (bare), `upnp
  status`, `upnp show`, `upnp list`, `upnp info`, `upnp start`, `upnp on`,
  `upnp 1`, `upnp enable 1`, `upnp -e`, `upnp -e 1`, `upnp -s`, `upnp init`,
  `upnp restart`.

---

## [0.4.8] — 2026-06-13 (unreleased)  **"Empty reply (52)" — Expect: 100-continue**

### Found — UpnpSource (40010) listens, but `curl -d @file` gets Error 52
`curl -v` against `http://localhost:40010/AVTransport/Control` returns
"Empty reply from server" for every POST. Likely cause: the
SetAVTransportURI body (SOAP envelope + escaped DIDL-Lite metadata) is right
around or over 1024 bytes, so curl automatically adds
`Expect: 100-continue`. Tiny embedded HTTP servers (libupnp's miniserver
included) typically don't implement the 100-continue handshake — they never
send "100 Continue", curl waits for it, and the server eventually closes the
idle connection → "Empty reply from server" (curl error 52), with NO SOAP
fault ever generated. `sources_api` reporting UPNP as UNAVAILABLE may simply
reflect Bose's app-level source list and be unrelated to whether the
AVTransport SOAP server itself works.

### Changed
- **`play_url()`** — both `SetAVTransportURI` and `Play` curl commands now
  send `-H 'Expect:'` to disable the 100-continue handshake.

### Added
- **`diagnose_upnp_control()`** — three new variants of the SetAVTransportURI
  probe, each with `-v` + body-size + exit code: `_noexpect` (the proposed
  fix), `_http10` (`--http1.0` fallback), `_useragent` (SoundTouch-flavoured
  `User-Agent`, in case identity matters too).
- **`_query_port17000()`** / **`diagnose_port17000()`** /
  **`GET /api/diagnose/port17000`** — sends a battery of candidate commands
  (`sources`, `source status`, `upnp enable`, `renderer status`, etc.) to the
  port-17000 `->`-prompt CLI (a different command set from the BusyBox shell
  on port 23), looking for a way to force the UPNP source to READY if the
  `Expect:` fix alone doesn't resolve it.

---

## [0.4.7] — 2026-06-13 (unreleased)  **Diagnose empty SOAP responses**

### Problem
`play_url()` against `http://localhost:40010/AVTransport/Control` logged
empty strings for both SetAVTransportURI and Play — `curl -s` swallows
connection refusals, timeouts, non-2xx statuses, and `<s:Fault>` bodies alike,
so an empty string is uninformative.

### Added
- **`diagnose_upnp_control()`** / **`GET /api/diagnose/upnp_control`** —
  re-runs the SetAVTransportURI POST WITHOUT `-s`, with `-v` and an explicit
  `echo EXIT=$?`, reusing the actual `tmp_upnp_seturi.xml` written by the last
  `play_url()` call. Also checks:
  - `port_40010_listen` — is 0x9C4A actually in LISTEN state right now?
  - `upnpsource_proc` — `/proc/<pid>/status` + `cmdline` for the UpnpSource process
  - `curl_verbose_root` — baseline GET / on port 40010 (connectivity sanity check)
  - `sources_api` — is the UPNP source itself reported available?
  - `upnp_logs` — grep `/var/log` for upnp/AVTransport entries

### Open question
Per the project notes, `UPNP` was previously confirmed **unavailable** in
`/sources` (alongside BLUETOOTH/AIRPLAY/QPLAY). If the firmware gates
AVTransport command effects on source-availability the same way it gates
`/select`, SetAVTransportURI could be accepted at the socket level (or even
return 200 with an empty body — normal for some UPnP actions) while the audio
pipeline ignores it because UPNP isn't the active/available source. The
`sources_api` and verbose curl checks above are aimed at distinguishing
"connection-level failure" from "accepted but ignored".

---

## [0.4.6] — 2026-06-13 (unreleased)  **AVTransport controlURL confirmed**

### Found — true UPnP renderer location
`diagnose_upnp()` revealed:
- Port 8091 is owned by `STSCertified` (PID 1794) and returns 404 — a red
  herring, not the renderer.
- The real `UpnpSource` process (PID 1802) listens on `127.0.0.1:40010` only.
- `/opt/Bose/Xml/MediaRenderer.xml` contains
  `<controlURL>/AVTransport/Control</controlURL>`.

True AVTransport SOAP endpoint: `http://localhost:40010/AVTransport/Control`.

### Configured
- `docker-compose.yml`:
  `BOSE_UPNP_CONTROL_URL=http://localhost:40010/AVTransport/Control` —
  bypasses `_discover_upnp_control_url()` entirely (absolute URL, used as-is).

---

## [0.4.5] — 2026-06-13 (unreleased)  **UPnP controlURL discovery diagnostics**

### Found — `UpnpSource-spotty.xml` has no descriptionURL
Live test of the 0.4.4 pivot failed: `UpnpSource-spotty.xml` only contains
`<Configuration><productName>SoundTouch 20</productName><isSsdpEnabled>true</isSsdpEnabled></Configuration>`
— no `descriptionURL`. The `localhost:8091` descriptor-path fallback probe
also missed, so `_discover_upnp_control_url()` had nothing to parse.

### Added
- **`diagnose_upnp()`** / **`GET /api/diagnose/upnp`** — pinpoints the real
  AVTransport controlURL and the port the renderer listens on:
  - `listening_ports` (`netstat -tlnp` / `/proc/net/tcp`) — confirms the
    actual listening port (may not be 8091).
  - `avtransport_files` + `avtransport_file_dump` — every on-disk XML
    mentioning AVTransport, dumped in full (likely contains the real SCPD/
    device descriptor with `<controlURL>`).
  - `controlurl_grep` — direct grep for `<controlURL>`/`<SCPDURL>`/
    `<serviceType>` across `/opt/Bose` and `/mnt`.
  - `root_8091` / `common_paths_8091` — raw body + HTTP codes for `/` and a
    dozen common AVTransport control paths.
- **`BOSE_UPNP_CONTROL_URL`** env var — manual override that skips discovery
  entirely once the real controlURL is known (absolute or path-only, e.g.
  `/AVTransport/ctrl`). Documented in `docker-compose.yml`, replacing the
  stale (and unused since 0.4.0) `BOSE_UPNP_DESCRIPTOR_URL`.

---

## [0.4.4] — 2026-06-13 (unreleased)  **Pivot to internal UPnP renderer**

### Root cause: `/select` is a dead end on v27 firmware
Even after patching Sources.xml (0.4.2) to remove the dead cloud token,
`/select` with `source="LOCAL_INTERNET_RADIO"` still returns
1005 UNKNOWN_SOURCE_ERROR. The v27 runtime source table hardcodes a block on
this source string independent of Sources.xml — `/select` cannot be used for
internet radio streams on this firmware at all.

### Changed — `play_url()` now injects via UPnP AVTransport (port 8091)
Port 8091 hosts the speaker's internal UPnP MediaRenderer — a completely
separate code path that does not consult Sources.xml or the runtime source
table. `play_url()` now, from inside the speaker via the Telnet shell:
1. Discovers (and caches) the AVTransport `controlURL` by reading
   `/opt/Bose/etc/UpnpSource-spotty.xml` and/or probing `localhost:8091`
   descriptor paths.
2. Writes DIDL-Lite metadata + `SetAVTransportURI`/`Play` SOAP envelopes to
   temp files and `curl`-posts them to `controlURL`.
3. Clears the cached `controlURL` and raises `BoseError` on a SOAP
   `<errorCode>` response (e.g. after a reboot changes the descriptor).

### Added
- **`_discover_upnp_control_url()`**, **`_infer_mime()`**, **`_esc_xml()`**,
  **`_upnp_control_url`** cache, **`_UPNP_PORT`**/`_UPNP_CFG_PATH` constants.

### Removed
- **`_build_select_xml`**, **`_build_store_preset_xml`**, **`_build_stream_json`**,
  **`play_via_upnp_shell`** (merged into `play_url`), `_STREAM_FILE`,
  `xml.sax.saxutils` import.
- `setup_speaker()` no longer writes per-preset JSON descriptor files or calls
  `/storePreset` — both relied on the dead `/select` path. Physical preset
  buttons are now served entirely by the WebSocket listener
  (`run_preset_listener` → `play_url`), which already called `play_url` and
  therefore now uses UPnP automatically.
- `/api/play_upnp` endpoint (main.py) — redundant now that `/api/play_url` is
  UPnP-based.

### Retained (no longer gates playback)
`patch_sources_xml()` and `_configure_bmx_registry()` are still run from
`setup_speaker()` for BMX content-browsing metadata only (so
LOCAL_INTERNET_RADIO appears in the speaker's source list/UI); they are not
required for `play_url()` to work.

---

## [0.4.3] — 2026-06-13 (unreleased)

### Fixed — `remote_services` no longer needs manual re-enabling after reboot
`remote_services on` (the setting that opens Telnet port 23) is volatile and
resets every time the speaker reboots — including reboots triggered by
`setup_speaker()` / `patch_sources_xml()`. Previously this required manually
running `remote_services on` via port 17000 before the app could reconnect.

### Added
- **`_enable_remote_services(host)`** — connects to port 17000 (the Bose
  diagnostic CLI, always reachable regardless of `remote_services` state),
  waits for its `->` prompt, and sends `remote_services on`. Waits 3 s for
  port 23 to come up.
- **`_PORT_17000`** constant.

### Changed
- **`_BoseShell._open()`** — if the connection to port 23 is refused
  (`ConnectionRefusedError`/`OSError`), automatically calls
  `_enable_remote_services()` and retries the port 23 connection once before
  giving up.
- After a successful root login, `_open()` now runs
  `test -f /mnt/nv/remote_services || touch /mnt/nv/remote_services`. The
  firmware checks for this file at boot and enables `remote_services`
  automatically, so port 23 survives future reboots without any manual or
  port-17000 intervention.

---

## [0.4.2] — 2026-06-13

### Fixed — root cause of persistent 1005 UNKNOWN_SOURCE_ERROR

**Root cause confirmed via diagnostic dump:**
The firmware builds its runtime source table from `Sources.xml` at boot, NOT from the
BMX registry (the registry only provides content-browsing metadata).  The factory entry
for `LOCAL_INTERNET_RADIO` carries `secretType="token"` with a cloud-issued device
serial (`{"serial": "b9adc30b-..."}`).  The firmware POSTs this serial to the dead
Bose cloud token-validation endpoint at boot → validation fails → source excluded from
runtime table → every `/select` returns 1005, regardless of `bmxRegistryUrl` state.

**Fix:** Clear `secret=""` and `secretType=""` in Sources.xml — exactly the values
AUX carries.  The firmware then registers the source unconditionally (no cloud call
attempted), making it READY in the runtime table.

### Added
- **`patch_sources_xml()`** — reads `/mnt/nv/BoseApp-Persistence/1/Sources.xml`,
  patches `LOCAL_INTERNET_RADIO` (and `INTERNET_RADIO`) to remove dead cloud tokens,
  sets `account=_BMX_ANON_USER_ID` to align with stored preset `sourceAccount`,
  re-serialises with `ET.indent()`, and writes back via `_write_file` (base64 pipe).
  Idempotent: checks if already patched before returning `True`/`False`.
- **`_SOURCES_XML_PATH`** constant added.

### Changed
- **`setup_speaker()`** now calls `patch_sources_xml()` (step 0a) before
  `_configure_bmx_registry()` (step 0b), combines their reboot flags into a
  single `reboot` + 90 s wait rather than two sequential reboots.

### Architecture clarification
- `Sources.xml` → controls what sources enter the runtime table (boot-time, requires reboot)
- BMX registry → provides content-browsing metadata only; NOT needed for `/select` with explicit `location` URL
- Modifying `Sources.xml` alone is sufficient to unblock `/select` calls

---

## [0.4.1] — 2026-06-12

### Fixed
- **1005 UNKNOWN_SOURCE_ERROR on `/select`** — root cause identified and fixed.
  `LOCAL_INTERNET_RADIO` (BMX source id=11) only exists in the speaker's runtime
  source table after the firmware fetches and parses `bmxRegistryUrl` at boot.
  The factory default URL points to a dead Bose cloud server, so the source is
  never registered and every `/select` call with it returns 1005.

### Added
- **`_configure_bmx_registry()`** in `bose.py` — one-time setup function that:
  1. Reads `/opt/Bose/etc/SoundTouchSdkPrivateCfg.xml` and logs it
  2. Checks idempotently whether `bmxRegistryUrl` already points to our server
  3. Writes `/mnt/nv/OverrideSdkPrivateCfg.xml` (override takes precedence over
     read-only original; original is never modified) with our server's URL
  4. Issues `reboot` via the Telnet shell (required once for the change to take effect)
  Called from `setup_speaker()` before any `storePreset` attempts.
- **`BOSE_SERVER_URL` env var** — external URL of the FastAPI server as seen from
  the speaker (default `http://192.168.100.135:8000`).
- **BMX registry endpoints** in `main.py` (format confirmed from Soundploy's live server):
  - `GET /bmx/registry/v1/services` — registers `LOCAL_INTERNET_RADIO` (id=11) with
    the speaker; served with `askAgainAfter: 86400` so the speaker caches it 24 h
  - `GET /bmx/registry/v1/servicesAvailability` — marks source as online
  - `GET|POST /bmx/orion/token` — static anonymous token for the auth handshake

### Changed
- `setup_speaker()` now calls `_configure_bmx_registry()` first, waits 90 s if a
  reboot was triggered, then proceeds with JSON file writes and `storePreset`.
- `_build_select_xml` and `_build_store_preset_xml` XML payloads are **unchanged** —
  `LOCAL_INTERNET_RADIO` was always the correct source type.

---

## [0.4.0] — 2026-06-12  **Telnet pivot — Soundploy method**

### Removed
- All UPnP/SSDP/SOAP code (`_SSDPProtocol`, `_ssdp_probe`, `_scan_descriptor_paths`,
  `_resolve_descriptor_url`, `_get_upnp_control_url`, `_upnp_soap`, `_build_didl`,
  `_infer_mime`, `_telnet_query_upnp_path`, `_probe_upnp_root`).
- `BOSE_UPNP_DESCRIPTOR_URL` env var (no longer needed).
- `socket`, `urllib.parse` imports.

### Added — core playback engine
- **`_BoseShell`** — persistent async Telnet manager (port 23, `asyncio.open_connection`).
  - Lazy connect + automatic single-retry reconnect on session drop.
  - Per-instance `asyncio.Lock` serialises commands.
  - Unique sentinel string (`BOSE_TS_DONE_7F4A`) detects exact command completion.
  - Auto-runs `rw` after every new login to make the overlay filesystem writable.
- **`_write_file(path, content)`** — writes arbitrary text to the speaker's filesystem
  via `base64 | base64 -d` pipe, avoiding all shell-escaping issues.
  Falls back to `printf '%s'` if BusyBox `base64` is absent.
- **`_build_stream_json(url, name)`** — constructs the `LOCAL_INTERNET_RADIO` JSON
  descriptor (`{"audio":{...},"streamType":"liveRadio"}`) served at `localhost:8080/`.
- **`_build_select_xml(name, file)`** — ContentItem XML for `/select` from localhost.
- **`_build_store_preset_xml(slot, name, file)`** — ContentItem XML for `/storePreset`.

### Added — startup & API
- **`setup_speaker()`** — called once in FastAPI lifespan. Writes `N.json` for every
  configured preset slot and calls `/storePreset` via shell curl so physical buttons
  work autonomously (no WebSocket interception needed).
- **`diagnose()`** — probes hostname, filesystem, processes, curl self-test, base64
  availability. Exposed at `GET /api/diagnose`.

### Changed
- **`play_url(url, name)`** — completely rewritten. Now:
  1. Writes `/opt/Bose/html/ts_current.json` via base64-safe shell command.
  2. POSTs to `localhost:8090/select` via `curl` running INSIDE the speaker —
     bypasses the v27 remote cloud-token gate that blocks the same call from outside.
- **`main.py` lifespan** — calls `setup_speaker()` before starting the WebSocket listener.
- WebSocket listener unchanged; now acts as belt-and-suspenders fallback since
  `storePreset` makes physical buttons work independently.

### Architecture note
Port 8091 is Bose `jamlink` (proprietary multi-room protocol), not a UPnP renderer.
The speaker is `root@spotty` running BusyBox Linux. Root shell is obtained via
`remote_services on` on port 17000, then Telnet port 23 (`root`, no password).
The overlay filesystem is read-only by default; `rw` remounts it writable.

---

## [0.3.2] — 2026-06-11

### Root cause established
Port 8091 is Bose's proprietary **jamlink** multi-room audio protocol. It implements
UPnP internally but at a descriptor path generated at boot and announced exclusively
via SSDP NOTIFY. Docker bridge NAT + WireGuard silently drops the unicast SSDP
response before it reaches the container, making all discovery strategies fail.

### Added
- **`BOSE_UPNP_DESCRIPTOR_URL` env var** — when set, bypasses all SSDP/path-scan
  discovery and uses the given URL directly. Set it using the output of
  `tools/discover.py`. With this set, `play_url` works immediately.
- **`tools/discover.py`** — standalone one-shot SSDP tool. Run from any machine
  on the home LAN (192.168.1.x). Performs active M-SEARCH (multicast + unicast)
  with all relevant ST values, falls back to passive `ssdp:alive` listener (30 s),
  and prints the exact docker-compose.yml env var line to copy-paste.
- **`_telnet_query_upnp_path()`** — connects to the speaker's CLI on TCP port 17000,
  queries known sys commands, and logs the full output. Often reveals the actual
  descriptor path. Acts as Strategy 3 after all other strategies fail.
- **`_probe_upnp_root()`** — fires a background HTTP GET to port 8091 root path and
  logs `Server:` and `Location:` headers for firmware identification.
- All four strategies now fire on each `play_url` call (with caching), and the
  error message contains the exact 4-step fix procedure.

### Changed
- Discovery pipeline extended: Strategy 0 (env var) → Strategy 1 (SSDP concurrent)
  → Strategy 2 (path scan) → Strategy 3 (telnet CLI).
- `docker-compose.yml` documents the discovery procedure inline.
- `deviceID` fetch from port 8090 is skipped when override URL is set.

---

## [0.3.1] — 2026-06-05

### Fixed
- **UPnP 404 on descriptor fetch** — removed the hardcoded `BO5EBO5E-F00D-F00D-FEED-`
  URL convention that only applies to some Bose firmware variants.  Discovery is now
  fully dynamic via a three-strategy pipeline (see below).

### Changed
- **`_get_upnp_control_url` completely rewritten** — replaced one-shot hardcoded URL
  guess with a three-strategy discovery pipeline:
  1. **SSDP unicast** (M-SEARCH sent directly to `{HOST}:1900`; reliable over WireGuard
     since it is pure unicast UDP with no multicast routing requirement)
  2. **SSDP multicast** (M-SEARCH to `239.255.255.250:1900`; may fail across VPN but
     worth attempting; both ST variants — `MediaRenderer:1` and `upnp:rootdevice` —
     are sent simultaneously)
  — Strategies 1 & 2 run concurrently via `asyncio.gather`; first response wins.
  3. **HTTP path scan** (probes 8 known UPnP descriptor paths on port 8091 via plain
     GET requests; zero dependency on SSDP infrastructure)
- **`asyncio.DatagramProtocol`** used for both SSDP probes — fully non-blocking,
  integrates cleanly into the FastAPI event loop.
- **`_validate_stream_url`** added — enforces HTTP (not HTTPS) and rejects playlist
  file extensions (`.m3u`, `.m3u8`, `.pls`, `.xspf`, `.asx`, `.wpl`) with a
  descriptive error message before any UPnP call is attempted.
- **SOAP 404 auto-heals** — `_upnp_soap` now invalidates the cached control URL on
  HTTP 404, so the next `play_url` call transparently re-runs discovery.
- `_make_control_url_absolute` correctly handles both relative and absolute
  `<controlURL>` values in the descriptor XML.
- Added `from urllib.parse import urlparse` (stdlib; no new dependency).

---

## [0.3.0] — 2026-06-05

### Changed
- **`play_url` completely rewritten** — now uses UPnP AVTransport (port 8091) instead
  of the defunct Bose TuneIn/cloud `/select` path killed by firmware v27 (May 2026).
- **UPnP discovery** is deterministic, not SSDP-multicast: `deviceID` is read from
  `:8090/info`, then the descriptor URL `BO5EBO5E-F00D-F00D-FEED-{deviceID}.xml` is
  fetched from `:8091`. The AVTransport `controlURL` is parsed and cached for the
  process lifetime.
- **SOAP two-shot injection**: `SetAVTransportURI` (with DIDL-Lite metadata) followed
  by `Play(Speed=1)` — fully async via httpx, zero new blocking dependencies.
- **FastAPI `lifespan`** replaces the old startup/shutdown hooks in `main.py`.
- **Version bumped** to 2.0.0 in FastAPI app metadata.

### Added
- **WebSocket preset listener** (`run_preset_listener` in `bose.py`): connects to
  `ws://speaker:8080` with subprotocol `gabbo`, parses `nowSelectionUpdated` XML
  events, and fires `play_url()` with the configured stream URL for that slot.
  Reconnects automatically with exponential backoff (5 s → 60 s ceiling).
- **`PRESET_N_URL` / `PRESET_N_NAME`** environment variables (N = 1–6) in
  `docker-compose.yml` for per-slot stream configuration.
- **`websockets>=13.0`** added to `requirements.txt`.
- **MIME-type inference** in DIDL-Lite builder (mp3/aac/ogg/flac/m4a from URL extension).

### Removed
- `_tunein_id()` helper — TuneIn station IDs are no longer usable; direct HTTP stream
  URLs are required.

### Notes
- Stream URLs **must be HTTP** (not HTTPS). Bose firmware TLS root certs are expired.
- Preset stream URLs in `docker-compose.yml` are placeholders — verify actual stream
  URLs for each station before deploying.

---

## [0.2.0] — 2026-06-05 (superseded by 0.3.0, not deployed)

### Added
- **TuneIn proxy** (`tunein-proxy/proxy.py`) — FastAPI container that intercepts
  the speaker's `opml.radiotime.com` calls and transparently forwards them to the
  real TuneIn OPML API, bypassing the defunct Bose cloud backend.
- **HTTPS → HTTP downgrade** in proxy responses — Bose firmware (older root
  certs) cannot handle HTTPS stream URLs; the proxy patches them to HTTP in-flight.
- **Dockerfile.proxy** — separate Docker image for the TuneIn proxy on port 80.
- **docker-compose.yml** updated to orchestrate both the main app and the proxy.
- Home router DNS override: `opml.radiotime.com` now resolves to the Office Server
  (`192.168.100.135`) so the speaker's TuneIn requests hit the local proxy.

### Known blocker
- Port 80 on the Office Server is occupied (PID 17836 — likely Docker Desktop's
  port proxy). Needs resolution before the proxy container can be deployed.

---

## [0.1.0] — 2026-05-01

### Added
- **FastAPI backend** (`app/main.py`) with full async httpx client for the Bose
  SoundTouch 20 XML REST API (port 8090).
- **`app/bose.py`** — typed async wrappers for `/now_playing`, `/volume`,
  `/presets`, `/key`, `/select`, `/sources`.
- **REST API endpoints:**
  - `GET /api/status` — now-playing info + volume, polled every 3 s by the UI
  - `GET /api/presets` — all 6 preset slots with names
  - `POST /api/key/{name}` — any key command (PLAY, PAUSE, MUTE, NEXT_TRACK, …)
  - `POST /api/volume` — set level 0–100
  - `POST /api/source` — switch source (AUX, SPOTIFY, etc.)
  - `POST /api/play_url` — play a TuneIn station by ID (blocked by Bose cloud; fixed in 0.2.0)
- **Dark web UI** (`app/static/index.html`) — single-page control panel served
  directly by FastAPI; no external dependencies.
- **Dockerfile** — Python 3.11-slim image, port 8000.
- **WireGuard VPN tunnel** between Home (192.168.1.0/24) and Office
  (192.168.100.0/24) networks via Keenetic KN-3610 routers.
  - Tunnel subnet: 172.16.82.0/24
  - Office peer: 172.16.82.1 | Home peer: 172.16.82.2, port 16632
  - PersistentKeepalive = 25 on Home side

### Fixed
- Keenetic Home router firewall blocking tunnel traffic — resolved via two-step
  fix: `interface WireGuard0 security-level private` in CLI **and** an explicit
  Allow rule in the Web UI firewall for the VPN segment.

### Notes
- Bose SoundTouch 20 confirmed on port 8090 (not 8080).
- BLUETOOTH, AIRPLAY, UPNP not available on this firmware build.
- Spotify works independently (no Bose cloud required).
