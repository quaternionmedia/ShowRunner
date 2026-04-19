# Getting Started

ShowRunner is a Python-based platform for managing live performances. It provides a plugin-driven API server, a CLI, and a shared database that tools like **ShowScripter**, **ShowDesigner**, **ShowMixer**, and more all build on top of.

---

## Installation

### with pip

```bash
pip install showrunner[all]
```

!!! note "[all] extra"

    The `[all]` extra includes optional dependencies like an admin panel, CLI, and other tools. You can also install just the core API with `pip install showrunner` and add extras as needed.

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

### Create a virtual environment (recommended)

This is optional, but strongly recommended.

```bash
uv venv
source .venv/bin/activate
```

!!! note "Using uv without a venv"

    If you choose not to use a virtual environment, you can run commands with `uv run [cmd]` instead of just `[cmd]` (e.g. `uv run sr start` instead of `sr start`).

### Install dependencies and dev tools

```bash
uv sync --all-extras
```

!!! note "Core dependencies"

    If you want to choose which extra dependencies to install, you can run `uv sync --extra [extra_name]` for each extra group (e.g. `admin`, `cli`, etc.) or just `uv sync` to install only the core API dependencies and dev tools.

### Start the API server

```bash
sr start
```

Visit [http://localhost:8000](http://localhost:8000) to access the dashboard and tools.

---

## Starting the Server

The `sr` CLI is the main entry point.

```bash
# Start the API server on http://localhost:8000
sr start
```

Or run with `uv run` without activating the environment:

```bash
uv run sr start
```

Once running, open [http://localhost:8000](http://localhost:8000) to access the dashboard and tools.

| URL                                | Description                              |
| ---------------------------------- | ---------------------------------------- |
| http://localhost:8000              | Dashboard (show selector)                |
| http://localhost:8000/script       | ShowScripter — script viewer + cue editor |
| http://localhost:8000/programmer   | ShowProgrammer — GO panel + cue clock    |
| http://localhost:8000/admin        | Admin panel _(requires `admin` group)_   |
| http://localhost:8000/docs         | FastAPI / OpenAPI interactive docs       |
| http://localhost:8000/openapi.json | Raw OpenAPI schema                       |

---

## Next Steps

- [First Show Walkthrough](first-show.md) – step-by-step guide to creating and running your first show
- [Cookbook](../cookbook/index.md) – common tasks and patterns
- [Plugin Architecture](../about/plugin-architecture.md) – deep dive into how plugins work
