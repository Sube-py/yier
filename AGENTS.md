# AGENTS.md

## Repo Conventions

- Python string formatting should prefer f-strings.
- Use `Path` for filesystem operations instead of raw string paths where practical.
- Commit messages must be written in English.
- Commit messages must not mention Codex, Claude, or collaboration tooling.

## Environment

- This repo uses `uv`, not a manually managed virtualenv workflow.
- Python dependencies are resolved from [`pyproject.toml`](/Users/sube/me/yier/pyproject.toml) and [`uv.lock`](/Users/sube/me/yier/uv.lock).
- Use `uv run ...` for Python commands.
- Do not assume `python` is available on `$PATH`; in this environment `uv run python ...` is the reliable path.
- The project requires Python `>=3.12`.
- Frontend commands run from [`web`](/Users/sube/me/yier/web) and use `pnpm`.

## Common Commands

- Backend tests:
  `uv run pytest`
- Targeted backend tests:
  `uv run pytest tests/test_codex_backend.py tests/test_codex_workspace.py tests/test_app.py`
- Python compile sanity check:
  `uv run python -m compileall yier_web`
- Frontend unit tests:
  `cd web && pnpm test:unit`
- Frontend type-check:
  `cd web && pnpm type-check`
- Frontend production build:
  `cd web && pnpm build`

## Workspace Layout

- Main backend app code lives in [`yier_web`](/Users/sube/me/yier/yier_web).
- Frontend app code lives in [`web/src`](/Users/sube/me/yier/web/src).
- The repo is a `uv` workspace and includes [`packages/yier-channel`](/Users/sube/me/yier/packages/yier-channel).

## Frontend Conventions

- Prefer Tailwind CSS for new frontend styling work. Reuse existing semantic classes when touching old code, but do not default to adding more large hand-written CSS blocks if Tailwind utilities or small extracted components can express the change cleanly.
- Do not keep expanding [`web/src/App.vue`](/Users/sube/me/yier/web/src/App.vue) with unrelated UI concerns.
- Frontend structure should prefer module-based splitting:
  - page-level or workspace-level UI should live in a `views` layer
  - module-specific subparts should stay close to that module
  - truly reusable UI pieces should go into [`web/src/components`](/Users/sube/me/yier/web/src/components)
- Do not treat `components` as the default destination for every new Vue file. Shared pieces go to `components`; feature and page composition should be split by module.
- If a piece of UI state has long-term product meaning, persist it through the backend config surface when appropriate instead of leaving it only in local component state.
- If a piece of UI state affects first paint or workspace routing, do not rely only on async `/api/config` hydration plus a hardcoded default. Provide a boot-safe strategy such as:
  - a loading guard that avoids rendering the wrong surface before hydration
  - or a small local bootstrap cache that is reconciled after config loads
- When using local bootstrap cache for UX, keep the backend config as the durable source of truth and treat the cache only as a first-paint hint.

## Codex SDK Notes

- `codex-app-server-sdk==0.2.0` is sourced from a local path:
  [`../codex/sdk/python`](/Users/sube/me/codex/sdk/python)
- Prefer the public SDK surface when possible:
  `Codex`, `Thread`, `thread_list()`, `thread_resume()`, `Thread.read()`, `Thread.turn()`
- Public SDK kwargs are snake_case, not wire camelCase.
  Examples:
  `approval_policy`, `approvals_reviewer`, `sandbox_policy`, `source_kinds`, `sort_key`
- Keep direct `AppServerClient` usage only where needed for runtime control that the high-level SDK does not cleanly expose in this app.

## Current Codex Integration Behavior

- Codex workspace session listing prefers SDK `thread_list()` and only falls back to reading local `~/.codex` data if SDK listing fails.
- Codex workspace grouping is still project-oriented in this app, even though SDK threads are flat.
- Only interactive thread sources are shown in the Codex workspace list:
  `cli`, `vscode`, `exec`, `appServer`
- Timeline hydration for bridged Codex sessions reads live thread state via SDK thread read APIs instead of relying only on local transcript storage.

## Approval And Elicitation Notes

- App-server exposes MCP elicitation to this app primarily as `mcpServer/elicitation/request`.
- Raw MCP-style `elicitation/create` is also normalized by the backend for compatibility.
- Treat both methods as the same logical `mcp_elicitation` flow in this codebase.
- The backend normalizes elicitation payloads so the frontend can read a consistent `payload.request`.
- `payload.request.mode` may be:
  - `form`
  - `url`
- Do not assume elicitation payloads are plain approval prompts.

## Elicitation UI Notes

- Users should not be forced to type raw JSON for normal elicitation forms.
- The frontend currently renders structured controls for common schema shapes:
  - `string`
  - `number`
  - `integer`
  - `boolean`
  - single-select enums via `enum` or `oneOf`
  - multi-select enums via `array` item enums / `anyOf`
- If a schema shape is unsupported, the frontend may still fall back to JSON response editing.
- When changing elicitation behavior, update both:
  - backend normalization in [`codex_backend.py`](/Users/sube/me/yier/yier_web/agent_backends/codex_backend.py)
  - frontend approval rendering in [`ChatTimeline.vue`](/Users/sube/me/yier/web/src/components/ChatTimeline.vue)

## Known Pitfalls

- Do not assume local `.codex` session files are the primary source of truth anymore; SDK listing is the preferred path.
- Do not mix SDK snake_case params with lower-level wire camelCase in the same change without checking the call site carefully.
- `thread_list()` and `Thread.read()` are good SDK-level entry points; streaming approval interception still depends on the lower-level client flow in this app.
- If you add or change persisted config fields, update the full chain together:
  - backend schemas in [`yier_web/schemas.py`](/Users/sube/me/yier/yier_web/schemas.py)
  - backend config read/write logic in [`yier_web/config.py`](/Users/sube/me/yier/yier_web/config.py)
  - frontend API types in [`web/src/types/api.ts`](/Users/sube/me/yier/web/src/types/api.ts)
  - frontend hydration/save logic
  - relevant backend and frontend tests
- After backend schema/config changes, do not trust a hot frontend refresh alone. Restart the backend process before judging whether `/api/config` or `settings.json` behavior is correct.
- If you change approval payload structure, also update:
  - [`web/src/types/api.ts`](/Users/sube/me/yier/web/src/types/api.ts)
  - [`web/src/App.vue`](/Users/sube/me/yier/web/src/App.vue)
  - frontend tests in [`web/src/__tests__/App.spec.ts`](/Users/sube/me/yier/web/src/__tests__/App.spec.ts)
