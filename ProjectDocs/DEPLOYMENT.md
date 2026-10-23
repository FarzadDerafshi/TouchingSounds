# Deployment & operations

## At a glance

| | |
|---|---|
| Server | Office Server, `192.168.100.51`, Windows + Docker Desktop |
| Folder | `D:\apps\TouchingSounds` (a git checkout of `main` since 1.0.0) |
| Container | `touchingsounds`, port 8000 (`docker-compose.yml`) |
| Settings | `D:\apps\TouchingSounds\.env` (gitignored; template `.env.example`): speakers, `BOSE_SERVER_URL`, preset defaults |
| Persistent data | `D:\apps\TouchingSounds\data` (bind mount, gitignored) |
| Release | Telegram `/release` (runs `ProdRelease.bat`), or double-click it in that folder |
| Backups | Nightly 02:05, `data\` only, encrypted 7z, OK/FAILED e-mail (server task, since 2026-10-20); plus a copy before each release |
| Logs | `docker compose logs -f touchingsounds`; release logs in `deploy_log_*.txt` |

## Releasing

1. On the dev PC: commit and push to `main`.
2. Release, either way:
   - **Telegram:** `/release` in the Mira Olives bot → TouchingSounds → "✅ Yes, release".
     The server runs `ProdRelease.bat` unattended within a minute and the bot reports the
     result. If the pull updated the script, the runner runs it a second time automatically.
     How it works: `_Office Server Release System.md` (OneDrive, IT, Web and Mail Hosting);
     the master copy is in the Mira AI Department repo, `ProjectDocs/RELEASE-SYSTEM.md`.
   - **On the server:** double-click `D:\apps\TouchingSounds\ProdRelease.bat` and accept
     the admin prompt.

The script:

| Step | What it does |
|---|---|
| 1/5 | Copies `data\` to `DataBackup\data_<timestamp>` (aborts if that fails) |
| 2/5 | Makes the folder match `origin/main` exactly (`git fetch` + `git checkout -f -B main origin/main`), not a merge. Edits to tracked files made on the server are saved first as `DataBackup\server_edits_<timestamp>.patch`. A folder without `.git` is turned into a checkout. Stops if this changed `ProdRelease.bat` itself; just run it again. |
| (before 3/5) | If `.env` is missing, writes it from the running container's settings (`docker inspect`); stops if it can't. |
| 3/5 | `docker compose up -d --build` with `GIT_COMMIT` set, so the app knows its commit |
| 4/5 | Waits up to 4 minutes for `/api/version` and checks the commit matches |
| 5/5 | Asks the speaker for its status, which warns if the WireGuard tunnel is down |

On startup the app checks the speaker's settings. If anything needed changing, it reboots
the speaker once and waits about 90 s before serving, which is why step 4 waits up to
4 minutes. Normally nothing changes and the app is up in a few seconds.

### Why a hard checkout and not `git pull`

The server is deploy-only: code changes are made on the dev PC and pushed. Before 1.0.0
the folder was updated by manual copies on top of an old checkout (`.git` at `4a69169`),
so on 2026-10-19 `git pull` refused with "local changes would be overwritten". Making the
folder match `main` exactly avoids that whole class of failure. Anything edited on the
server isn't lost: it's in the `server_edits_*.patch` file. Untracked files that `main`
doesn't track (`data\`, backups, logs) are never touched.

### Git on the server

Git for Windows 2.55 is installed at `C:\Program Files\Git` (on the machine PATH). It
used to live in `D:\TrExpress\Installed Env\Git`. On 2026-10-19 that folder was deleted
by accident and every project's `ProdRelease.bat` failed with `'git' is not recognized`.
Git was then reinstalled on its own with `winget install --id Git.Git -e --location
"C:\Program Files\Git"`. The GitHub login is stored in Windows, not in the Git folder,
so it survived. `ProdRelease.bat` also checks the standard Git and GitHub Desktop
folders if `git` isn't on PATH.

### Rolling back a release

```powershell
cd D:\apps\TouchingSounds
git log --oneline -5                 # pick the commit to go back to
git checkout <commit>
docker compose up -d --build
```

Go back to `main` with `git checkout main` before the next `ProdRelease.bat`. If data
needs restoring, copy the files back from `DataBackup\data_<timestamp>\`.

## Cloudflare tunnel

Goal: https://touchingsounds.example.com serves the app, for phone use outside the LAN
and so the app can be installed (PWAs need https).

1. **Public hostname.** Cloudflare Zero Trust → Networks → Tunnels → the tunnel served by
   the `cloudflared` container on the Office Server → *Public hostnames* → Add:
   - Subdomain `touchingsounds`, domain `example.com` (your own)
   - Service `HTTP`, URL `host.docker.internal:8000`, since `cloudflared` runs in Docker
     on the same machine; `192.168.100.51:8000` works too.
2. **Protect it with Cloudflare Access. Do this before sharing the link.** Without it,
   anyone who finds the hostname can control the speaker. Zero Trust → Access →
   Applications → Add → *Self-hosted*:
   - Application domain: `touchingsounds.example.com`
   - Policy: *Allow*, Include → *Emails* → the household's addresses (one-time PIN by
     email works without any identity provider)
   - Session duration: e.g. 1 month, so the installed app doesn't ask for a code often
3. Open https://touchingsounds.example.com, log in, check that the speaker status
   shows. Then install it (see [USER-MANUAL.md](USER-MANUAL.md#install-as-an-app)).

The app itself hides `/marge`, `/bmx`, `/relay`, diagnostics and API docs from requests
that come through Cloudflare (see [DECISIONS.md](DECISIONS.md)). The speaker always uses
the LAN address, so the tunnel never sits in its path.

## The speaker side

Speakers are listed in `data/devices.json` (since 1.5), which the app's speakers wizard
writes. `BOSE_DEVICES` in `.env` only seeds that file on the first start
and is ignored afterwards. See [ARCHITECTURE.md](ARCHITECTURE.md#devices) for which
device is set up how.

### Adding a speaker

In the app: speaker button → **Add or manage speakers** (see the
[user manual](USER-MANUAL.md#adding-a-speaker)). No release needed. Speakers added there
are set up through the port-17000 console (`fresh`) and get a fresh source list.
Reserve the speaker's IP in the router afterwards: the app knows speakers by address.

The server searches the /24 of every configured speaker. For another subnet, set
`TS_SCAN_SUBNETS` (comma-separated CIDRs, e.g. `192.168.1.0/24,192.168.100.0/24`).

A speaker whose own Spotify/Alexa logins should be kept needs the root-shell route
instead: add it to `data/devices.json` by hand with `"fresh": false` (it needs
`remote_services`) and restart the container.

### SoundTouch 20 (root-shell setup)

| What | Where |
|---|---|
| Cloud redirects | `/mnt/nv/OverrideSdkPrivateCfg.xml` on the speaker: `margeServerUrl` and `bmxRegistryUrl` → `http://192.168.100.51:8000` |
| Source list | `/mnt/nv/BoseApp-Persistence/1/Sources.xml`; the original is kept in `data\Sources.xml` |
| Shell | `telnet 192.168.1.49 17000` (Bose debug shell; `remote_services on` enables telnet/ssh as root) |

**If the server's IP ever changes:** update `BOSE_SERVER_URL` in `.env` and
release. On startup the app rewrites the override and reboots the speaker. (On
2026-10-19 the address was wrong, `.135` instead of `.51`, and the speaker got no answer
at all; see the 0.6.0 changelog.)

**Speaker rollback to stock behaviour** (radio stops working, Spotify/Bluetooth/AUX
unaffected):
```sh
rw
rm /mnt/nv/OverrideSdkPrivateCfg.xml
reboot
```
If Spotify ever disappears, copy `data\Sources.xml` back to
`/mnt/nv/BoseApp-Persistence/1/Sources.xml` and reboot.

### Wireless Link Adapter (console setup)

Set up through the port-17000 console (`telnet 192.168.100.150 17000`, or PuTTY "raw").
Check the current URLs with `getpdo CurrentSystemConfiguration`.

**Rollback to Bose's (dead) URLs**, typed in that console in this order:
```
sys configuration bmxRegistryUrl "https://content.api.bose.io/bmx/registry/v1/services"
sys configuration margeServerUrl "https://streaming.bose.com"
envswitch boseurls set "https://streaming.bose.com" "https://worldwide.bose.com/updates/soundtouch"
sys reboot
```
Then remove it from the app's speakers list, or the next start redirects it again. (The
wizard's *Remove* with *Also put back its original settings* does all of this for a
speaker added from the app.)

## Troubleshooting

| Symptom | Check |
|---|---|
| App says the speaker is offline (red dot) | Home speaker: is the WireGuard tunnel up? `curl http://192.168.1.49:8090/info` from the server. Office adapter: is it powered and on Wi-Fi? |
| Station won't play / error 1005 | `http://192.168.100.51:8000/api/diagnose/bmx`: `reach_fastapi` must be 200; LOCAL_INTERNET_RADIO must be in `sources_api`. If not, reboot the speaker with the tunnel up. |
| HTTPS/HLS station silent | `docker compose logs touchingsounds`, look for `relay: start` / `relay: stop`. ffmpeg fails fast on dead streams. |
| Search shows "unavailable" | radio-browser.info is down or unreachable from the server; it tries three mirrors. |
| Logos missing | Some logo hosts (e.g. TuneIn's CDN) don't answer from every network; the app shows a radio icon instead. |
| `marge: UNHANDLED` in logs | The speaker asked for a marge endpoint we don't emulate. Usually harmless; note the path. |
| `marge: request for unknown account` in logs | A speaker asked for an account no configured device has. Check its `/info` account and `BOSE_DEVICES`. |
| Adapter radio fails after a release | Logs: `Device 192.168.100.150: …`. If the console steps ran but radio still fails, power-cycle it with the server up. |
| Release stopped at "[STOP] This pull updated ProdRelease.bat" | Expected. Run it again. |
| App shows a speaker offline, but it plays and answers from a PC | A Docker network on the server overlaps that speaker's subnet, so containers route its traffic into a bridge. On the server: `docker network ls -q \| % { docker network inspect $_ --format "{{.Name}}  {{range .IPAM.Config}}{{.Subnet}} {{end}}" }` and look for `192.168.x` ranges covering `192.168.1.0/24` or `192.168.100.0/24`. Fix it by pinning that network's subnet (to `10.x`) in its project. See the 2026-10-19 log entry. |

## Log

- **2026-10-19:** 0.6.0 deployed (manual copy); radio playing after the server IP fix.
  Presets 1-3 set: Radyo Voyage, Number One FM (via relay), Europa Plus.
- **2026-10-19:** 1.0.0 adds `ProdRelease.bat`. First attempts failed: (1) `'git' is not
  recognized`, because the Git install under `D:\TrExpress` had been deleted; restored.
  (2) `git pull` refused because of the old checkout plus manual copies; the script now
  does a hard checkout and saves server edits as a patch.
- **2026-10-19:** Cloudflare public hostname touchingsounds.example.com set up with
  Cloudflare Access (no public access).
- **2026-10-19:** 1.0.0 live (`238ee51`) after the `ProdRelease.bat` fixes. Checked on
  desktop Chrome and Safari on iPhone 13 Pro: playback, favorites and save-to-preset work.
  Git moved to `C:\Program Files\Git` (standalone install).
- **2026-10-19:** Wireless Link Adapter "OfficeSounds" added at 192.168.100.150 (Wi-Fi via
  the Bose app, IP reserved). Read-only checks: FW 26.0.1, no `remote_services`,
  `getpdo`/`sys configuration` present, previous owner's account. 1.1.0 adds it.
- **2026-10-19:** 1.1.0 live (`9209bac`). The console setup ran and rebooted the adapter, and it
  came back with `margeURL` = our server about 1.5 min after the release. Its sources:
  LOCAL_INTERNET_RADIO **READY**, AUX READY, and the previous owner's Pandora/Amazon/Spotify
  logins are gone. Plays http streams (NPO Radio 1) and https/HLS through the relay
  (Number One FM), independently of the SoundTouch 20. So FW 26.0.1 works with the marge
  stand-in.
- **2026-10-19 (evening) / 2026-10-20:** The app showed "F&F Touching Sounds" offline
  while it kept playing Radyo Voyage. The speaker could reach the server, and the server
  host reached the speaker (200), but connections from containers to `192.168.1.49` timed
  out. **Cause:** Mira AI Department's new `proxynet` network (n8n ↔ Docker socket proxy,
  commit `dca35a4`) had no subnet set, and with every `172.17`–`172.31` range already
  used on the server, Docker gave it `192.168.0.0/20`. That range contains the home LAN,
  so every container sent `192.168.1.x` traffic into that internal bridge. **Fix**, in
  Mira AI Department: `proxynet` pinned to `10.201.0.0/24` and recreated on release. No
  TouchingSounds change. Checked 2026-10-20: the speaker is online in the app and
  `/api/status` answers in 0.17 s. **Avoid:** Docker networks on this server must not
  use `192.168.0.0/16` (home `192.168.1.x`, office `192.168.100.x`) or `172.16.82.0/24`
  (WireGuard). The adapter
  "OfficeSounds" was unplugged at the time, which is why it showed offline too.
- **2026-10-20:** First release from Telegram `/release` (33 s), picking up the
  `ProdRelease.bat` fix `2cdc61f`. A nightly encrypted backup of `data\` was set up on
  the server (the old backup target was a stale copy of the code, without `data\`).
- **2026-10-21:** Docker Desktop `default-address-pools` set to
  `[{"base": "10.200.0.0/16", "size": 24}]`, done from another Claude Code session, no
  issues reported. New Docker networks that don't pin a subnet now get a `10.200.x.0/24`,
  never a `192.168.x` range. Existing networks kept their subnets.
- **2026-10-21:** 1.2.0 live (`bcf7c90`), released from Telegram `/release`.
  `/api/version` reports 1.2.0; `/api/sources` on the SoundTouch 20 lists AUX (READY)
  and Bluetooth (UNAVAILABLE, nothing paired), so the app shows those two source
  buttons. Before the release the UI was tested in Chrome against fake speakers; that
  found and fixed tall empty preset tiles. The adapter was unplugged, so its real
  source list is still to be checked.
- **2026-10-21:** 1.3.0 live (`5348e3b`): song titles and covers, sleep timer, alarms.
  This release adds the `tzdata` package (`requirements.txt`) and a new data file,
  `data/alarms.json`. The user checked it on the SoundTouch 20: songs and covers show,
  the sleep timer goes to standby, and an alarm test woke the speaker. The first real
  morning alarm (2026-10-22) worked as planned.
- **2026-10-22:** 1.3.1 live (`0ae1c9e`): the theme follows the phone in the installed
  app. Confirmed by the user on an iPhone (PWA, light mode).
- **2026-10-22:** 1.5.0 live (`d6f5831`, with 1.4.1): the speakers wizard. On start the
  app created `data/devices.json` from `BOSE_DEVICES`; `/api/devices` lists both speakers
  as before. The wizard itself is not yet tried on a real speaker.
- **2026-10-22:** 1.4.0 live (`d32dd47`), released from Telegram `/release`: full-screen
  cover with Share / WhatsApp / find-the-song links, and the top-bar fix (1.3.2 was
  folded into this release). No new packages or settings. Confirmed by the user on
  the iPhone.
- **2026-10-23:** The GitHub repo went public (MIT licence) with a fresh, single-commit
  history; the private history is kept as a git bundle outside the repo. Server
  settings moved from `docker-compose.yml` to a gitignored `.env`, which the first
  `ProdRelease.bat` run after the switch writes from the running container.
- **2026-10-23:** 1.5.1 live (`568e80a`), released from Telegram `/release`, the first
  release from the new public repo (same URL, so the server's checkout needed no change;
  the hard checkout took the unrelated history in its stride). `.env` was created
  from the running container on that run. No new packages.
- **2026-10 (earlier):** v0.5.x deploy failed. The likely cause was the now-removed
  `tunein-proxy` service failing on port 80; this was never confirmed.
