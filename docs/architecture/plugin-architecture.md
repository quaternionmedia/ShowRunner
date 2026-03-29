# ShowRunner Plugin Architecture

## Overview

ShowRunner uses [pluggy](https://pluggy.readthedocs.io/) to implement a plugin-based architecture where each tool (ShowScripter, ShowMixer, etc.) is a self-contained plugin that registers with the core application through a well-defined set of hooks.

```mermaid
graph TB
    subgraph Core ["ShowRunner Core"]
        APP[ShowRunnerApp]
        PM[PluginManager<br/><i>pluggy</i>]
        SPECS[ShowRunnerSpec<br/><i>hookspecs</i>]
        API[FastAPI App]
    end

    APP --> PM
    APP --> API
    PM --> SPECS

    subgraph Hooks ["Hook Specifications"]
        H1["showrunner_register()"]
        H2["showrunner_startup(app)"]
        H3["showrunner_shutdown(app)"]
        H4["showrunner_get_routes()"]
        H5["showrunner_get_commands()"]
    end

    SPECS --> H1
    SPECS --> H2
    SPECS --> H3
    SPECS --> H4
    SPECS --> H5

    subgraph Plugins ["Built-in Plugins"]
        P1[ShowScripter<br/>/scripter]
        P2[ShowDesigner<br/>/designer]
        P3[ShowProgrammer<br/>/programmer]
        P4[ShowMixer<br/>/mixer]
        P5[ShowLighter<br/>/lighter]
        P6[ShowManager<br/>/manager]
        P7[ShowStopper<br/>/stopper]
        P8[ShowPrompter<br/>/prompter]
        P9[ShowComms<br/>/comms]
        P10[ShowCmd<br/>/cmd]
        P11[ShowRecorder<br/>/recorder]
    end

    PM --> P1
    PM --> P2
    PM --> P3
    PM --> P4
    PM --> P5
    PM --> P6
    PM --> P7
    PM --> P8
    PM --> P9
    PM --> P10
    PM --> P11

    EXT[External Plugins<br/><i>setuptools entry points</i>]
    PM -.->|"load_setuptools_entrypoints<br/>('showrunner')"| EXT

    H4 -->|"APIRouter"| API

    style Core fill:#1a1a2e,stroke:#e94560,color:#eee
    style Hooks fill:#16213e,stroke:#0f3460,color:#eee
    style Plugins fill:#0f3460,stroke:#533483,color:#eee
    style EXT fill:#533483,stroke:#e94560,color:#eee
```

## Hook Specifications

All hooks are defined in `src/showrunner/hookspecs.py` and prefixed with `showrunner_`.

| Hook                        | Purpose                             | Returns                                      |
| --------------------------- | ----------------------------------- | -------------------------------------------- |
| `showrunner_register()`     | Report plugin metadata              | `dict` with `name`, `description`, `version` |
| `showrunner_startup(app)`   | Initialize resources at app startup | —                                            |
| `showrunner_shutdown(app)`  | Release resources at app shutdown   | —                                            |
| `showrunner_get_routes()`   | Provide HTTP endpoints              | `fastapi.APIRouter` or `None`                |
| `showrunner_get_commands()` | Provide CLI/TUI commands            | `list[dict]`                                 |

## Plugin Lifecycle

```mermaid
sequenceDiagram
    participant User
    participant App as ShowRunnerApp
    participant PM as PluginManager
    participant P as Plugin

    User->>App: ShowRunnerApp()
    App->>PM: get_plugin_manager()
    PM->>PM: add_hookspecs(ShowRunnerSpec)
    PM->>P: register(plugin_instance)
    PM->>PM: load_setuptools_entrypoints("showrunner")
    App->>PM: hook.showrunner_get_routes()
    PM->>P: showrunner_get_routes()
    P-->>PM: APIRouter
    PM-->>App: [routers]
    App->>App: include_router(router)

    User->>App: startup()
    App->>PM: hook.showrunner_startup(app=self)
    PM->>P: showrunner_startup(app)

    Note over App,P: Application runs...

    User->>App: shutdown()
    App->>PM: hook.showrunner_shutdown(app=self)
    PM->>P: showrunner_shutdown(app)
```

## Project Layout

```
src/showrunner/
├── __init__.py          # Public API: hookimpl marker, ShowRunnerApp
├── hookspecs.py         # Hook specifications (the plugin contract)
├── app.py               # Core app: PluginManager + FastAPI wiring
└── plugins/
    ├── __init__.py      # Built-in plugin discovery
    ├── scripter.py      # ShowScripter  - /scripter
    ├── designer.py      # ShowDesigner  - /designer
    ├── programmer.py    # ShowProgrammer - /programmer
    ├── mixer.py         # ShowMixer     - /mixer
    ├── lighter.py       # ShowLighter   - /lighter
    ├── stage_manager.py # ShowManager   - /manager
    ├── stopper.py       # ShowStopper   - /stopper
    ├── prompter.py      # ShowPrompter  - /prompter
    ├── comms.py         # ShowComms     - /comms
    ├── cmd.py           # ShowCmd       - /cmd
    └── recorder.py      # ShowRecorder  - /recorder
```

## Writing an External Plugin

Third-party plugins are discovered via **setuptools entry points**. Create a package with a `pyproject.toml`:

```toml
[project]
name = "showrunner-myplugin"
dependencies = ["showrunner"]

[project.entry-points.showrunner]
myplugin = "showrunner_myplugin"
```

Then implement the hooks using the `@showrunner.hookimpl` decorator:

```python
import showrunner
from fastapi import APIRouter

router = APIRouter(prefix="/myplugin", tags=["MyPlugin"])

@router.get("/")
async def index():
    return {"plugin": "MyPlugin"}

class MyPlugin:
    @showrunner.hookimpl
    def showrunner_register(self):
        return {"name": "MyPlugin", "description": "...", "version": "0.1.0"}

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        return router
```

Install the package alongside ShowRunner and it will be automatically discovered.
