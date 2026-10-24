# Roadmap

## Next

- **Try 1.5 on a real speaker** (a spare or second-hand one, not the home SoundTouch 20,
  since setup mode resets it): the cable path's Wi-Fi scan and join, the phone
  hand-over in setup mode on an iPhone, and the SoundTouch 30, 10 and Link Adapter button
  drawings (marked as unchecked in the app). Safest first test: remove OfficeSounds in the
  app and add it again (it's on Wi-Fi, so no reset is needed).
- **Re-adding a speaker the app already knows** (e.g. the home SoundTouch 20 after a factory
  reset): the wizard lists it under "Already added" and the address field refuses it, so
  the takeover can't be started from the app. The primary can't be removed either. Until
  that's handled, a reset primary is set up again by restarting the container; and its
  root-shell route needs `remote_services`, which a reset may turn off.
- **Remote deploys from the dev PC.** Releases can be started from Telegram `/release`
  since 2026-10-20, but a Claude Code session still can't start one or read the server's
  logs. OpenSSH Server on the Office Server (key login only) would allow that.
- **Browse by genre/country** in Search (radio-browser has tag and country lists), next
  to the name search.
- **Clear a preset slot** from the app. Not done yet because the firmware's remove-preset
  call hasn't been verified on this speaker; slots can be overwritten meanwhile.

## Later / ideas

- Alarm snooze, and a gentler wake-up option (longer rise).
- Song titles for Number One FM and Europa Plus, if a now-playing source turns up
  (Europa Plus's site API has a playlist call that came back empty).
- Lock-screen media controls on phones (Media Session API), so volume/stop work without
  opening the app. Probably not feasible: phones only show these controls while the page
  itself plays audio, and here the speaker plays, not the phone.
- Link your own Spotify to the adapter (its previous owner's login was removed).
- Group the two speakers (multiroom), if the firmware allows it across the tunnel.

## Done

- 1.5.3 (2026-10-24, live `731ab8a`): new Pulse Bars app icon and logo ("Touching" light, "Sounds" bold).
- 1.5.2 (2026-10-23, live `9e73f9a`): search results scroll on iPhone without the bottom bars floating mid-screen.
- 1.5.1 (2026-10-23, live `568e80a`): public repo (MIT), settings in a gitignored `.env`, root README.
- 1.5.0 (2026-10-22, live `d6f5831`): "Add a speaker" wizard (find, cable + Wi-Fi from the
  speaker's own scan, setup-mode guide per model, phone hand-over), automatic takeover,
  Change Wi-Fi, Remove; speakers in `data/devices.json`.
- 1.4.1 (2026-10-22, live with 1.5.0): the cover can't open the previous station's song while a new one tunes in.
- 1.4.0 (2026-10-22, live `d32dd47`): full-screen cover with Share / WhatsApp / find-the-song links;
  top-bar speaker name no longer overlaps the theme button.
- 1.3.1 (2026-10-22, live `0ae1c9e`): light/dark follows the phone in the installed app.
- 1.3.0 (2026-10-21, live `5348e3b`): song titles and covers for internet radio (station feeds, ICY),
  sleep timer, alarms.
- 1.2.0 (2026-10-21, live `bcf7c90`): UI polish: speaker-unreachable state, controls and sources that
  match the device, empty presets pick a station, auto theme, instant "Tuning in…",
  logo-coloured player and cleaner logo framing.
- 1.1.0 (2026-10-19): several speakers in one app (device picker), Wireless Link Adapter
  via console setup.
- 1.0.0 (2026-10-19): station search, favorites, recent, save to preset, logos,
  responsive layout, PWA, Cloudflare guard, `ProdRelease.bat`, ProjectDocs.
- 0.6.0 (2026-10-19): internet radio works again (marge stand-in, https/HLS relay).
