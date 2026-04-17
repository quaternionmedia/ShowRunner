# Create a new plugin

## Quick start (CLI)

The fastest way to create a plugin is with the built-in scaffolding command:

```bash
sr plugin create "My Plugin" -d "Does something useful"
```

This generates a complete package with the entry point pre-configured and
installs it in editable mode. ShowRunner will discover it on the next
`sr start`. See `sr plugin create --help` for all options.

## Manual setup

If you prefer to set things up by hand, create a package with this structure:

```bash
my_plugin/
├── pyproject.toml
└── my_plugin/__init__.py
```

**`my_plugin/__init__.py`**

```python
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
        print("MyPlugin started")

    @showrunner.hookimpl
    def showrunner_shutdown(self, app):
        print("MyPlugin stopped")


plugin = MyPlugin()
```

**`pyproject.toml`** – Register via setuptools entry points so ShowRunner discovers it automatically:

```toml
[project]
name = "my-plugin"
version = "0.1.0"
dependencies = ["showrunner"]

[project.entry-points."showrunner"]
my_plugin = "my_plugin:plugin"
```

Install it into the same environment:

```bash
uv pip install -e ./my_plugin
```

The plugin will be loaded automatically on the next `sr start` via `pm.load_setuptools_entrypoints("showrunner")`.
