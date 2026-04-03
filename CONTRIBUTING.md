# Contributing to ShowRunner

Thank you for your interest in contributing! This guide covers environment setup, project conventions, and the plugin architecture so you can start contributing quickly.

---

## Table of Contents

- [Requirements](#requirements)
- [Dev Environment Setup](#dev-environment-setup)
- [Running the App](#running-the-app)
- [Running Tests](#running-tests)
- [Linting & Formatting](#linting--formatting)
- [Project Layout](#project-layout)
- [Writing a Plugin](#writing-a-plugin)
- [Coding Conventions](#coding-conventions)
- [Submitting Changes](#submitting-changes)

---

## Requirements

| Tool | Version |
|---|---|
| Python | ≥ 3.12 |
| [uv](https://docs.astral.sh/uv/) | latest |

Install `uv` once (it manages Python and all virtual environments):

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

---

## Dev Environment Setup

```bash
git clone https://github.com/quaternionmedia/ShowRunner.git
cd ShowRunner

# Install all dependencies including dev tools (pytest, ruff, black, uvicorn)
uv sync
```

> **Optional admin panel** (SQLAdmin web interface):
> ```bash
> uv sync --group admin
> ```

`uv sync` creates `.venv/` in the project root and installs everything declared in `pyproject.toml`. You do not need to activate the virtual environment manually — all `uv run` commands use it automatically.

---

## Running the App

```bash
# Start the API server (http://localhost:8000)
uv run sr start

# Or use the dev helper script (same thing, no uv overhead)
uv run python scripts/dev
```

Useful URLs once running:

| URL | Description |
|---|---|
| http://localhost:8000 | Dashboard |
| http://localhost:8000/script | Script viewer / cue editor |
| http://localhost:8000/docs | Interactive OpenAPI docs |
| http://localhost:8000/admin | Admin panel *(requires admin group)* |

---

## Running Tests

```bash
uv run pytest
```

All tests live under `tests/`. The test suite uses `tmp_path` fixtures and in-process `TestClient` — no network or external services are needed.

**Individual test files:**

| File | What it covers |
|---|---|
| `tests/test_api.py` | HTTP routes via `TestClient` |
| `tests/test_database.py` | `ShowDatabase` CRUD operations |
| `tests/test_models.py` | Model `__str__` and field behaviour |
| `tests/test_plugins.py` | Plugin registration and hook plumbing |

Run a specific file or test:

```bash
uv run pytest tests/test_api.py
uv run pytest tests/test_api.py::test_create_show_with_venue -v
```

---

## Linting & Formatting

```bash
# Check for lint issues
uv run ruff check src/ tests/

# Auto-fix safe issues (import sorting, unused imports, etc.)
uv run ruff check --fix src/ tests/

# Format code
uv run black src/ tests/
```

The project targets a **100-character line length**. Configured in `pyproject.toml` under `[tool.ruff]`.

Inline JavaScript strings embedded in NiceGUI event handlers (`scripter.py`) are exempt from the line-length rule — they cannot be split without changing behaviour.

---

## Project Layout

```
src/showrunner/
├── __init__.py         # Public API: hookimpl marker + ShowRunner re-export
├── app.py              # ShowRunner class and get_plugin_manager()
├── cli.py              # Typer CLI (sr command)
├── database.py         # ShowDatabase — engine/session management
├── hookspecs.py        # pluggy hook contract (ShowRunnerSpec)
├── models.py           # SQLModel table definitions
└── plugins/
    ├── __init__.py     # Built-in plugin registry (get_builtin_plugins)
    ├── db.py           # ShowDB — SQLite backend + FastAPI routes
    ├── dashboard.py    # ShowDashboard — NiceGUI web dashboard
    ├── scripter.py     # ShowScripter — script viewer and cue editor
    ├── admin.py        # ShowAdmin — SQLAdmin panel (optional)
    └── ...             # One file per plugin
```

**Startup order** is enforced by pluggy's `tryfirst`/`trylast` markers, not by import order:

1. **ShowDB** (`tryfirst`) — opens the database, sets `app.db`
2. All other plugins — can safely access `app.db`
3. **ShowAdmin**, **ShowDashboard** (`trylast`) — mount views after DB is ready

---

## Writing a Plugin

A plugin is a plain Python class decorated with `@showrunner.hookimpl` on each hook it implements. Plugins do not need to extend a base class.

### Minimal plugin example

```python
# my_plugin/__init__.py
import showrunner
from fastapi import APIRouter

router = APIRouter(prefix="/my-plugin", tags=["MyPlugin"])

@router.get("/")
async def index():
    return {"plugin": "MyPlugin", "status": "ok"}


class MyPlugin:
    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "MyPlugin",
            "description": "Does something useful",
            "version": "0.1.0",
        }

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        return router

    @showrunner.hookimpl
    def showrunner_startup(self, app):
        # Called when ShowRunner starts — set up resources here
        pass

    @showrunner.hookimpl
    def showrunner_shutdown(self, app):
        # Called when ShowRunner stops — clean up here
        pass
```

### Register via entry points (external packages)

Add this to your plugin's `pyproject.toml`:

```toml
[project.entry-points."showrunner"]
my_plugin = "my_plugin:MyPlugin"
```

Install into the same environment:

```bash
uv pip install -e ./my_plugin
```

ShowRunner automatically discovers entry-point plugins via `pm.load_setuptools_entrypoints("showrunner")`.

### Available hooks

See [`src/showrunner/hookspecs.py`](src/showrunner/hookspecs.py) for full docstrings.

| Hook | When to implement |
|---|---|
| `showrunner_register()` | Always — return `name`, `description`, `version` dict |
| `showrunner_startup(app)` | Open DB connections, register pages, etc. |
| `showrunner_shutdown(app)` | Release resources, close connections |
| `showrunner_get_routes()` | Return a `fastapi.APIRouter` (or `None`) |
| `showrunner_get_commands()` | Return a list of CLI command dicts |
| `showrunner_command(name, **kwargs)` | Handle a broadcast command *(future)* |
| `showrunner_query(name, **kwargs)` | Answer a broadcast query *(future)* |
| `showrunner_event(name, **kwargs)` | React to a broadcast event *(future)* |

### Accessing the database from a plugin

During `showrunner_startup`, `app.db` is the live `ShowDatabase` instance (set by `ShowDB`, which runs `tryfirst`):

```python
@showrunner.hookimpl
def showrunner_startup(self, app):
    db = app.db          # ShowDatabase instance
    with db.session() as s:
        ...
```

---

## Coding Conventions

- **Python ≥ 3.12** — use modern union syntax (`X | Y`) instead of `Optional[X]` in new code. (`Optional` is still used in the CLI layer for typer compatibility.)
- **SQLModel** for all DB models — no raw SQLAlchemy ORM classes.
- **FastAPI** for HTTP routes — define them on an `APIRouter` and return it from `showrunner_get_routes`.
- **NiceGUI** for UI pages — call `ui.page(...)` inside the plugin's `showrunner_startup`.
- **No global state** — plugins access the database via `app.db`, not a module-level singleton (except the `_db` reference in `db.py` which is set during startup).
- **Docstrings** on all public functions and classes.

---

## Submitting Changes

1. Fork the repo and create a feature branch.
2. Make your changes and add or update tests.
3. Run `uv run pytest` — all tests must pass.
4. Run `uv run ruff check src/ tests/` — no new lint errors.
5. Open a pull request with a clear description of what changed and why.
