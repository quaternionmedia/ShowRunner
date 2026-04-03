# Getting Started with ShowRunner

ShowRunner is a Python-based platform for managing live performances. It provides a plugin-driven API server, a CLI, and a shared database that tools like **ShowScripter**, **ShowDesigner**, **ShowMixer**, and more all build on top of.

---

## Requirements

| Requirement | Minimum |
|---|---|
| Python | 3.12 |
| [uv](https://docs.astral.sh/uv/) | latest |

Install `uv` if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## Installation

Clone the repository and sync all dependencies (including development tools):

```bash
git clone https://github.com/quaternionmedia/ShowRunner.git
cd ShowRunner
uv sync
```

`uv sync` creates a `.venv` and installs everything declared in `pyproject.toml`, including the `dev` group (`uvicorn`, `pytest`, `ruff`, `black`) and `nicegui` (core dependency for the web UI).

### Optional: Admin Panel

To enable the SQLAdmin web interface, install the `admin` dependency group:

```bash
uv sync --group admin
```

This adds `sqladmin` and `wtforms`. The admin panel will then be available at `/admin` when the server is running.

---

## Starting the Server

The `sr` CLI is the main entry point.

```bash
# Start the API server on http://localhost:8000
uv run sr start
```

Or run the dev helper script directly:

```bash
uv run python scripts/dev
```

Once running, open:

| URL | Description |
|---|---|
| http://localhost:8000 | Dashboard (show selector) |
| http://localhost:8000/script | Script viewer and cue editor |
| http://localhost:8000/admin | Admin panel *(requires admin group)* |
| http://localhost:8000/docs | FastAPI / OpenAPI interactive docs |
| http://localhost:8000/openapi.json | Raw OpenAPI schema |

---

## Creating Your First Show

Use the `create` command to register a new show in the local SQLite database:

```bash
uv run sr create "ShakeSpear" --venue "The Globe Theatre"
# Created show "ShakeSpear" (id=1)
```

You can omit `--venue` if you don't have one yet.

---

## CLI Reference

```
sr --help
```

| Command | Description |
|---|---|
| `sr start` | Start the API server |
| `sr create <name>` | Create a show (shorthand) |
| `sr list` | List shows (shorthand) |
| `sr plugins` | Show all loaded plugins |
| `sr shows list` | List all shows |
| `sr shows create <name>` | Create a show |
| `sr shows info <id>` | Show details |
| `sr shows delete <id>` | Delete a show |
| `sr scripts list <show-id>` | List scripts for a show |
| `sr scripts add <show-id> <title>` | Add a script |
| `sr scripts delete <id>` | Delete a script |
| `sr cue-lists list <show-id>` | List cue lists |
| `sr cue-lists create <show-id> <name>` | Create a cue list |
| `sr cues list <cue-list-id>` | List cues |
| `sr cues add <cue-list-id> <num> <name>` | Add a cue |

## Running Tests

```bash
uv run pytest
```

---

## Next Steps

- [Cookbook](cookbook.md) – common tasks and patterns
- [Plugin Load Demo](plugin-load-demo.md) – step-by-step walkthrough of the plugin system
- [Plugin Architecture](architecture/plugin-architecture.md) – deep dive into how plugins work
