# Deploying 0.6.0 — local marge server

Restores internet radio, including any `http://` stream URL, on the
SoundTouch 20 using our own stack. No third-party install on the speaker.
For background see CHANGELOG `[0.6.0]`. For the third-party alternative
see [AFTERTOUCH_INSTALL_PLAN.md](AFTERTOUCH_INSTALL_PLAN.md).

> ⚠️ The **v0.5.x deployment failed** and the cause isn't known yet. One
> suspect is the `tunein-proxy` service failing on port 80, which is now
> opt-in. Watch the output of step 3 closely.

---

## What happens on first start

1. **Snapshot.** The container copies the speaker's `Sources.xml` (with the
   Spotify/Alexa secrets) to `D:\apps\TouchingSounds\data\Sources.xml`.
2. **Patch.** `Sources.xml` LOCAL_INTERNET_RADIO is set to the canonical
   token.
3. **Redirect.** `/mnt/nv/OverrideSdkPrivateCfg.xml` points both
   `margeServerUrl` and `bmxRegistryUrl` at `http://192.168.100.51:8000`.
   **marge is only redirected if step 1 succeeded.**
4. **One reboot.** The container waits 90 s. At boot the speaker asks our
   marge for its account and gets a source table that includes
   LOCAL_INTERNET_RADIO.

---

## Deploy (Office Server, `D:\apps\TouchingSounds\`)

1. Copy these files from the repo. This is a manual copy; that folder is
   not a git checkout.
   - `app\bose.py`, `app\main.py`, `app\marge.py` (new), `app\server.py` (new)
   - `Dockerfile`, `docker-compose.yml`
   - optionally `app\static\*` (the 0.5.x UI)
2. Check `PRESET_N_URL` in `.env`. These URLs came from an
   earlier session and haven't been confirmed to play. Look each station
   up on https://www.radio-browser.info and
   use its direct `http://` stream URL (not `https://`, not `.m3u`/`.pls`).
3. Rebuild. A plain `restart` isn't enough.
   ```powershell
   docker compose up -d --build
   docker compose logs -f touchingsounds
   ```
4. In the logs, expect:
   - `Sources.xml snapshot saved to /app/data/Sources.xml`
   - `Override written:` followed by both `margeServerUrl` and `bmxRegistryUrl` lines
   - `Rebooting speaker …`, then a 90 s wait
   - after the reboot: **`marge: full account <account id> requested — serving N sources`**.
     That's the speaker talking to us.

   If you see `marge redirect withheld`, the snapshot failed. Nothing
   risky happened. Fix shell access, then restart the container.

---

## Verify

1. `http://192.168.100.51:8000/api/diagnose/sources_raw`:
   **LOCAL_INTERNET_RADIO** should now appear in the list.
2. Play a stream from the web UI (the "play URL" field), or:
   ```powershell
   curl.exe -X POST http://192.168.100.51:8000/api/play_url -H "Content-Type: application/json" -d '{\"url\":\"http://<stream>\",\"name\":\"Test\"}'
   ```
   The logs should show `BMX: station 'Test' -> http://…`.
3. **Check Spotify still works** (play something from the Spotify app).
4. Short-press preset buttons 1–6.

### If something's missing
Search the logs for `marge: UNHANDLED`. Each line is an endpoint the
speaker wanted that we don't emulate yet. Paste them into a Claude session.

---

## Rollback (back to the 0.5 state)

On the speaker (telnet/SSH as root):
```sh
rw
rm /mnt/nv/OverrideSdkPrivateCfg.xml
reboot
```
That points the speaker back at the dead Bose cloud, which is today's
behavior. If Spotify broke, also restore the original sources from the
snapshot. Copy `D:\apps\TouchingSounds\data\Sources.xml` back to
`/mnt/nv/BoseApp-Persistence/1/Sources.xml`, then reboot.

> Keep `D:\apps\TouchingSounds\data\Sources.xml`. After the redirect, it's the
> only copy of the speaker's original source list.

---

## Known limits

- **Depends on the WireGuard tunnel at speaker boot.** If the tunnel or the
  Office Server is down when the speaker starts, it gets no source table.
  Radio stays unavailable until the next reboot with the tunnel up.
- **Only LOCAL_INTERNET_RADIO is served.** No TuneIn or Radio Browser
  browsing; you play stream URLs directly.
- **Stream URLs must be plain `http://`.** The firmware's TLS root
  certificates are expired.
