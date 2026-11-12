# ArcFlash

ArcFlash is a requirements-to-web-app agent: it reads a `requirements.yaml` and reference screenshots, makes one plan call, writes the backend and frontend of a React + Express + SQLite app in parallel from that plan, checks the whole app locally, repairs it in at most 3 rounds and restarts once if needed. Plan: `../docs/arcflash-plan.md`.
