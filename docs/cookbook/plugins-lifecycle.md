# Plugin Lifecycle

A complete walkthrough of the ShowRunner plugin lifecycle — from loading built-in plugins to registering an external one and verifying everything is wired up correctly.

---

## Overview

ShowRunner uses [pluggy](https://pluggy.readthedocs.io/) for its plugin system. The lifecycle looks like this:

```
get_plugin_manager()
  └─ pm.add_hookspecs(ShowRunnerSpec)      # register the contract
  └─ pm.register(plugin_instance)  × 12   # register each built-in
  └─ pm.load_setuptools_entrypoints(...)   # auto-discover external plugins

ShowRunner.__init__()
  └─ _mount_routes()                       # collect FastAPI routers from plugins

ShowRunner.startup()
  └─ pm.hook.showrunner_startup(app=self)  # broadcast startup to all plugins
```

---

## Step 1 – Import and Inspect the Plugin Manager

```python
from showrunner.app import get_plugin_manager

pm = get_plugin_manager()

print("Registered plugins:")
for plugin in pm.get_plugins():
    name = pm.get_name(plugin)
    print(f"  {name}")
```

Expected output (order may vary):

```
Registered plugins:
  ShowScripterPlugin
  ShowDesignerPlugin
  ShowProgrammerPlugin
  ShowMixerPlugin
  ShowLighterPlugin
  ShowManagerPlugin
  ShowStopperPlugin
  ShowPrompterPlugin
  ShowCommsPlugin
  ShowCmdPlugin
  ShowRecorderPlugin
  ShowDBPlugin
  ShowAdminPlugin
  ShowDashboardPlugin
```

---

## Step 2 – Call the Register Hook

The `showrunner_register` hook asks each plugin to return its metadata. pluggy collects all return values into a list.

```python
from showrunner.app import get_plugin_manager

pm = get_plugin_manager()
metadata_list = pm.hook.showrunner_register()

print(f"{'Name':<20} {'Version':<10} Description")
print("-" * 70)
for meta in metadata_list:
    print(f"{meta['name']:<20} {meta['version']:<10} {meta['description']}")
```

Expected output:

```
Name                 Version    Description
----------------------------------------------------------------------
ShowScripter         0.1.0      Script viewer and OCR parser for cue management
ShowDesigner         0.1.0      Cue design from parsed scripts with layer integration
ShowProgrammer       0.1.0      Synchronization with QLab and other cue tools
ShowMixer            0.1.0      Sound mixer monitoring and control (Behringer, A&H, …)
ShowLighter          0.1.0      Lighting control integration (ETC Eos, Chamsys, MA, …)
ShowManager          0.1.0      Stage management and cue triggering for live shows
ShowStopper          0.1.0      Stopwatch, logging, and cue timing tools
ShowPrompter         0.1.0      Teleprompter for scripts and cues
ShowComms            0.1.0      Crew communication, messaging, and cue notifications
ShowCmd              0.1.0      CLI and TUI interface for ShowRunner
ShowRecorder         0.1.0      Archive, annotate, and review rehearsals
ShowDB               0.1.0      SQLite database backend for shows, cues, and logs
ShowAdmin            0.1.0      SQLAdmin web interface (optional — requires admin group)
ShowDashboard        0.1.0      NiceGUI web dashboard for show control
```

---

## Step 3 – Create ShowRunner and Inspect Mounted Routes

`ShowRunner.__init__` wires every plugin's `showrunner_get_routes` return value into the FastAPI application as an `APIRouter`.

```python
from showrunner import ShowRunner

app = ShowRunner()

print("Mounted API routes:")
for route in app.api.routes:
    methods = getattr(route, "methods", {"-"})
    print(f"  {sorted(methods)} {route.path}")
```

Expected output (subset):

```
Mounted API routes:
  ['GET'] /docs
  ['GET'] /openapi.json
  ['GET'] /db/shows
  ['GET'] /db/shows/{show_id}
  ['POST'] /db/shows
  ['GET'] /db/shows/{show_id}/cues
  ['GET'] /recorder/
  ['GET'] /cmd/
  ['GET'] /mixer/
  ['GET'] /lighter/
  ['GET'] /stopper/
  ...
```

> **Note:** The NiceGUI pages (`/`, `/script`) and the admin panel (`/admin`) are mounted during `startup()` via NiceGUI's `ui.run_with()` and SQLAdmin's `Admin()` — not as standard FastAPI `APIRouter`s — so they won't appear in the route list above.

---

## Step 4 – Trigger the Startup Hook

The startup hook notifies all plugins that the application is ready. Plugins use this to open connections, initialize state, etc.

```python
from showrunner import ShowRunner

app = ShowRunner()
app.startup()   # broadcasts showrunner_startup(app=app) to all plugins

print("ShowRunner is running.")

# ... do work ...

app.shutdown()  # broadcasts showrunner_shutdown(app=app) to all plugins
print("ShowRunner has stopped.")
```

---

## Step 5 – Register an External Plugin at Runtime

You can register additional plugins into an existing `PluginManager` at any time — no restart required.

```python
import showrunner
from showrunner.app import get_plugin_manager
from fastapi import APIRouter

# 1. Define the plugin
class DemoPlugin:
    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "DemoPlugin",
            "description": "A live-registered demonstration plugin",
            "version": "0.0.1",
        }

    @showrunner.hookimpl
    def showrunner_startup(self, app):
        print("[DemoPlugin] startup called")

    @showrunner.hookimpl
    def showrunner_shutdown(self, app):
        print("[DemoPlugin] shutdown called")

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        router = APIRouter(prefix="/demo", tags=["DemoPlugin"])

        @router.get("/")
        async def index():
            return {"plugin": "DemoPlugin", "status": "ok", "message": "Hello from the demo!"}

        return router

# 2. Get the plugin manager and register
pm = get_plugin_manager()
pm.register(DemoPlugin())

# 3. Verify registration
meta = pm.hook.showrunner_register()
demo_meta = next(m for m in meta if m["name"] == "DemoPlugin")
print("Registered:", demo_meta)

# 4. Wire it up (normally done inside ShowRunner.__init__)
from showrunner import ShowRunner
app = ShowRunner()
# Note: to pick up the demo plugin's routes, rebuild after registering it on app.pm
pm2 = app.pm
pm2.register(DemoPlugin())
# Re-include routes manually:
routers = pm2.hook.showrunner_get_routes()
for router in routers:
    if router is not None:
        try:
            app.api.include_router(router)
        except Exception:
            pass  # Already included

app.startup()
print("All plugins after registration:")
for m in app.list_plugins():
    print(f"  {m['name']}")  # 15 plugins total (14 built-in + DemoPlugin)
app.shutdown()
```

---

## Step 6 – Verify with the Running Server

Start the server and hit the endpoints to confirm all plugins are active:

```bash
sr start &
sleep 2

# REST endpoints
curl -s http://localhost:8000/db/shows   | python -m json.tool
curl -s http://localhost:8000/recorder/  | python -m json.tool
curl -s http://localhost:8000/cmd/       | python -m json.tool
curl -s http://localhost:8000/mixer/     | python -m json.tool
curl -s http://localhost:8000/lighter/   | python -m json.tool
curl -s http://localhost:8000/stopper/   | python -m json.tool

# NiceGUI web pages (browser only)
open http://localhost:8000/          # Dashboard
open http://localhost:8000/script    # Script viewer
open http://localhost:8000/admin     # Admin panel (requires: uv sync --group admin)

# FastAPI docs
open http://localhost:8000/docs
```

---

## Hook Reference

| Hook                                         | Called by                    | Purpose                                |
| -------------------------------------------- | ---------------------------- | -------------------------------------- |
| `showrunner_register()`                      | App inspection               | Return plugin name/description/version |
| `showrunner_startup(app)`                    | `ShowRunner.startup()`       | Initialize plugin resources            |
| `showrunner_shutdown(app)`                   | `ShowRunner.shutdown()`      | Release plugin resources               |
| `showrunner_get_routes()`                    | `ShowRunner._mount_routes()` | Return a `fastapi.APIRouter`           |
| `showrunner_get_commands()`                  | `ShowRunner.list_commands()` | Return CLI command descriptors         |
| `showrunner_command(command_name, **kwargs)` | Broadcast                    | Receive a command from the system      |
| `showrunner_query(query_name, **kwargs)`     | Broadcast                    | Answer a query from the system         |
| `showrunner_event(event_name, **kwargs)`     | Broadcast                    | Receive a system event                 |

All hooks are optional — a plugin only needs to implement the hooks relevant to its function.

---

## See Also

- [Getting Started](getting-started.md)
- [Cookbook](cookbook.md)
- [Plugin Architecture](architecture/plugin-architecture.md)
