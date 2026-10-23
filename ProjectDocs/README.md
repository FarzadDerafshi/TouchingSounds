# TouchingSounds — Project Docs

TouchingSounds keeps a Bose SoundTouch 20 useful after Bose shut down its cloud
(2026-05). A small FastAPI app on the Office Server stands in for the dead Bose
services, so the speaker plays any internet radio station again. A responsive web app
(phone, tablet, desktop, installable) searches stations, keeps favorites and assigns the
six preset buttons.

This folder is the durable home for anything that isn't code: architecture, decisions,
deployment and operations, the user manual and the roadmap. Code-level changes go in
[`CHANGELOG.md`](../CHANGELOG.md) at the repo root.

## Index

| Doc | What's in it |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Network, components, how a station gets from the app to the speaker, data files |
| [DECISIONS.md](DECISIONS.md) | Why things are built the way they are, and the alternatives that were rejected |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Releasing with `ProdRelease.bat`, the Cloudflare tunnel, speaker rollback, troubleshooting |
| [USER-MANUAL.md](USER-MANUAL.md) | Using the app: search, favorites, presets, installing it on a phone |
| [ROADMAP.md](ROADMAP.md) | What's next |
| [MARGE_DEPLOY.md](MARGE_DEPLOY.md) | Notes from the first 0.6.0 deployment (what happens on first start, speaker rollback) |
| [AFTERTOUCH_INSTALL_PLAN.md](AFTERTOUCH_INSTALL_PLAN.md) | Plan B: the third-party AfterTouch route, if our own stack ever stops working |

## Quick facts

| | |
|---|---|
| Speakers | SoundTouch 20 "F&F Touching Sounds", `192.168.1.49` (home), FW 27.0.6 (primary) |
| | Wireless Link Adapter "OfficeSounds", `192.168.100.150` (office), FW 26.0.1 |
| Server | Office Server, `192.168.100.51`, Docker Desktop, folder `D:\apps\TouchingSounds` |
| Link between them | WireGuard tunnel "TouchingSounds-Office" between the two routers |
| Web app (LAN) | http://192.168.100.51:8000 |
| Web app (internet) | https://touchingsounds.example.com (Cloudflare tunnel) |
| Release | Telegram `/release` → TouchingSounds, or double-click `ProdRelease.bat` on the server |

## Conventions

- Keep entries dated (`YYYY-MM-DD`) when recording a decision or an operational event.
- A "decision" belongs in `DECISIONS.md` if a future contributor could reasonably ask
  "why is it done this way instead of the obvious alternative?"
- Operational events (deploys, incidents, infra changes) belong in `DEPLOYMENT.md`;
  the changelog is for shipped code changes only.
