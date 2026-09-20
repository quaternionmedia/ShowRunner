# Testing

This section covers how to run tests in ShowRunner, both for the core application and for plugins.

For page-level NiceGUI UI tests and Playwright end-to-end tests see the dedicated guide: [UI Testing](ui-testing.md).

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
