"""
server.py — uvicorn entry point that keeps the "ETag" header's casing.

The SoundTouch firmware only reads a marge response's ETag when the header
name is spelled exactly "ETag" (soundcork issue #129; AfterTouch has a test
for it).  uvicorn lowercases every response header name, so we wrap the
transport and restore the casing in the status/header block.  soundcork
solves the same problem with an nginx sidecar.
"""

import asyncio
import os

import uvicorn
from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol

_CASE_FIXES = ((b"\r\netag: ", b"\r\nETag: "),)


class _HeaderCasingTransport:
    """Delegating transport that rewrites header names in the header block."""

    def __init__(self, transport: asyncio.Transport):
        self._transport = transport

    def write(self, data: bytes) -> None:
        if data.startswith(b"HTTP/"):
            head, sep, rest = data.partition(b"\r\n\r\n")
            for wrong, right in _CASE_FIXES:
                head = head.replace(wrong, right)
            data = head + sep + rest
        self._transport.write(data)

    def __getattr__(self, name):
        return getattr(self._transport, name)


class BoseHttpProtocol(HttpToolsProtocol):
    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        super().connection_made(_HeaderCasingTransport(transport))  # type: ignore[arg-type]


if __name__ == "__main__":
    uvicorn.run(
        "app.main:api",
        host=os.getenv("TS_BIND", "0.0.0.0"),
        port=int(os.getenv("TS_PORT", "8000")),
        http=BoseHttpProtocol,
    )
