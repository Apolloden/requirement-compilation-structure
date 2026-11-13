# ArcFlash

ArcFlash turns a requirements folder (`requirements.yaml` plus screenshots) into a React + Express + SQLite web app.
It makes one plan call, then writes the backend and the frontend in parallel from that plan.
It checks the whole app locally (static checks, build, start, a page check and Playwright scenario tests written from the requirement text only).
It repairs the app with SEARCH/REPLACE edits in at most 3 rounds, keeps the best version, and restarts once from a new plan if the result is poor.
Every call uses `deepseek-flash` with thinking off; the endpoint and key come only from `OPENAI_BASE_URL` and `OPENAI_API_KEY`.

Results of the ARC-Bench run: `RESULTS.md`. Design and tasks: `../docs/3-arcflash/arcflash-plan.md`. Change log: `../docs/3-arcflash/arcflash-changes.md`. These two documents are kept in the course workspace next to this folder, not in this repository.

**A full run costs money. Ask first.**

## Commands (from this folder)

- Setup: `uv sync` (Node 22 and npm must be on `PATH`). The check uses the Playwright version whose Chromium is already installed: 1.57.0 (the ARC-Bench runner's, Chromium build 1200) or 1.64.1 (build 1248); with neither, it tries to install 1.57.0's for at most 5 minutes
- Run (costs money): `.venv/bin/python main.py <requirements-dir> -o <out>`, for example `.venv/bin/python main.py input -o outputs/sample-1`
- Start a finished app: `cd <out> && bash deploy.sh`, then open http://localhost:3000 (or set `PORT`)
- Grade a finished app with a test folder: `.venv/bin/python scripts/grade_local.py <out> <tests-dir> [--port 3311] [--keep]`
- Unit tests (no model, no npm): `.venv/bin/python -m unittest discover -s tests -v`
- Integration tests (no model; real npm and Chromium): `ARCFLASH_INTEGRATION=1 .venv/bin/python -m unittest discover -s tests -v`
- Package for ARC-Bench: `python3 scripts/package.py [-o PATH]` (writes `../arcflash-<date>.zip` with only `main.py`, `requirements.txt` and `arcflash/`)

## What a run leaves in `<out>`

The app (`package.json`, `deploy.sh`, `backend/`, `frontend/`, with `node_modules`) and `.arc/`: `run.json` (calls, tokens, estimated cost, minutes, local test results, the shipped version), `calls/` (every prompt and answer), `plan-<n>.json`, `notes.json`, `digest.txt`, `versions/` (every checked version) and `harness/` (our scenario tests).
