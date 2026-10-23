# TouchingSounds

Internet radio for Bose SoundTouch speakers again, after Bose shut down the SoundTouch
cloud in May 2026.

Without that cloud a SoundTouch speaker can't play internet radio any more: the presets
go silent and every station fails. TouchingSounds is a small server you run at home
that stands in for the parts of the Bose cloud the speaker needs, plus a web app
(phone, tablet, desktop, installable) to control it:

- play any internet radio station (search of radio-browser.info, https and HLS streams
  included), favorites, recent stations
- put stations on the six preset buttons, which then work on the speaker itself again
- song titles and covers, sleep timer, alarms
- several speakers in one app, with an "Add a speaker" wizard

Bluetooth, AUX and (where it still works) Spotify are not affected.

## What you need

- A Bose SoundTouch speaker on your network. Tested on a **SoundTouch 20** (firmware
  27.0.6) and a **Wireless Link Adapter** (firmware 26.0.1). Other SoundTouch models
  use the same software and should work, but haven't been tried.
- An always-on machine with **Docker** on the same network (a PC, a NAS, ...). The
  speaker must be able to reach it on port 8000, and it must reach the speaker (ports
  8090, 8080, 17000 and, on firmware that has it, 23).

## Quick start

```sh
git clone https://github.com/FarzadDerafshi/TouchingSounds.git
cd TouchingSounds
cp .env.example .env        # Windows: copy .env.example .env
```

Edit `.env`:

- `BOSE_DEVICES`: your speaker's IP address (see its network settings in your router;
  reserve that address so it doesn't change)
- `BOSE_SERVER_URL`: the address the speaker reaches this machine at, e.g.
  `http://192.168.1.10:8000` (not `localhost`)

Then:

```sh
docker compose up -d --build
```

Open `http://<this machine>:8000` in a browser. On the first start the server points
the speaker at itself and restarts it once (about 90 seconds). After that, search for a
station and play it. More speakers can be added from the app (speaker button →
**Add or manage speakers**).

## What it changes on your speaker

On startup the server changes two settings on the speaker, the addresses of Bose's
account service (`margeServerUrl`) and music-service registry (`bmxRegistryUrl`), so they
point at your server instead of Bose's dead ones. Depending on the firmware this goes
through the speaker's own debug console (port 17000) or its root shell. Before the
change, the speaker's source list (including any Spotify/Alexa logins) is saved to
`data/Sources.xml`. Keep that folder.

To undo it, see *Speaker rollback* in
[ProjectDocs/DEPLOYMENT.md](ProjectDocs/DEPLOYMENT.md#the-speaker-side).

## Security

The app has **no login of its own**: anyone who can open port 8000 can control your
speakers. Keep it on your home network. To use it from outside, put something with a
login in front of it (the author uses a Cloudflare tunnel with Cloudflare Access; see
[DEPLOYMENT.md](ProjectDocs/DEPLOYMENT.md#cloudflare-tunnel)). Requests that arrive
through Cloudflare can't reach the speaker-facing and diagnostic endpoints.

## Documentation

[ProjectDocs/](ProjectDocs/README.md) has the architecture, the design decisions, the user
manual and the operations notes. They describe the author's own setup (two speakers in
two locations joined by WireGuard, a Windows server released with `ProdRelease.bat`),
so addresses and scripts there are examples; yours come from `.env`.
[CHANGELOG.md](CHANGELOG.md) records every version, including the dead ends on the way.

## Disclaimer

An independent hobby project, not affiliated with or endorsed by Bose Corporation.
"Bose" and "SoundTouch" are trademarks of Bose Corporation. It changes settings on your
speaker; use it at your own risk (see the [licence](LICENSE)).

## Licence

[MIT](LICENSE): use it, change it, share it; keep the copyright notice.
