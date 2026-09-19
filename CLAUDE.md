# openjev-tool - Project Specification

## Goal

A Python CLI tool

## What is openjev-tool?

`openjev-tool` is a command-line utility built with modern Python tooling and best practices.

## Technical Requirements

### Runtime

- Python 3.14+
- Installable globally with mise
- Cross-platform (macOS, Linux, Windows)

### Dependencies

- `typer` - CLI framework (built on Click with type hints)

### Optional Dependencies

- `opentelemetry-api` - OpenTelemetry API
- `opentelemetry-sdk` - OpenTelemetry SDK
- `opentelemetry-exporter-otlp` - OTLP exporter for Grafana Alloy/OTEL Collector

### Development Dependencies

- `ruff` - Linting and formatting
- `mypy` - Type checking
- `pytest` - Testing framework
- `bandit` - Security linting
- `pip-audit` - Dependency vulnerability scanning
- `gitleaks` - Secret detection (requires separate installation)

## CLI Arguments

```bash
openjev-tool [OPTIONS]
```

### Options

- `-v, --verbose` - Enable verbose output (count flag: -v, -vv, -vvv)
  - `-v` (count=1): INFO level logging
  - `-vv` (count=2): DEBUG level logging
  - `-vvv` (count=3+): TRACE level (includes library internals)
- `--telemetry` - Enable OpenTelemetry observability (or set OTEL_ENABLED=true)
- `--help` / `-h` - Show help message
- `--version` - Show version

## Project Structure

```
openjev-tool/
├── openjev_tool/
│   ├── __init__.py
│   ├── app.py            # make_app() — groups print help on no subcommand
│   ├── cli.py            # Typer CLI entry point (help on no subcommand)
│   ├── completion.py     # Shell completion (typer.completion)
│   ├── logging_config.py # Multi-level verbosity logging + file logging
│   └── telemetry/        # OpenTelemetry observability (optional extra)
├── tests/                # pytest (coverage-gated)
├── vulture_whitelist.py  # Dead-code allowlist for Typer entrypoints
├── AGENTS.md             # Agent guidance (conventions / gates)
├── pyproject.toml
├── README.md
├── CLAUDE.md             # This file
├── Makefile
├── LICENSE
├── .mise.toml
├── .gitleaks.toml
└── .gitignore
```

Repo-specific agent conventions live in `AGENTS.md`.

## Code Style

- Type hints for all functions
- Docstrings for all public functions
- Follow PEP 8 via ruff
- 100 character line length
- Strict mypy checking

## Development Workflow

```bash
# Install dependencies
make install

# Run linting
make lint

# Format code
make format

# Type check
make typecheck

# Run tests
make test

# Coverage-gated tests (fails under 40%)
make coverage

# Dead code + complexity (vulture / xenon / radon)
make quality

# Security scanning
make security-bandit       # Python security linting
make security-pip-audit    # Dependency CVE scanning
make security-gitleaks     # Secret detection
make security              # Run all security checks

# Run all checks (coverage + quality + security)
make check

# Full pipeline (format … build, install-global)
make pipeline
```

## Security

The template includes three lightweight security tools:

1. **bandit** - Python code security linting
   - Detects: SQL injection, hardcoded secrets, unsafe functions
   - Speed: ~2-3 seconds

2. **pip-audit** - Dependency vulnerability scanning
   - Detects: Known CVEs in dependencies
   - Speed: ~2-3 seconds

3. **gitleaks** - Secret and API key detection
   - Detects: AWS keys, GitHub tokens, API keys, private keys
   - Speed: ~1 second
   - Requires: `brew install gitleaks` (macOS)

All security checks run automatically in `make check` and `make pipeline`.

## Multi-Level Verbosity Logging

The template includes a centralized logging system with progressive verbosity levels and optional file logging.

### Implementation Pattern

1. **logging_config.py** - Centralized logging configuration
   - `setup_logging(verbose_count, log_file, log_format)` - Configure logging
   - `get_logger(name)` - Get logger instance for module
   - Maps verbosity to Python logging levels (WARNING/INFO/DEBUG)
   - Supports file logging with rotation (10MB, 5 backups)

2. **CLI Integration** - Add to every CLI command
   ```python
   from typing import Annotated
   import typer
   from openjev_tool.logging_config import get_logger, setup_logging

   logger = get_logger(__name__)

   @app.command()
   def command(
       verbose: Annotated[int, typer.Option("--verbose", "-v", count=True, help="...")] = 0,
   ) -> None:
       setup_logging(verbose)  # First thing in command
       logger.info("Operation started")
       logger.debug("Detailed info")
   ```

3. **Logging Levels**
   - **0 (no -v)**: WARNING only - production/quiet mode
   - **1 (-v)**: INFO - high-level operations
   - **2 (-vv)**: DEBUG - detailed debugging
   - **3+ (-vvv)**: TRACE - enable library internals

4. **File Logging**

   Enable file logging via environment variable or argument:
   ```bash
   # Via environment
   export LOG_FILE=/var/log/openjev-tool.log
   openjev-tool -v

   # Or programmatically
   setup_logging(verbose_count=1, log_file="/var/log/app.log")
   ```

   File logging features:
   - Rotating file handler (10MB max, 5 backups)
   - Creates parent directories automatically
   - Includes timestamps in log format
   - Custom format via `LOG_FORMAT` env var

5. **Environment Variables**

   | Variable | Default | Description |
   |----------|---------|-------------|
   | `LOG_FILE` | (none) | Path to log file (enables file logging) |
   | `LOG_FORMAT` | (default) | Custom log format string |

6. **Best Practices**
   - Always log to stderr (keeps stdout clean for piping)
   - Use structured messages with placeholders: `logger.info("Found %d items", count)`
   - Call `setup_logging()` first in every command
   - Use `get_logger(__name__)` at module level
   - For TRACE level, enable third-party library loggers in `logging_config.py`

7. **Customizing Library Logging**
   Edit `logging_config.py` to add project-specific libraries:
   ```python
   if verbose_count >= 3:
       logging.getLogger("requests").setLevel(logging.DEBUG)
       logging.getLogger("urllib3").setLevel(logging.DEBUG)
   ```

## OpenTelemetry Observability

The template includes OpenTelemetry integration for traces, metrics, and logs. Designed for Grafana stack (Alloy, Tempo, Prometheus, Loki).

### Installation

```bash
# Install with telemetry support
pip install openjev-tool[telemetry]
# or with uv
uv sync --extra telemetry
```

### Enabling Telemetry

```bash
# Via CLI flag
openjev-tool --telemetry

# Via environment variable
export OTEL_ENABLED=true
openjev-tool
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_ENABLED` | `false` | Enable telemetry |
| `OTEL_SERVICE_NAME` | `openjev-tool` | Service name in traces |
| `OTEL_EXPORTER_TYPE` | `console` | `console` or `otlp` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | Alloy/Collector endpoint |
| `OTEL_EXPORTER_OTLP_INSECURE` | `true` | Use insecure connection |

### Architecture (SOLID Design)

```
telemetry/
├── config.py      # TelemetryConfig - configuration from env vars (SRP)
├── service.py     # TelemetryService - singleton facade (ISP, DIP)
├── decorators.py  # @traced, trace_span - tracing utilities
└── exporters.py   # Exporter factory - extensible backends (OCP)
```

### Usage Patterns

**1. @traced Decorator**
```python
from openjev_tool.telemetry import traced

@traced("process_data")
def process_data(items: list) -> dict:
    return {"count": len(items)}

@traced(attributes={"operation.type": "batch"})
def batch_process():
    pass
```

**2. trace_span Context Manager**
```python
from openjev_tool.telemetry import trace_span

with trace_span("database_query", {"db.system": "postgres"}) as span:
    result = db.execute(query)
    if span:
        span.set_attribute("db.rows", len(result))
```

**3. Custom Metrics**
```python
from openjev_tool.telemetry import TelemetryService

meter = TelemetryService.get_instance().meter
counter = meter.create_counter("items_processed", description="Items processed")
counter.add(100, {"type": "batch"})

histogram = meter.create_histogram("processing_duration", unit="ms")
histogram.record(150.5, {"operation": "transform"})
```

### Grafana Stack Integration

**Push to Grafana Alloy (recommended)**
```bash
export OTEL_ENABLED=true
export OTEL_EXPORTER_TYPE=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
openjev-tool
```

### Development Mode

For development, use console exporter (default):
```bash
openjev-tool --telemetry -vv
```

This outputs traces, metrics, and logs to stderr. Point
`OTEL_EXPORTER_TYPE=otlp` at a local Alloy/Collector when you have one.

## Shell Completion

Uses `typer.completion` (Typer ≥0.27 — **do not** import
`click.shell_completion`; that API was removed and crashes the binary).

```bash
openjev-tool completion generate bash
openjev-tool completion generate zsh
openjev-tool completion generate fish
eval "$(openjev-tool completion generate bash)"
```

Supported: bash, zsh, fish, powershell, pwsh. The `completion` group is built
with `make_app()` so `openjev-tool completion` prints help.

### Adding More Commands

1. Create a module under `openjev_tool/`
2. For a group with subcommands, use `make_app("…")` then `app.add_typer(...)`
3. Register new Typer entrypoints / StrEnum members in `vulture_whitelist.py`
4. Put `\b Examples:` blocks in every command docstring
## Installation Methods

### Global installation with mise

```bash
cd /path/to/openjev-tool
mise use -g python@3.14
uv sync
uv tool install .
```

After installation, `openjev-tool` command is available globally.

### Local development

```bash
uv sync
uv run openjev-tool [args]
```

## Build Locally

```bash
make build
ls dist/
```