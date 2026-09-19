# AGENTS.md

Guidance for AI agents working in a project generated from
`cookiecutter-python-cli-uv`. Repo-specific product facts belong here as the
project grows; template-level logging/telemetry details live in `CLAUDE.md`.

## What this is
A Typer CLI scaffold (Python 3.14, uv, mypy strict,
ruff). AI-agent-first: every command ships complete runnable examples in
`--help`, and groups print help instead of erroring on no subcommand.

## Always run before declaring work done
`make pipeline` is the single gate (format → install → lint → typecheck →
coverage → quality → security → build → install-global). Fix everything it
reports; do not bypass. `make check` is the lighter gate (no build/install).
- `make coverage` — pytest with `--cov=openjev_tool`, fails
  under 40% branch coverage. Prefer pure-helper tests.
- `make quality` — vulture (needs `vulture_whitelist.py`), xenon (`-b C -m C
  -a C`), radon metrics.
- gitleaks requires `brew install gitleaks` (not a Python dep).
- Run via `uv run …`, never global python/pip.

## Toolchain conventions
- **`make_app()`** (`openjev_tool/app.py`): use for every
  command group so `openjev-tool <group>` prints help.
- Type hints + docstrings on public functions. No comments.
- When adding a Typer entrypoint / StrEnum member, register it in
  `vulture_whitelist.py`.
- Completion uses `typer.completion` (Typer ≥0.27 dropped click shell APIs —
  do not import `click.shell_completion`).

## AI-agent-first CLI rules
- Every command docstring ends with a `\b Examples:` block of copy-pasteable
  commands.
- Keep request/parse logic in a pure `_helper(...)` and have the thin Typer
  command call it (that is what unit tests cover).
- Status/data → stdout; errors/diagnostics → stderr.
- Global `-v/-vv/-vvv` must precede the subcommand:
  `openjev-tool -v completion generate bash`, not after it.

## Don't
- Don't construct `typer.Typer()` for groups — use `make_app()`.
- Don't reintroduce `from click.shell_completion import …`.
