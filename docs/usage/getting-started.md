# Getting Started

ShowRunner is a Python-based platform for managing live performances. It provides a plugin-driven API server, a CLI, and a shared database that tools like **ShowScripter**, **ShowDesigner**, **ShowMixer**, and more all build on top of.

---

## Installation

### with pip

```bash
pip install showrunner
```

### with uv (recommended for development)

Install `uv` if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

#### Clone the repo:

```bash
git clone https://github.com/quaternionmedia/ShowRunner.git
cd ShowRunner
```

### Install dependencies and dev tools

```bash
uv sync
```

#### Optional dependencies:

Install all optional dependencies, including the admin panel:

```bash
uv sync --all-extras
```

### Activate the virtual environment

This is optional, but strongly recommended. You can also run commands with `uv run [cmd]` if not activated.

```bash
source .venv/bin/activate
```

### Start the API server

```bash
sr start
```

Visit [http://localhost:8000](http://localhost:8000) to access the dashboard and tools.

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
sr start
```

Or run the dev helper script directly:

```bash
uv run scripts/dev
```

Once running, open: [http://localhost:8000](http://localhost:8000) to access the dashboard and tools.

| URL                                | Description                          |
| ---------------------------------- | ------------------------------------ |
| http://localhost:8000              | Dashboard (show selector)            |
| http://localhost:8000/script       | Script viewer and cue editor         |
| http://localhost:8000/admin        | Admin panel _(requires admin group)_ |
| http://localhost:8000/docs         | FastAPI / OpenAPI interactive docs   |
| http://localhost:8000/openapi.json | Raw OpenAPI schema                   |

---

## Next Steps

- [First Show Walkthrough](first-show.md) – step-by-step guide to creating and running your first show
- [Cookbook](cookbook.md) – common tasks and patterns
- [Plugin Architecture](architecture/plugin-architecture.md) – deep dive into how plugins work
