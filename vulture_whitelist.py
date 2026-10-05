"""Vulture whitelist.

Vulture reports code that appears unused. Typer CLI commands (registered
dynamically via decorators / add_typer) and the make_app no-subcommand
callback show up as false positives, so they are listed here.

Reviewed manually. Do not delete entries without re-running:
    uv run vulture openjev_tool vulture_whitelist.py
"""

# --- Typer command entrypoints (registered dynamically, not imported) --------
main  # openjev_tool/cli.py — @app.callback
generate_completion  # openjev_tool/completion.py — @completion_app.command
download  # openjev_tool/jev.py — @jev_app.command
noul  # openjev_tool/jev.py — @jev_app.command
score  # openjev_tool/jev.py — @jev_app.command
choice  # openjev_tool/jev.py — @jev_app.command
rerank  # openjev_tool/jev.py — @jev_app.command
grade  # openjev_tool/jev.py — @jev_app.command
serve  # openjev_tool/jev.py — @jev_app.command
_show_help_when_no_subcommand  # openjev_tool/app.py — make_app callback

# --- openjev_tool/models.py — @models_app.command entrypoints ---------------
list_models  # openjev_tool/models.py — @models_app.command
add_model  # openjev_tool/models.py — @models_app.command
remove_model  # openjev_tool/models.py — @models_app.command
enable_model  # openjev_tool/models.py — @models_app.command
disable_model  # openjev_tool/models.py — @models_app.command
download_model  # openjev_tool/models.py — @models_app.command
purge_model  # openjev_tool/models.py — @models_app.command

# --- openjev_tool/config_cli.py — @config_app.command entrypoints ------------
config_path_cmd  # openjev_tool/config_cli.py — @config_app.command
config_init  # openjev_tool/config_cli.py — @config_app.command
config_show  # openjev_tool/config_cli.py — @config_app.command
config_set  # openjev_tool/config_cli.py — @config_app.command

# --- ModelBackend protocol members (structural typing; never called here) ---
load  # openjev_tool/backend.py — ModelBackend protocol method
unload  # openjev_tool/backend.py — ModelBackend protocol method
is_loaded  # openjev_tool/backend.py — ModelBackend protocol method
predict_probs  # openjev_tool/backend.py — ModelBackend protocol method

# --- v2 APIs consumed by server.py / menubar.py (later phases) ---------------
loaded_model_count  # openjev_tool/backend.py — /stats residency count
unload_all  # openjev_tool/backend.py — server shutdown hook
supports  # openjev_tool/config.py — ModelEntry.supports, router capability filter
is_downloaded  # openjev_tool/registry.py — /v1/models cache flag
DEFAULT_MODEL_REPO  # openjev_tool/jev.py — v1 API compat re-export (tests import it)
_model_repo  # openjev_tool/jev.py — v1 API compat re-export (tests import it)
_model_revision  # openjev_tool/jev.py — v1 API compat re-export (tests import it)

# --- openjev_tool/server.py — FastAPI route handlers (registered dynamically) -
health  # openjev_tool/server.py — @app.get("/health")
levels  # openjev_tool/server.py — @app.get("/v1/levels")
reload_config  # openjev_tool/server.py — @app.get("/v1/reload")
get_config  # openjev_tool/server.py — @app.get("/v1/config")
put_config  # openjev_tool/server.py — @app.put("/v1/config")
ask  # openjev_tool/server.py — @app.post("/v1/ask")
invoke  # openjev_tool/server.py — @app.post("/v1/invoke")
root  # openjev_tool/server.py — @app.get("/") playground redirect

# --- Typer option params -----------------------------------------------------
version  # openjev_tool/cli.py — --version option param
menubar  # openjev_tool/cli.py — @app.command (menu bar controller)

# --- openjev_tool/downloader.py — DownloadProgress field consumed by renderers -
elapsed_s  # openjev_tool/downloader.py — DownloadProgress dataclass field

# --- TelemetryService public API (consumed by external callers) -------------
meter  # property
otel_logger  # property
reset  # method
