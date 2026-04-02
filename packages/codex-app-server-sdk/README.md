# Vendored codex-app-server-sdk

This workspace package vendors `codex-app-server-sdk==0.2.0` into the `yier`
repository so teammates can use this project without cloning the separate
`codex` repository.

Source origin:

- `../codex/sdk/python`
- upstream project: `https://github.com/openai/codex`

Why vendor it here:

- the SDK is not published to PyPI yet
- `yier` should remain self-contained for teammates and CI
- the SDK is a small pure-Python package, so vendoring is straightforward on
  both macOS and Linux

When updating the SDK:

1. sync files from the upstream SDK package
2. keep the package version here aligned with upstream
3. run `uv lock` in the repo root
4. run the relevant backend tests
