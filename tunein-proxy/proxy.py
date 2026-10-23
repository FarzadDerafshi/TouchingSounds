import logging
import re

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tunein-proxy")

UPSTREAM = "http://opml.radiotime.com"

app = FastAPI(title="TuneIn Proxy")


@app.api_route("/{path:path}", methods=["GET", "POST", "HEAD"])
async def proxy(request: Request, path: str):
    url     = f"{UPSTREAM}/{path}"
    query   = str(request.url.query)
    station = request.query_params.get("id", "")

    log.info("%s /%s?%s", request.method, path, query)
    if station:
        log.info("Station ID: %s", station)

    forward_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding")
    }

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            r = await client.request(
                method=request.method,
                url=url,
                params=dict(request.query_params),
                headers=forward_headers,
                content=await request.body(),
            )

        log.info("Upstream -> %d  (%d bytes)", r.status_code, len(r.content))

        content = r.content
        ct      = r.headers.get("content-type", "text/xml")

        # Bose firmware cannot handle HTTPS streams — downgrade any HTTPS
        # stream URLs embedded in the OPML response before returning to speaker
        if "xml" in ct or "opml" in ct or not ct:
            text    = content.decode("utf-8", errors="replace")
            patched = re.sub(r'url="https://', 'url="http://', text)
            if patched != text:
                log.info("Patched HTTPS stream URLs to HTTP in OPML response")
            content = patched.encode("utf-8")

        return Response(content=content, status_code=r.status_code, media_type=ct)

    except httpx.ConnectError as e:
        log.error("Cannot reach opml.radiotime.com: %s", e)
        return Response(status_code=502, content=b"Upstream unreachable")
    except httpx.TimeoutException as e:
        log.error("Upstream timeout: %s", e)
        return Response(status_code=504, content=b"Upstream timeout")
    except Exception as e:
        log.error("Proxy error: %s", e)
        return Response(status_code=500, content=str(e).encode())
