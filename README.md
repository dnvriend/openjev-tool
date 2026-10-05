# openjev-tool

[![Python Version](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A local judgment-model server for Jev-style agentic engineering. Runs any
registered Hugging Face NLI cross-encoder (default: `AlexWortega/openjev`, a
Qwen3.5-4B 3-way NLI model) behind an HTTP API with a deterministic model
router, invocation stats, a built-in web playground for the "10 levels of
Jev" ladder, and a macOS menu bar controller.

Python 3.14 · Typer · FastAPI · torch (MPS/CUDA/CPU) · uv · AI-agent-first
(`--help` ships copy-pasteable examples)

## Install

```bash
git clone https://github.com/dnvriend/openjev-tool.git
cd openjev-tool
mise trust && mise install
uv sync
uv tool install .

# macOS menu bar controller (optional extra, PyObjC-based)
uv sync --extra menubar
```

## Quickstart

```bash
# 60-second tour — no model download, deterministic fake backend
openjev-tool serve --fake
open http://127.0.0.1:8080/ui

# Small model (~17 MB with include patterns) — live progress: MB, MB/s, ETA
openjev-tool models download nli-deberta-v3-small
openjev-tool models purge nli-deberta-v3-small  # remove weights again

# Real model (~8 GB, cached under ~/.cache/huggingface)
openjev-tool models download openjev-qwen3.5-4b
openjev-tool serve
curl -s -X POST http://127.0.0.1:8080/v1/score \
  -H 'Content-Type: application/json' \
  -d '{"premise": "A man plays guitar.", "hypothesis": "Someone makes music."}'

# macOS toolbar icon: start/stop, model checkboxes, live stats
openjev-tool menubar
```

## Commands

| Command | Description |
|---------|-------------|
| `serve [--fake] [--supervised]` | Start the judgment server (host/port from config) |
| `menubar [--fake]` | macOS toolbar controller (needs `--extra menubar`) |
| `config path \| init \| show \| set` | Manage `~/.config/openjev/config.json` |
| `models list` | Registry table: enabled, priority, cache state |
| `models add <org/repo> [--labels …] [--capabilities …]` | Register a HF NLI model |
| `models remove/enable/disable <id>` | Manage the registry |
| `models download <id>` | Fetch weights into the HF cache |
| `jev score \| noul \| choice \| rerank \| grade` | Direct CLI inference (local or `--server`) |
| `jev serve` | v1 alias for `serve` |
| `completion generate <shell>` | Shell completion (bash/zsh/fish/powershell/pwsh) |

No subcommand prints help. Global `-v` / `-vv` / `-vvv` must precede the subcommand.

## HTTP API

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Status, version, uptime (menu bar polls this) |
| `GET /stats` | Invocations, errors, p50/p95 per endpoint and model |
| `GET /v1/models` | Registry state: enabled, loaded, capabilities, cached |
| `GET /v1/levels` | The 10-level ladder with examples |
| `GET/PUT /v1/config` | Read / validate-and-persist config.json (hot-applies) |
| `GET /v1/reload` | Re-read config.json after external edits |
| `POST /v1/score \| noul \| choice \| rerank \| grade` | Judgment primitives |
| `POST /v1/ask` | Batch several questions over one state (level 10) |
| `POST /v1/invoke` | Dispatch by level 1–10 (playground entry point) |
| `GET /ui` | Web playground |

Legacy `/score`, `/noul`, … paths alias the `/v1` routes. Every judgment
response carries `"routing": {"model", "reason", "candidates"}` — an optional
`"model"` key in the request pins routing to one model.

## The 10 levels

The authentic ladder from IndyDevDan's "10 Levels of Jev For Agentic
Engineers" (transcript in `docs/transcript/`); each level ships the video's
use case as a ready-to-run fixture:

| Level | Name | Primitive |
|-------|------|-----------|
| 1 | Basic Decision (prompt injection) | noul |
| 2 | Multiple Choice (support triage) | choice |
| 3 | Score & Weights (priority) | score |
| 4 | Tool Safety (reversible?) | noul |
| 5 | Ask Multi (bools+choices+scores) | ask |
| 6 | Jev Guard (block destructive writes) | noul |
| 7 | Should I Compact (context triggers) | noul |
| 8 | Cheap File Reads (classify unread files) | ask |
| 9 | Harness At Scale (ask-jev everywhere) | ask |
| 10 | Agent-to-Jev (self-validation) | ask |

Driven by `openjev_tool/levels.py` — the playground and `/v1/invoke` render
the ladder from that table, so levels are data, not code.

## Configuration — `~/.config/openjev/config.json`

The file and its directory are materialized automatically by `serve`,
`menubar`, and the `models`/`config` commands (`openjev-tool config init`
does it explicitly; idempotent). `OPENJEV_CONFIG` overrides the path; writes
are atomic. Server host/port, the model registry (repo, revision, logit label
order, template, capabilities, priority, enabled), router policy, and stats
persistence. The default registry ships three entries: the pinned openjev
checkpoint, a small `cross-encoder/nli-deberta-v3-small` (~142 MB) for quick
experiments, and `FacebookAI/roberta-large-mnli`. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full schema and design.

The menu bar shows each model as `id · cached` / `id · not cached`, and a
Download menu lists uncached models with live progress (`⬇ model 42% (4.0
GB/8.1 GB)`) plus a completion notification; native macOS menus cannot host
progress bars or colored badges, so progress is text in the item title.

## Develop

```bash
uv sync
make pipeline   # format → lint → types → coverage → quality → security → build → install
make check      # lighter gate (no build/install)
```

Agent conventions: [AGENTS.md](AGENTS.md) · Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## License

MIT · Dennis Vriend
