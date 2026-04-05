# Create a new plugin

Create a package with this structure:

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
```

**`pyproject.toml`** – Register via setuptools entry points so ShowRunner discovers it automatically:

```toml
[project]
name = "my-plugin"
version = "0.1.0"
dependencies = ["showrunner"]

[project.entry-points."showrunner"]
my_plugin = "my_plugin:MyPlugin"
```

Install it into the same environment:

```bash
uv pip install -e ./my_plugin
```

The plugin will be loaded automatically on the next `sr start` via `pm.load_setuptools_entrypoints("showrunner")`.
