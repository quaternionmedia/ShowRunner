# Testing

This section covers how to run tests in ShowRunner, both for the core application and for plugins.

### Run the full test suite

```bash
uv run pytest
```

This includes the NiceGUI page tests in `tests/ui/`, which render a plugin's page
in NiceGUI's user simulation, with no browser and no server.

### Test a page without a browser

Each fixture in `tests/ui/conftest.py` seeds a fresh database and registers one
plugin's page:

```python
async def test_layer_buttons_present(scripter_ui):
    user, _show_id = scripter_ui
    await user.open("/script")
    for layer in ("Lights", "Sound", "Video", "Audio", "Stage"):
        await user.should_see(layer)
```

### Run the browser tests

`tests/e2e/` starts `sr start` on a free port with its own database and drives
it in Chromium. It is not part of `uv run pytest`; run it on its own:

```bash
uv sync --group e2e
uv run playwright install chromium
uv run pytest tests/e2e
```

Set `SHOWRUNNER_E2E_PORT` to pin the server's port.

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
