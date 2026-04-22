# UI Testing

ShowRunner uses two complementary test layers for front-end coverage, matching the modular plugin architecture: **page-level UI tests** that run without a browser, and **Playwright end-to-end tests** that drive a real Chromium browser against a live server.

---

## Overview

| Layer | Tool | Speed | Requires browser | Requires server |
|---|---|---|---|---|
| UI (page-level) | NiceGUI `user_simulation` | Fast (~10 s for 16 tests) | No | No |
| E2E | Playwright | Slower | Yes | Yes |

Both layers follow the same modular principle as the plugin system: each plugin has its own isolated test fixture, seeded DB, and page registration — tests never share server state.

---

## Page-level UI Tests

These tests use NiceGUI's built-in simulation infrastructure to render pages in-process and drive them through Python calls.  No browser, no server, no real DOM.  They cover page structure, button interactions, feedback labels, and cue-firing flows.

### Install

```bash
uv sync --group ui-test
```

### Run

```bash
uv run pytest tests/ui/ -v
# or with marker filter:
uv run pytest -m ui -v
```

### Architecture

The key is `plugin_simulation`, a custom async context manager defined in `tests/ui/conftest.py`.  It mirrors NiceGUI's `user_simulation` but injects the plugin's `_build_page()` call *after* the global route registry is cleared and *before* the lifespan starts:

```python
@asynccontextmanager
async def plugin_simulation(setup_fn: Callable) -> AsyncGenerator:
    with nicegui_reset_globals():          # clears all @ui.page routes
        prepare_simulation()
        ui.run(None, storage_secret='test-secret')
        setup_fn()                          # registers this plugin's page only
        async with core.app.router.lifespan_context(core.app):
            async with httpx.AsyncClient(...) as client:
                yield User(client)
```

This mirrors how each plugin registers its page in production (`showrunner_startup` → `_build_page()`), giving each test an isolated NiceGUI app that knows about only one plugin's routes.

### Fixtures

Each plugin has a dedicated pytest fixture in `tests/ui/conftest.py`:

| Fixture | Page | Yields |
|---|---|---|
| `programmer_ui` | `/programmer` | `(user, show_id, cue_list_id)` |
| `scripter_ui` | `/script` | `(user, show_id)` |
| `dashboard_ui` | `/` | `user` |

Every fixture:

1. Creates a fresh `ShowDatabase` in `pytest`'s `tmp_path`
2. Seeds the minimum data needed for the page to render
3. Patches `showrunner.plugins.db._db` so `get_db()` returns the test DB
4. For the programmer: also sets `_app_ref` to a `MockApp(db=test_db)` so REST route functions called directly from the page work
5. Resets all programmer timing/pointer globals to a clean baseline
6. Calls `plugin_simulation(plugin._build_page)` and yields the `User`
7. Restores all patches after the test

### Important: importlib for post-eviction imports

`nicegui_reset_globals` evicts `showrunner` and its sub-packages from `sys.modules` as part of cleanup.  After eviction, the `import a.b.c as name` bytecode form fails because it tries `getattr(showrunner, 'plugins')` on the newly-reimported `showrunner` package before its subpackages are set.

Always use `importlib.import_module` inside fixtures that run after another plugin test:

```python
# WRONG — breaks after module eviction:
import showrunner.plugins.db as db_module

# CORRECT:
import importlib
db_module = importlib.import_module("showrunner.plugins.db")
```

### Writing a test

```python
import pytest
pytestmark = pytest.mark.ui

async def test_page_loads(programmer_ui):
    user, show_id, cl_id = programmer_ui
    await user.open("/programmer")
    await user.should_see("Programmer")

async def test_go_fires_cue(programmer_ui):
    from nicegui import ui
    user, show_id, cl_id = programmer_ui
    await user.open("/programmer")
    user.find(kind=ui.button, content="GO").click()
    # _on_go is async — should_see retries until the feedback label appears
    await user.should_see("GO →")
```

**Key patterns:**

- `await user.open("/path")` — navigate to the page
- `await user.should_see("text")` — assert element visible (retries 3× with 0.1 s gap)
- `await user.should_not_see("text")` — assert element absent
- `user.find(kind=ui.button, content="GO").click()` — trigger a button
- `user.find(kind=ui.select).click()` — open a select dropdown
- For async handlers (like GO), the handler is scheduled as a background task; `should_see` polling handles the gap automatically

### Adding a fixture for a new plugin

1. Add a fixture function in `tests/ui/conftest.py`:

```python
@pytest.fixture
async def my_plugin_ui(tmp_path):
    db_module = importlib.import_module("showrunner.plugins.db")
    my_plugin = importlib.import_module("showrunner.plugins.my_plugin")

    db = ShowDatabase(tmp_path / "test.db")
    db.create_schema()
    with db.session() as s:
        # seed minimum data
        ...

    original_db = db_module._db
    db_module._db = db

    async with plugin_simulation(my_plugin._build_page) as user:
        yield user

    db_module._db = original_db
    db.close()
```

2. Create `tests/ui/test_my_plugin.py` with `pytestmark = pytest.mark.ui`.

---

## End-to-End (Playwright) Tests

These tests open a real Chromium browser against a live ShowRunner server.  They verify visual state, keyboard shortcuts, and anything that requires a real DOM.

### Install

```bash
uv sync --group e2e
playwright install chromium
```

### Run

```bash
uv run pytest tests/e2e/ -v
# headed (see the browser):
uv run pytest tests/e2e/ -v --headed
```

!!! note "Requires seeded data"
    E2E tests need a running server with data.  The `live_server` fixture in `tests/e2e/conftest.py` starts a subprocess server with a temporary DB.  You may need to seed it with `python examples/setup_intro.py --db /path/to/e2e.db` or adapt the fixture to seed data programmatically.

### Architecture

The `live_server` session-scoped fixture in `tests/e2e/conftest.py` starts a subprocess:

```python
@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("e2e") / "e2e.db"
    proc = subprocess.Popen(["uv", "run", "sr", "start", "--db", str(db_path)], ...)
    # wait for health endpoint, yield base_url, terminate after session
```

Tests receive a `page` fixture from Playwright and the `live_server` base URL:

```python
def test_go_click_fires_cue(page, live_server):
    page.goto(f"{live_server}/programmer")
    page.wait_for_selector("text=Programmer")
    page.get_by_role("button", name="GO").click()
    page.wait_for_selector("text=GO →", timeout=3000)
```

### Writing an E2E test

```python
import pytest
pytestmark = pytest.mark.e2e

@pytest.fixture
def programmer_page(page, live_server):
    page.goto(f"{live_server}/programmer")
    page.wait_for_selector("text=Programmer", timeout=5000)
    return page

def test_space_shortcut_fires_go(programmer_page):
    programmer_page.keyboard.press("Space")
    programmer_page.wait_for_selector("text=GO →", timeout=3000)
```

---

## Running both layers together

```bash
# UI tests only (fast, no browser)
uv run pytest -m ui -v

# E2E only (requires live server)
uv run pytest -m e2e -v

# Everything except E2E (CI-friendly)
uv run pytest -m "not e2e" -v
```

---

## See also

- [Plugin Architecture](../about/plugin-architecture.md) — how `_build_page()` fits into the plugin lifecycle
- [Creating Plugins](plugins-create.md) — adding a new plugin with its own test fixture
- [Tests cookbook](tests.md) — API and database testing patterns
