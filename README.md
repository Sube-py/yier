# yier

Local-first chat workspace with a Python backend and a Vue frontend.

## Resume
[一二Resume](https://baike.baidu.com/item/%E4%B8%80%E4%BA%8C/23434669)

## Requirements

- Python 3.12+
- `uv`
- Node.js 20+
- `pnpm`

## Install

Backend dependencies:

```bash
uv sync
```

This repo is self-contained for the Python SDK dependency:

- `codex-app-server-sdk` is vendored under [packages/codex-app-server-sdk](/Users/sube/me/yier/packages/codex-app-server-sdk)
- teammates do not need to clone a separate `codex` repository just to install dependencies

Frontend dependencies:

```bash
cd web
pnpm install
```

## Authentication

This app now supports password protection for deployed environments.

Enable auth with either:

- `YIER_AUTH_PASSWORD`
- `YIER_AUTH_PASSWORD_HASH`

Plain password example:

```bash
export YIER_AUTH_PASSWORD='change-this-password'
```

Hashed password example:

```bash
uv run python -c "from yier_web.auth import hash_password; print(hash_password('change-this-password'))"
export YIER_AUTH_PASSWORD_HASH='paste-generated-hash-here'
```

Optional auth settings:

- `YIER_AUTH_SECRET`: optional extra signing secret for session cookies
- `YIER_AUTH_SESSION_TTL_HOURS`: cookie lifetime in hours, default is `168`

If neither password variable is set, authentication is disabled.

## Development Startup

Development mode is different from production:

- The backend should run with `--debug`
- The frontend should run with Vite dev server
- In this mode, the backend proxies frontend requests to `http://127.0.0.1:5173`

Recommended one-command startup:

```bash
uv run yier-dev
```

This starts:

- frontend: `pnpm dev`
- backend: debug mode with reload

If you prefer split terminals:

Frontend only:

```bash
uv run yier-dev-web
```

Backend only:

```bash
uv run yier-dev-backend
```

You can also override backend bind settings:

```bash
uv run yier-dev --host 127.0.0.1 --port 9999
uv run yier-dev-backend --host 127.0.0.1 --port 9999
```

Default address:

- App: `http://127.0.0.1:9999`
- Vite dev server: `http://127.0.0.1:5173`

Notes:

- Keep `pnpm dev` running, otherwise the backend cannot proxy the frontend in debug mode.
- API requests still go through the Python server at port `9999`.

## Production Startup

Production mode does not use the Vite dev server.

You must build the frontend first:

```bash
uv run yier-build-web
```

Then start the backend without `--debug`:

```bash
uv run yier-prod
```

In production mode:

- The backend serves `web/dist`
- No Vite proxy is used
- Authentication should usually be enabled with `YIER_AUTH_PASSWORD` or `YIER_AUTH_PASSWORD_HASH`

Production example:

```bash
export YIER_AUTH_PASSWORD='change-this-password'
uv run yier-build-web
uv run yier-prod --host 0.0.0.0 --port 9999
```

## Common Commands

Backend tests:

```bash
uv run pytest
```

Targeted backend tests:

```bash
uv run pytest tests/test_codex_backend.py tests/test_codex_workspace.py tests/test_app.py
```

Backend compile check:

```bash
uv run python -m compileall yier_web
```

Frontend unit tests:

```bash
cd web
pnpm test:unit
```

Frontend type check:

```bash
cd web
pnpm type-check
```

Frontend production build:

```bash
uv run yier-build-web
```

## Startup Summary

Development:

```bash
uv run yier-dev
```

Production:

```bash
export YIER_AUTH_PASSWORD='change-this-password'
uv run yier-build-web
uv run yier-prod --host 0.0.0.0 --port 9999
```

## Available uv Scripts

Development:

- `uv run yier-dev`: start frontend and backend together
- `uv run yier-dev-web`: start Vite only
- `uv run yier-dev-backend`: start backend only in debug mode

Production:

- `uv run yier-build-web`: build frontend assets
- `uv run yier-prod`: start backend in production mode
```
