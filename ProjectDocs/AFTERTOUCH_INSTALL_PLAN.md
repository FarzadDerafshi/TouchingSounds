# AfterTouch Installation Plan — Bose SoundTouch 20

Researched 2026-10-18. Restores internet radio, including **any stream URL**,
and the six hardware presets on the SoundTouch 20 after Bose's cloud shutdown
on 2026-05-06.

> **Status:** plan only, not executed. Read the whole document before starting.

---

## Speaker facts (from `GET http://192.168.1.49:8090/info`)

| Field | Value |
|---|---|
| Model | SoundTouch 20 |
| Name | F&F Touching Sounds |
| Firmware | **27.0.6.46330.5043500** (final Bose build, the exact one AfterTouch was verified on) |
| IP | 192.168.1.49 (Home LAN) |
| margeURL | `https://streaming.bose.com` ← dead Bose cloud, the root cause |

---

## Why this works when TouchingSounds 0.4.x didn't

At boot, the speaker asks Bose's **marge** account server (`margeServerUrl`)
which sources the account has. Marge is dead, so the source table stays empty
and every `/select` of TUNEIN or LOCAL_INTERNET_RADIO returns **1005**.

TouchingSounds redirected `opml.radiotime.com`, `bmxRegistryUrl` and patched
`Sources.xml`. Those are all secondary services. We never redirected or
emulated **marge**. AfterTouch emulates marge and pairs the speaker to a local
account (`margeAccountUUID`). Its docs say it outright: *"Radio error 1005:
margeAccountUUID is still empty."*

---

## Project comparison

| | **AfterTouch** (primary) | **SoundTouch Reborn / STR** (plan B) |
|---|---|---|
| Repo | https://github.com/gesellix/bose-soundtouch | https://github.com/JRpersonal/streborn |
| License | MIT | Apache-2.0 (MIT ≤ v1.0.12) |
| Cost | Free (optional GitHub Sponsors) | Free (donation-funded, no tiers) |
| Network calls | Update check off by default | Optional version check to st-reborn.de (`STR_NO_UPDATE_CHECK=1` disables it), radio-browser.info for search |
| SoundTouch 20 | ✅ Verified on hardware 2026-08-16, FW 27.0.6 | ✅ Contributor-confirmed |
| Install style | SSH + shell script on speaker | Signed Windows GUI app, over Wi-Fi |

**Possible costs:** none in money. An optional micro-USB OTG adapter (about €3)
is needed only if SSH can't be enabled over the network.

**Risks:** both projects run as root on the speaker, and the speaker has only
about 31 MB of flash (`/mnt/nv`). The code has not been security-audited by us.

---

## Phase 0 — Remove our old setup (do not skip)

1. **Stop TouchingSounds on the Office Server** (`D:\TouchingSounds`):
   ```powershell
   docker compose down
   ```
   `setup_speaker()` in `app/bose.py` runs on every container start. It
   re-patches `Sources.xml` and the override XML and can reboot the speaker,
   which would undo AfterTouch.

2. **Remove the DNS override on the Home router** (Keenetic CLI):
   ```
   no ip host opml.radiotime.com
   system configuration save
   ```

3. **Save the current presets:** open `http://192.168.1.49:8090/presets` in a
   browser and save the XML.

---

## Phase 1 — Root SSH to the speaker

Run these from a Windows PC on the Home LAN, or from the Office over WireGuard.

4. Try SSH directly:
   ```powershell
   ssh -oHostKeyAlgorithms=+ssh-rsa root@192.168.1.49
   ```
5. If it's refused, download `soundtouch-cli-<ver>-windows-amd64.exe` from
   https://github.com/gesellix/Bose-SoundTouch/releases/latest and enable SSH
   over port 17000:
   ```powershell
   .\soundtouch-cli.exe --host 192.168.1.49 setup enable-ssh
   ```
6. **Last resort:** put an empty file named `remote_services` on a FAT32 USB
   stick, plug it into the speaker through the OTG adapter, then power-cycle.
   You then get root SSH with no password.

---

## Phase 2 — Undo TouchingSounds changes on the speaker

7. On the speaker:
   ```sh
   rw
   cp /mnt/nv/OverrideSdkPrivateCfg.xml /mnt/nv/OverrideSdkPrivateCfg.xml.ts-backup && rm /mnt/nv/OverrideSdkPrivateCfg.xml
   cp /mnt/nv/BoseApp-Persistence/1/Sources.xml /mnt/nv/Sources.xml.ts-backup
   df -h /mnt/nv
   ```
   Leave `Sources.xml` in place. Revisit it only if AfterTouch's Health tab
   complains.

---

## Phase 3 — Install AfterTouch

8. Read the installer first:
   https://raw.githubusercontent.com/gesellix/Bose-SoundTouch/main/scripts/on-device-install/install.sh
   Then install a **pinned** version:
   ```sh
   rw && curl -sSL https://raw.githubusercontent.com/gesellix/Bose-SoundTouch/main/scripts/on-device-install/install.sh | sh -s -- --version <latest>
   wget -qO- http://localhost:8000/health
   sync
   reboot
   ```
9. Wait 2–3 minutes, then open the admin UI at **http://192.168.1.49:17008**.
   On the ST20, port 8000 is blocked from the LAN, so the installer forwards
   17008 to it. Check this with `/etc/init.d/aftertouch status`.
   Fallback is an SSH tunnel:
   ```powershell
   ssh -oHostKeyAlgorithms=+ssh-rsa -L 8000:localhost:8000 root@192.168.1.49
   ```
   Then browse to http://localhost:8000. The tunnel is also required for
   Spotify linking.
10. **Change the admin password.** The default `admin` / `change_me!` is
    published in the docs.

---

## Phase 4 — Point the speaker at itself and link the account

11. In the admin UI, set Settings → Target Domain to `http://localhost:8000`,
    then Devices → **Migrate**, accept the plan, and reboot.
    CLI alternative:
    ```sh
    /mnt/nv/aftertouch/soundtouch-cli --host 127.0.0.1 setup migrate \
      --service-url http://localhost:8000 --method telnet
    sync; reboot
    ```
12. Open the Health tab, run the checks, and apply the **QuickFix for the
    empty `margeAccountUUID`**. If that doesn't stick:
    ```sh
    /mnt/nv/aftertouch/soundtouch-cli --host 127.0.0.1 setup pair \
      --mode=bare --account=1111111 --service-url http://localhost:8000
    sync; reboot
    ```
    `--mode=bare` is experimental. Try `--mode=full` if it fails.

---

## Phase 5 — Verify

13. On the speaker:
    ```sh
    wget -qO- http://localhost:8090/info | grep margeAccountUUID   # must NOT be empty
    wget -qO- http://localhost:8090/sources                        # expect LOCAL_INTERNET_RADIO, TUNEIN, RADIO_BROWSER
    ```

---

## Phase 6 — Any radio station on the preset buttons

14. Install the CLI on the speaker. Match the version you installed, and keep
    it in `/mnt/nv`, not `/tmp`:
    ```sh
    cd /mnt/nv/aftertouch
    curl -L --fail -o soundtouch-cli \
      https://github.com/gesellix/Bose-SoundTouch/releases/download/v<ver>/soundtouch-cli-v<ver>-linux-armv7
    chmod +x soundtouch-cli
    ```
15. For each slot (1–6), start the stream, wait, then store it:
    ```sh
    /mnt/nv/aftertouch/soundtouch-cli --host 127.0.0.1 source custom-radio \
      --url "<direct stream URL>" --name "Radyo Voyage"
    sleep 5
    /mnt/nv/aftertouch/soundtouch-cli --host 127.0.0.1 preset store-current --slot 1
    ```
    Find direct stream URLs at https://www.radio-browser.info.
    Previous presets: 1 Radyo Voyage, 2 Joy FM, 4 Virgin Radio Lebanon,
    5 Radio 1, 6 DJ Radio Dubai.
16. Check with `wget -qO- http://localhost:8090/presets`, then `sync; reboot`.

> ⚠️ **Short-press** preset buttons. A long press overwrites the stored preset.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `no matching host key type` | add `-oHostKeyAlgorithms=+ssh-rsa` |
| Port 8000/17008 unreachable | use the SSH tunnel (Phase 3, step 9) |
| Radio error **1005** | `margeAccountUUID` still empty → redo step 12 |
| Service not responding | `logread \| grep aftertouch \| tail -20` |
| Install aborts, no space | clean `/mnt/nv/aftertouch/*.backup`, or use `--version` for an older build |
| `scp` "subsystem request failed" | use `scp -O` |

---

## Rollback

1. In the admin UI, **revert the migration** first. Otherwise the speaker
   points at neither Bose nor AfterTouch.
2. Uninstall:
   ```sh
   curl -sSL https://raw.githubusercontent.com/gesellix/Bose-SoundTouch/main/scripts/on-device-install/uninstall.sh | sh
   ```
3. Restore TouchingSounds backups if needed:
   ```sh
   rw
   mv /mnt/nv/OverrideSdkPrivateCfg.xml.ts-backup /mnt/nv/OverrideSdkPrivateCfg.xml
   cp /mnt/nv/Sources.xml.ts-backup /mnt/nv/BoseApp-Persistence/1/Sources.xml
   sync; reboot
   ```

---

## Plan B — SoundTouch Reborn (STR)

1. Do Phase 0 and Phase 2 above.
2. Download `STR-Windows.exe` from https://github.com/JRpersonal/streborn/releases
   or https://st-reborn.de. It's Authenticode-signed (Certum), and you can
   optionally verify it with `gh attestation verify`.
3. Run it. It finds the speaker on the LAN and installs over Wi-Fi.
4. Complete the setup wizard, then search stations and assign presets 1–6.
5. Uninstall: Speaker Settings → **Remove STR**. A Bose factory reset does
   *not* remove STR.

---

## References

- AfterTouch on-device walkthrough: https://gesellix.github.io/Bose-SoundTouch/docs/guides/ON-DEVICE-INSTALL-WALKTHROUGH/
- AfterTouch service guide: https://gesellix.github.io/Bose-SoundTouch/docs/guides/SOUNDTOUCH-SERVICE/
- Model support matrix: https://gesellix.github.io/Bose-SoundTouch/docs/reference/MODEL-SUPPORT-MATRIX/
- Cloud API emulation reference: https://gesellix.github.io/Bose-SoundTouch/docs/reference/CLOUD-API/
- STR: https://github.com/JRpersonal/streborn · https://st-reborn.de
- soundcork (Python/FastAPI marge emulator, MIT): https://github.com/deborahgu/soundcork
- Bose end-of-life notice: https://www.bose.com/soundtouch-end-of-life
