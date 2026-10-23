"""
marge.py — local stand-in for Bose's "marge" account server.

WHY THIS EXISTS
───────────────
At boot the speaker asks marge (margeServerUrl, factory value
https://streaming.bose.com) for its account: devices, presets and — the
part that matters — the source table.  Bose shut marge down on 2026-05-06,
so the speaker never learns LOCAL_INTERNET_RADIO and every /select of it
returns 1005 UNKNOWN_SOURCE_ERROR.  That was the real root cause behind
the whole 0.4.x investigation: we redirected the BMX registry and
TuneIn, but never marge.

bose.setup_speaker() points margeServerUrl at {BOSE_SERVER_URL}/marge
(via /mnt/nv/OverrideSdkPrivateCfg.xml), and this module answers.

DATA
────
Per device (see bose.DEVICES): the primary device keeps the original
layout directly in TS_DATA_DIR; every other device gets
TS_DATA_DIR/devices/<ip>/.  In each:
• Sources.xml — a snapshot of the speaker's own source list (pulled over the
  root shell before the redirect, so SPOTIFY/ALEXA keep their secrets),
  with LOCAL_INTERNET_RADIO normalised to its canonical token.  Devices
  marked ":fresh" have no snapshot and get AUX + LOCAL_INTERNET_RADIO only
  (which also drops a previous owner's linked accounts).
• presets.json — presets saved from the app or on the speaker (long-press);
  on the primary device they override PRESET_N_URL/NAME from the environment.
• recents.json — recents as reported by the speaker.

The speaker's requests name an account id (and sometimes a deviceID), which
bose.hosts_for_account / host_for_device_id map back to a configured device.

Response formats follow soundcork (MIT, github.com/deborahgu/soundcork)
and AfterTouch (MIT, github.com/gesellix/bose-soundtouch).
"""

import json
import logging
import os
import time
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response

import app.bose as bose
from app.bose import BoseError

log = logging.getLogger("marge")

router = APIRouter()

_DATA_DIR = os.getenv("TS_DATA_DIR", "data")


def data_dir(h: str) -> str:
    """Where device `h` keeps its marge state (primary: the data dir itself)."""
    if bose.device(h).primary:
        return _DATA_DIR
    return os.path.join(_DATA_DIR, "devices", h)


def _sources_file(h: str) -> str:
    return os.path.join(data_dir(h), "Sources.xml")


def _presets_file(h: str) -> str:
    return os.path.join(data_dir(h), "presets.json")


def _recents_file(h: str) -> str:
    return os.path.join(data_dir(h), "recents.json")

_CONTENT_TYPE = "application/vnd.bose.streaming-v1.2+xml"
_DEFAULT_DATE = "2012-09-19T12:43:00.000+00:00"

# Bose's provider table — a source's sourceproviderid is its 1-based index.
PROVIDERS = [
    "PANDORA", "INTERNET_RADIO", "OFF", "LOCAL", "AIRPLAY", "CURRATED_RADIO",
    "STORED_MUSIC", "SLAVE_SOURCE", "AUX", "RECOMMENDED_INTERNET_RADIO",
    "LOCAL_INTERNET_RADIO", "GLOBAL_INTERNET_RADIO", "HELLO", "DEEZER",
    "SPOTIFY", "IHEART", "SIRIUSXM", "GOOGLE_PLAY_MUSIC", "QQMUSIC", "AMAZON",
    "LOCAL_MUSIC", "WBMX", "SOUNDCLOUD", "TIDAL", "TUNEIN", "QPLAY", "JUKE",
    "BBC", "DARFM", "7DIGITAL", "SAAVN", "RDIO", "PHONE_MUSIC", "ALEXA",
    "RADIOPLAYER", "RADIO.COM", "RADIO_COM", "SIRIUSXM_EVEREST", "RADIO_BROWSER",
]

# Stable ids for well-known sources (AfterTouch's canonical defaults).
_CANONICAL_IDS = {
    "AUX": "10001", "INTERNET_RADIO": "10002", "LOCAL_INTERNET_RADIO": "10003",
    "TUNEIN": "10004", "RADIO_BROWSER": "10005",
}


# ── Persistence ──────────────────────────────────────────────────────────────

def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def has_sources_snapshot(h: str) -> bool:
    return os.path.isfile(_sources_file(h))


async def snapshot_sources(h: str) -> bool:
    """
    Copy device `h`'s Sources.xml into its data dir, once.

    Must happen BEFORE margeServerUrl is redirected: afterwards the speaker
    rebuilds Sources.xml from whatever marge serves.  Returns True if a
    snapshot is available (bose.setup_speaker only redirects marge then).
    """
    if has_sources_snapshot(h):
        return True
    try:
        with bose.use_device(h):
            raw = await bose.read_sources_xml()
        ET.fromstring(raw)  # refuse to persist something we can't parse
    except (BoseError, ET.ParseError) as exc:
        log.error("Device %s: Sources.xml snapshot failed — marge redirect withheld: %s", h, exc)
        return False
    _write_text(_sources_file(h), raw)
    log.info("Device %s: Sources.xml snapshot saved to %s", h, _sources_file(h))
    return True


# ── Sources ──────────────────────────────────────────────────────────────────

def configured_sources(h: str) -> list[dict]:
    """
    Device `h`'s sources: its snapshot, normalising LOCAL_INTERNET_RADIO.
    Without a snapshot (":fresh" devices) just AUX + LOCAL_INTERNET_RADIO.
    """
    root = ET.parse(_sources_file(h)).getroot() if has_sources_snapshot(h) else ET.Element("sources")
    sources, next_id = [], 100001
    for el in root.findall("source"):
        sk = el.find("sourceKey")
        if sk is None:
            continue
        stype = sk.get("type", "")
        if stype not in PROVIDERS:
            log.warning("Skipping source with unknown provider type %r", stype)
            continue
        src = {
            "id":         el.get("id") or _CANONICAL_IDS.get(stype, ""),
            "type":       stype,
            "account":    sk.get("account", ""),
            "display":    el.get("displayName", ""),
            "secret":     el.get("secret", ""),
            "secretType": el.get("secretType", ""),
            "createdOn":  el.findtext("createdOn") or _DEFAULT_DATE,
            "updatedOn":  el.findtext("updatedOn") or _DEFAULT_DATE,
        }
        if not src["id"]:
            src["id"], next_id = str(next_id), next_id + 1
        if stype == "LOCAL_INTERNET_RADIO":
            # Undo the 0.4.x patch (blank token, custom account)
            src.update(secret=bose.LOCAL_RADIO_SECRET, secretType="token", account="")
        sources.append(src)

    if not sources and bose.device(h).fresh:
        sources.append({
            "id": _CANONICAL_IDS["AUX"], "type": "AUX", "account": "AUX",
            "display": "AUX IN", "secret": "", "secretType": "",
            "createdOn": _DEFAULT_DATE, "updatedOn": _DEFAULT_DATE,
        })
    if not any(s["type"] == "LOCAL_INTERNET_RADIO" for s in sources):
        sources.append({
            "id": _CANONICAL_IDS["LOCAL_INTERNET_RADIO"], "type": "LOCAL_INTERNET_RADIO",
            "account": "", "display": "", "secret": bose.LOCAL_RADIO_SECRET,
            "secretType": "token", "createdOn": _DEFAULT_DATE, "updatedOn": _DEFAULT_DATE,
        })
    return sources


def _source_xml(src: dict) -> ET.Element:
    el = ET.Element("source", id=src["id"], type="Audio")
    ET.SubElement(el, "createdOn").text = src["createdOn"]
    cred = ET.SubElement(el, "credential", type=src["secretType"] or "token")
    cred.text = src["secret"]
    ET.SubElement(el, "name").text = src["account"]
    ET.SubElement(el, "sourceproviderid").text = str(PROVIDERS.index(src["type"]) + 1)
    ET.SubElement(el, "sourcename").text = src["display"]
    ET.SubElement(el, "sourceSettings")
    ET.SubElement(el, "updatedOn").text = src["updatedOn"]
    ET.SubElement(el, "username").text = src["account"]
    return el


def _find_source(sources: list[dict], source_type: str, source_id: str = "") -> dict:
    for s in sources:
        if (source_id and s["id"] == source_id) or (not source_id and s["type"] == source_type):
            return s
    return next(s for s in sources if s["type"] == "LOCAL_INTERNET_RADIO")


# ── Presets & recents ────────────────────────────────────────────────────────

def presets(h: str) -> dict[str, dict]:
    """
    Device `h`'s slot → preset: presets saved from the app or on the speaker,
    over the PRESET_N_URL defaults (primary device only).
    """
    out = {}
    for n, p in (bose.env_presets().items() if bose.device(h).primary else ()):
        out[str(n)] = {
            "name": p["name"], "type": "stationurl", "source": "LOCAL_INTERNET_RADIO",
            "location": p["location"], "art": "",
            "createdOn": _DEFAULT_DATE, "updatedOn": _DEFAULT_DATE,
        }
    out.update(_read_json(_presets_file(h), {}))
    return out


def save_preset(h: str, slot: str, name: str, location: str, art: str = "") -> None:
    """Record a LOCAL_INTERNET_RADIO preset in device `h`'s presets.json."""
    sources = configured_sources(h)
    src = _find_source(sources, "LOCAL_INTERNET_RADIO")
    now = _now_iso()
    stored = _read_json(_presets_file(h), {})
    stored[slot] = {
        "name": name, "type": "stationurl", "source": src["type"], "sourceId": src["id"],
        "location": location, "art": art,
        "createdOn": stored.get(slot, {}).get("createdOn", now), "updatedOn": now,
    }
    _write_text(_presets_file(h), json.dumps(stored, indent=2))


def _preset_xml(slot: str, p: dict, sources: list[dict]) -> ET.Element:
    el = ET.Element("preset", buttonNumber=slot)
    ET.SubElement(el, "containerArt").text = p["art"]
    ET.SubElement(el, "contentItemType").text = p["type"]
    ET.SubElement(el, "createdOn").text = p["createdOn"]
    ET.SubElement(el, "location").text = p["location"]
    ET.SubElement(el, "name").text = p["name"]
    ET.SubElement(el, "username").text = p["name"]
    el.append(_source_xml(_find_source(sources, p["source"], p.get("sourceId", ""))))
    ET.SubElement(el, "updatedOn").text = p["updatedOn"]
    return el


def _presets_xml(h: str, sources: list[dict]) -> ET.Element:
    el = ET.Element("presets")
    for slot, p in sorted(presets(h).items()):
        el.append(_preset_xml(slot, p, sources))
    return el


def _recent_xml(r: dict, sources: list[dict]) -> ET.Element:
    el = ET.Element("recent", id=r["id"])
    ET.SubElement(el, "contentItemType").text = r["type"]
    ET.SubElement(el, "createdOn").text = r["createdOn"]
    ET.SubElement(el, "lastplayedat").text = r["lastPlayedAt"]
    ET.SubElement(el, "location").text = r["location"]
    ET.SubElement(el, "name").text = r["name"]
    el.append(_source_xml(_find_source(sources, r["source"], r.get("sourceId", ""))))
    ET.SubElement(el, "updatedOn").text = r["lastPlayedAt"]
    return el


def _recents_xml(h: str, sources: list[dict]) -> ET.Element:
    el = ET.Element("recents")
    for r in _read_json(_recents_file(h), []):
        el.append(_recent_xml(r, sources))
    return el


# ── Device ───────────────────────────────────────────────────────────────────

_last_info: dict[str, str] = {}


async def _device_xml(h: str, sources: list[dict]) -> ET.Element:
    """Device `h`'s <device> entry, built from its live /info."""
    try:
        with bose.use_device(h):
            _last_info[h] = await bose.info()
    except BoseError as exc:
        log.warning("marge: /info of %s unavailable, using last copy: %s", h, exc)
    info = ET.fromstring(_last_info[h]) if _last_info.get(h) else ET.Element("info")

    fw = serial = product_serial = ip = ""
    for comp in info.iter("component"):
        cat = comp.findtext("componentCategory", "")
        if cat == "SCM":
            fw, serial = comp.findtext("softwareVersion", ""), comp.findtext("serialNumber", "")
        elif cat == "PackagedProduct":
            product_serial = comp.findtext("serialNumber", "")
    for net in info.iter("networkInfo"):
        if net.get("type") == "SCM":
            ip = net.findtext("ipAddress", "")
    product = f'{info.findtext("type", "")} {info.findtext("moduleType", "")}'.strip()

    dev = ET.Element("device", deviceid=info.get("deviceID", ""))
    prod = ET.SubElement(dev, "attachedProduct", product_code=product)
    ET.SubElement(prod, "components")
    ET.SubElement(prod, "productlabel").text = product
    ET.SubElement(prod, "serialnumber").text = product_serial
    ET.SubElement(dev, "createdOn").text = _DEFAULT_DATE
    ET.SubElement(dev, "firmwareVersion").text = fw
    ET.SubElement(dev, "ipaddress").text = ip
    ET.SubElement(dev, "name").text = info.findtext("name", "")
    dev.append(_presets_xml(h, sources))
    dev.append(_recents_xml(h, sources))
    ET.SubElement(dev, "serialnumber").text = serial
    ET.SubElement(dev, "updatedOn").text = _DEFAULT_DATE
    return dev


# ── Response helper ──────────────────────────────────────────────────────────

def _xml_response(el: ET.Element | None = None, method_name: str = "") -> Response:
    body = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    if el is not None:
        body += ET.tostring(el, encoding="unicode")
    resp = Response(content=body, media_type=_CONTENT_TYPE)
    # The firmware wants this spelled exactly "ETag".  uvicorn lowercases it
    # on the wire; app/server.py restores the casing.
    resp.headers["ETag"] = str(zlib.crc32(body.encode()))
    if method_name:
        resp.headers["method_name"] = method_name
    return resp


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/marge/streaming/support/power_on")
async def power_on(request: Request):
    log.info("marge: power_on from speaker (%d bytes)", len(await request.body()))
    return Response(status_code=200)


@router.get("/marge/streaming/sourceproviders")
async def source_providers():
    root = ET.Element("sourceProviders")
    for i, name in enumerate(PROVIDERS, start=1):
        p = ET.SubElement(root, "sourceprovider", id=str(i))
        ET.SubElement(p, "createdOn").text = _DEFAULT_DATE
        ET.SubElement(p, "name").text = name
        ET.SubElement(p, "updatedOn").text = _DEFAULT_DATE
    return _xml_response(root)


async def _account_hosts(account: str) -> list[str]:
    """Configured devices on `account`, refreshing identities once on a miss."""
    hosts = bose.hosts_for_account(account)
    if not hosts:
        await bose.refresh_identities()
        hosts = bose.hosts_for_account(account)
    if not hosts:
        log.warning("marge: request for unknown account %s — no configured device has it", account)
    return hosts


async def _device_host(account: str, device: str) -> str | None:
    h = bose.host_for_device_id(device) if device else None
    if not h:
        hosts = await _account_hosts(account)
        h = hosts[0] if hosts else None
    return h


def _unknown() -> Response:
    return Response(status_code=404)


@router.get("/marge/streaming/account/{account}/full")
async def account_full(account: str):
    hosts = await _account_hosts(account)
    if not hosts:
        return _unknown()
    sources = configured_sources(hosts[0])
    log.info("marge: full account %s requested — serving %d sources, devices %s",
             account, len(sources), ", ".join(hosts))
    root = ET.Element("account", id=account)
    ET.SubElement(root, "accountStatus").text = "OK"
    devices = ET.SubElement(root, "devices")
    for h in hosts:
        devices.append(await _device_xml(h, configured_sources(h)))
    ET.SubElement(root, "mode").text = "global"
    ET.SubElement(root, "preferredLanguage").text = "en"
    root.append(_provider_settings(account))
    src_el = ET.SubElement(root, "sources")
    for s in sources:
        src_el.append(_source_xml(s))
    return _xml_response(root, "getFullAccount")


@router.get("/marge/streaming/account/{account}/devices")
async def account_devices(account: str):
    hosts = await _account_hosts(account)
    if not hosts:
        return _unknown()
    root = ET.Element("devices")
    for h in hosts:
        root.append(await _device_xml(h, configured_sources(h)))
    return _xml_response(root, "getDevices")


@router.get("/marge/streaming/account/{account}/sources")
async def account_sources(account: str):
    hosts = await _account_hosts(account)
    if not hosts:
        return _unknown()
    root = ET.Element("sources")
    for s in configured_sources(hosts[0]):
        root.append(_source_xml(s))
    return _xml_response(root)


@router.get("/marge/streaming/account/{account}/device/{device}/presets")
@router.get("/marge/streaming/account/{account}/presets/all")
async def account_presets(account: str, device: str = ""):
    h = await _device_host(account, device)
    if not h:
        return _unknown()
    return _xml_response(_presets_xml(h, configured_sources(h)))


@router.put("/marge/streaming/account/{account}/device/{device}/preset/{slot}")
async def put_preset(account: str, device: str, slot: str, request: Request):
    """A preset stored on the speaker (long-press) — keep it, echo it back."""
    h = await _device_host(account, device)
    if not h:
        return _unknown()
    el = ET.fromstring(await request.body())
    sources = configured_sources(h)
    source_id = el.findtext("sourceid", "")
    src = _find_source(sources, "", source_id) if source_id else _find_source(sources, "LOCAL_INTERNET_RADIO")
    now = _now_iso()
    preset = {
        "name": el.findtext("name") or el.findtext("username") or f"Preset {slot}",
        "type": el.findtext("contentItemType", "stationurl"),
        "source": src["type"], "sourceId": src["id"],
        "location": el.findtext("location", ""), "art": el.findtext("containerArt", ""),
        "createdOn": now, "updatedOn": now,
    }
    stored = _read_json(_presets_file(h), {})
    stored[slot] = preset
    _write_text(_presets_file(h), json.dumps(stored, indent=2))
    log.info("marge: %s preset %s stored → %s (%s)", h, slot, preset["name"], src["type"])
    return _xml_response(_preset_xml(slot, preset, sources))


@router.delete("/marge/streaming/account/{account}/device/{device}/preset/{slot}")
async def delete_preset(account: str, device: str, slot: str):
    h = await _device_host(account, device)
    if not h:
        return _unknown()
    stored = _read_json(_presets_file(h), {})
    stored.pop(slot, None)
    _write_text(_presets_file(h), json.dumps(stored, indent=2))
    return Response(status_code=200)


@router.get("/marge/streaming/account/{account}/device/{device}/recents")
async def account_recents(account: str, device: str):
    h = await _device_host(account, device)
    if not h:
        return _unknown()
    return _xml_response(_recents_xml(h, configured_sources(h)))


@router.post("/marge/streaming/account/{account}/device/{device}/recent")
async def post_recent(account: str, device: str, request: Request):
    h = await _device_host(account, device)
    if not h:
        return _unknown()
    el = ET.fromstring(await request.body())
    sources = configured_sources(h)
    source_id = el.findtext("sourceid", "")
    src = _find_source(sources, "", source_id) if source_id else _find_source(sources, "LOCAL_INTERNET_RADIO")
    now = _now_iso()
    recents = [r for r in _read_json(_recents_file(h), [])
               if not (r["location"] == el.findtext("location", "") and r["source"] == src["type"])]
    recent = {
        "id": str(max([int(r["id"]) for r in recents] + [0]) + 1),
        "name": el.findtext("name", ""), "type": el.findtext("contentItemType", ""),
        "location": el.findtext("location", ""), "source": src["type"], "sourceId": src["id"],
        "createdOn": now, "lastPlayedAt": el.findtext("lastplayedat") or now,
    }
    recents = [recent] + recents[:9]
    _write_text(_recents_file(h), json.dumps(recents, indent=2))
    return _xml_response(_recent_xml(recent, sources))


def _provider_settings(account: str) -> ET.Element:
    root = ET.Element("providerSettings")
    s = ET.SubElement(root, "providerSetting")
    ET.SubElement(s, "boseId").text = account
    ET.SubElement(s, "keyName").text = "ELIGIBLE_FOR_TRIAL"
    ET.SubElement(s, "value").text = "true"
    ET.SubElement(s, "providerId").text = "14"
    return root


@router.get("/marge/streaming/account/{account}/provider_settings")
async def provider_settings(account: str):
    return _xml_response(_provider_settings(account), "getProviderSettings")


@router.post("/marge/streaming/music/musicprovider/{provider_id}/is_eligible")
@router.post("/marge/streaming/music/musicprovider/{provider_id}/trial/is_eligible")
async def provider_eligibility(provider_id: str):
    root = ET.Element("providerSettings")
    ET.SubElement(root, "isEligible").text = "false"
    return _xml_response(root)


@router.get("/marge/streaming/software/update/account/{account}")
async def software_update(account: str):
    # Empty location = "no update available"; firmware stays frozen where it is.
    root = ET.Element("software_update")
    ET.SubElement(root, "softwareUpdateLocation")
    return _xml_response(root)


@router.get("/marge/streaming/device/{device_id}/streaming_token")
async def streaming_token(device_id: str):
    resp = Response(status_code=200)
    resp.headers["Authorization"] = "touchingsounds-local"
    resp.headers["ETag"] = str(int(time.time() * 1000))
    return resp


@router.api_route("/marge/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def marge_unhandled(path: str, request: Request):
    """Log anything we don't emulate yet — the speaker's calls tell us what's missing."""
    body = (await request.body())[:500]
    log.warning("marge: UNHANDLED %s /marge/%s  body=%r", request.method, path, body)
    return Response(status_code=404)
