# Plugins

This section provides an overview of the plugin architecture in ShowRunner, including how to write and register plugins, and examples of built-in plugins.

## Inspecting loaded plugins

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

## Commands

### Emit a command to all plugins

```python
from showrunner import ShowRunner

app = ShowRunner()
app.startup()

# Broadcast a command; plugins that implement showrunner_command will receive it
app.pm.hook.showrunner_command(command_name="go_cue", cue_id=42)

app.shutdown()
```

## Events

### Emit an event to all plugins

```python
app.pm.hook.showrunner_event(event_name="cue_completed", cue_id=42)
```

---
