# Cookbook

Common patterns and recipes for working with ShowRunner.

---

## Shows & Database

### Create a show via CLI

```bash
sr create "Hamlet" --venue "Globe Theatre"
```

### Create a show programmatically

```python
from showrunner.database import ShowDatabase
from showrunner.models import Show

db = ShowDatabase("show.db")
db.create_schema()

with db.session() as s:
    show = Show(name="Hamlet", venue="Globe Theatre")
    s.add(show)
    s.commit()
    s.refresh(show)
    print(f"Created show id={show.id}")

db.close()
```

### List all shows

```python
from showrunner.database import ShowDatabase

db = ShowDatabase("show.db")
for show in db.list_shows():
    print(show.name, show.venue)
db.close()
```

### Fetch a single show by id

```python
from showrunner.database import ShowDatabase

db = ShowDatabase("show.db")
show = db.get_show(1)
print(show.name)
db.close()
```

---

## Cues & Cue Lists

### Add a cue list and cues to a show

```python
from showrunner.database import ShowDatabase
from showrunner.models import Cue, CueList

db = ShowDatabase("show.db")
db.create_schema()

with db.session() as s:
    cue_list = CueList(show_id=1, name="Act 1")
    s.add(cue_list)
    s.commit()
    s.refresh(cue_list)

    cue = Cue(cue_list_id=cue_list.id, number=1, name="Lights Up")
    s.add(cue)
    s.commit()
    print(f"Cue '{cue.name}' added to list '{cue_list.name}'")

db.close()
```

### Place a cue at a specific script position

Cues can be anchored to a line and character position within a script. The ShowScripter UI reads these fields to render visual cue markers inline with the script text — and updates them when you drag a cue marker to a new position.

```python
cue = Cue(
    cue_list_id=cue_list.id,
    number=5,
    name="Blackout",
    layer="Lights",
    script_line=42,   # line number in the script text
    script_char=0,    # character offset within that line (0 = start of line)
)
```

---

## API – Using the REST Endpoints

Once the server is started (`sr start`), all plugin routes are available.

### List shows via HTTP

```bash
curl http://localhost:8000/db/shows
```

### Create a show via HTTP

```bash
curl -X POST "http://localhost:8000/db/shows?name=Hamlet&venue=Globe%20Theatre"
```

### Check a plugin's status

Every built-in plugin exposes a health/index endpoint at its prefix:

```bash
curl http://localhost:8000/recorder/    # ShowRecorder
curl http://localhost:8000/cmd/         # ShowCmd
curl http://localhost:8000/mixer/       # ShowMixer
```

### Web UI pages

The NiceGUI-powered pages are browser-only (not REST):

| URL                          | Plugin        | Description                                      |
| ---------------------------- | ------------- | ------------------------------------------------ |
| http://localhost:8000/       | ShowDashboard | Show selector and control dashboard              |
| http://localhost:8000/script | ShowScripter  | Script viewer with drag-and-drop cue placement   |
| http://localhost:8000/admin  | ShowAdmin     | SQLAdmin CRUD interface _(requires admin group)_ |

---

## Scripts

### Add a script via CLI

```bash
# Inline content
sr scripts add 1 "Act 1" --format fountain --content "INT. STAGE - DAY"

# From a file on disk
sr scripts add 1 "Full Script" --format fountain --file ./script.fountain
```

### Add a script programmatically

```python
from showrunner.database import ShowDatabase
from showrunner.models import Script

db = ShowDatabase("show.db")
db.create_schema()

with db.session() as s:
    script = Script(
        show_id=1,
        title="Act 1",
        format="fountain",
        content="INT. STAGE - DAY\n\nThe curtain rises.",
    )
    s.add(script)
    s.commit()
    s.refresh(script)
    print(f"Script id={script.id}")

db.close()
```

### List scripts for a show

```bash
sr scripts list 1
```

---

## Admin Panel

The admin panel requires the optional `admin` dependency group:

```bash
uv sync --group admin
sr start
# Then open http://localhost:8000/admin
```

The admin panel provides full CRUD for Shows, Scripts, CueLists, Cues, Actors, CueLogs, and Config.

---

## Working with the ShowRunner Application Object

### Start, inspect, and stop ShowRunner in Python

```python
from showrunner import ShowRunner

app = ShowRunner()
app.startup()

# List all registered plugins
for metadata in app.list_plugins():
    print(metadata["name"], "–", metadata["description"])

# List commands contributed by plugins
for cmd in app.list_commands():
    print(cmd["name"])

app.shutdown()
```

### Emit a command to all plugins

```python
from showrunner import ShowRunner

app = ShowRunner()
app.startup()

# Broadcast a command; plugins that implement showrunner_command will receive it
app.pm.hook.showrunner_command(command_name="go_cue", cue_id=42)

app.shutdown()
```

### Emit an event to all plugins

```python
app.pm.hook.showrunner_event(event_name="cue_completed", cue_id=42)
```

---

## Writing a Minimal External Plugin

Create a package with this structure:

```
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

---

## Testing

### Run the full test suite

```bash
uv run pytest
```

### Test a plugin in isolation

```python
from showrunner.app import get_plugin_manager
import pluggy
import showrunner


class FakePlugin:
    @showrunner.hookimpl
    def showrunner_register(self):
        return {"name": "Fake", "description": "Test plugin", "version": "0.0.1"}


pm = get_plugin_manager()
pm.register(FakePlugin())

results = pm.hook.showrunner_register()
assert any(r["name"] == "Fake" for r in results)
```
