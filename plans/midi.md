# ShowMidi: persistent connections, config UI, in/out mappings

## Context

PR #22 (`copilot/add-cue-type-midi-control-changes`) adds `ShowMidiPlugin`
(`src/showrunner/plugins/midi.py`) with `midi.note` / `midi.cc` commands and `/midi`
REST routes. We stay on this branch and make the plugin solid on its own:

- connections stay open, can reconnect automatically, and show their status
- a NiceGUI `/midi` page to manage connections and mappings, with a live monitor,
  a "learn" button and a panic button
- **outgoing mappings**: named saved messages that cues and commands send by name
- **incoming mappings**: a MIDI message triggers a `showrunner_command`
- settings are stored per show in the DB `Config` table, with `show.toml` as the default

## Review findings (fixed by this plan)

1. **Opens a new `MidiOut` for every message**, so it is slow, creates a new ALSA client
   each time, and can drop messages. → keep ports open.
2. **`_resolve_port` lists all ports on every command** and silently falls back to
   index 0 when the name doesn't match, so messages can go to the wrong device.
   → match by name only; if nothing matches, log it and don't send.
3. **Port indices are unstable** (they change when devices are plugged in or out). → no indices.
4. **REST routes ignore the config** (the `port=0` default bypasses `port_name`).
5. **Inputs aren't checked**: out-of-range values are trimmed with `& 0x7F`. → use
   `Query(ge=, le=)` and return 422; commands raise `ValueError` the same way.
6. **Notes can hang**: a Note On with no Note Off. → add a panic button now; add a
   `ponytail:` comment for a future `duration_ms` auto Note Off.
7. **No tests.**
8. **Working-tree noise**: the local `show.toml` port name and the
   `examples/Rocky-Horror.*` rename stay out of the commit.
9. The module docstring claims the `Cue` model "recognises" MIDI types. It's just
   free text. → fix the docstring.

## Approach

### Data model (same shape in show.toml and the DB)

```toml
[plugins.showmidi]
auto_reconnect = false        # background re-scan; toggle in UI
reconnect_interval = 2.0      # seconds
connections = [               # name = alias used by mappings/commands
  { name = "piano", port = "Digital Piano MIDI 1", direction = "out", enabled = true },
  { name = "rode",  port = "RODECaster",           direction = "in",  enabled = true },
]
mappings = [
  # outgoing: send by name (cue/command/UI)
  { name = "Piano mute", direction = "out", connection = "piano", type = "cc",   channel = 1, number = 7,  value = 0 },
  # incoming: match type/channel/number (connection optional = any input)
  { name = "Pedal GO",   direction = "in",  connection = "rode",  type = "note", channel = 1, number = 60, command = "programmer.go", data = {} },
]
```

- `type` is `note` or `cc`. For `note`, `value` is the velocity (0 sends a Note Off).
  Program change is left out; add it when someone needs it.
- `port` is a case-insensitive substring of the rtmidi port name.
- **Where settings come from:** the current show's `Config` rows
  (`key='midi.settings'`, `value=` JSON of the whole block above). If there are none,
  use `[plugins.showmidi]` from `show.toml`. Saving from the UI always writes to the DB
  for the current show, so `show.toml` is never rewritten and the config watcher's
  page reload isn't triggered.
- The `port`/`port_name` keys from the unmerged PR are dropped. The local `show.toml`
  gets a `connections` entry for "Digital Piano" instead.

### Runtime (`midi.py`, a single module-level `_midi = MidiManager()`)

- `MidiManager` holds:
  - `outputs` / `inputs`: dicts mapping connection name to an open rtmidi object,
    created with client name `"ShowRunner"`
  - one `MidiOut` and one `MidiIn` kept only for listing ports
  - a `threading.Lock`
  - `monitor: deque(maxlen=200)` of incoming messages
  - `learn: threading.Event` plus a `learned` slot
- Methods:
  - `apply(settings)`: close connections that were removed or disabled, open new ones
  - `refresh()`: close any open port whose name has disappeared, and open any wanted
    port that has appeared. Used by auto-reconnect and the manual "Reconnect" button.
  - `send(connection, bytes)`: raises `KeyError` if the connection isn't open
  - `send_mapping(name)`
  - `panic()`: CC 123 (All Notes Off) and CC 120 (All Sound Off) on all 16 channels
    to every open output
  - `close_all()`
- Input callback (rtmidi thread):
  1. decode note/cc; Note On with velocity 0 counts as Note Off
  2. append to `monitor`
  3. if `learn` is set, store the message and clear the flag
  4. otherwise, for each matching incoming mapping, call
     `pm.hook.showrunner_command(command, {**data, "value": v, "mapping": name})`
- Auto-reconnect: a daemon thread runs `refresh()` every `reconnect_interval` while
  `auto_reconnect` is on. It stops via an `Event` on shutdown or when the setting is
  turned off.
- Plugin hooks:
  - `startup`: load settings for `config.current_show`, then `apply`
  - `config_changed`: reload (only matters when the show has no DB settings)
  - `shutdown`: `close_all` and stop the reconnect thread
  - `showrunner_command`: handles `midi.note`, `midi.cc` (optional `connection`; the
    default is the first enabled output), `midi.send` `{mapping}` and `midi.panic`
  - `get_nav`: `/midi`
  - `get_status`: green if all enabled connections are open, amber if some are,
    red if none are, grey if none are configured or rtmidi is missing
  - emits `midi.connected`, `midi.disconnected` and `midi.in` events through
    `self.emit`
- Without rtmidi, everything is a no-op (same as now); the page shows an install hint.

### REST (validated)

- `GET /midi/`: status and per-connection state
- `GET /midi/ports`: `{inputs, outputs}`
- `POST /midi/note` and `/midi/cc`: `channel 1–16`, `number`/`value 0–127`, optional
  `connection`
- `POST /midi/send/{mapping}` (404 if unknown) and `POST /midi/panic`

### UI page `/midi` (NiceGUI, follows the `scripter._build_page` pattern)

- Header via `header()`. Settings are loaded for `_current_show_id()`; if they differ
  from what is running, the page applies them, so switching shows switches MIDI setup.
- **Connections card:**
  - a table of connections (name, direction, port, enabled toggle, state dot)
  - an "Add" row with port `ui.select` filled from `/ports`
  - delete per row
  - "Reconnect" button and auto-reconnect toggle
- **Mappings card:**
  - a table for each direction, with editable rows (type, channel, number,
    value or command)
  - "Test" sends an outgoing mapping
  - "Learn" fills an incoming row from the next message received (polled by `ui.timer`,
    with a timeout and a cancel)
- **Monitor card:** a `ui.log` fed by a 0.1 s `ui.timer` that drains new `monitor` entries.
- A big red **PANIC** button in the page toolbar.
- Every change does `save()`: writes to the DB `Config` for the current show, then
  `_midi.apply()`, then `ui.notify`.

## Files to modify

- `src/showrunner/plugins/midi.py`: rewrite, about 400 lines
- `tests/test_midi.py`: new
- `show.toml`: replace the local `port_name` with a commented `connections` example
  (commit only the commented example)
- `docs/ref/plugins.md`: ShowMidi section (config keys, commands, routes)

## Reuse

- `ShowRunnerPlugin` / `emit` (`plugin.py`)
- `get_db()` (`plugins/db.py`; DB starts `tryfirst`, so it's ready in our startup)
- `Config` model (`models.py`) for per-show key/value storage
- `header()`, `_current_show_id()` (`ui.py`)
- Page registration and nav dict shape: `scripter.py` `_build_page` and `showrunner_get_nav`
- Status dict shape: `ui._get_status_icons`

## Steps

- [x] Remove the working-tree noise from the PR scope (leave the Rocky-Horror files
      and the local `show.toml` uncommitted)
- [x] `MidiManager` with port matching, `apply`/`refresh`/`send`/`panic`/`close_all`,
      and the input callback with monitor, learn and mapping dispatch
- [x] Load and save settings (DB `Config` `midi.settings`, falling back to `show.toml`)
      and validate them (pydantic models `Connection` and `Mapping`, keeping invalid
      entries out of `apply`)
- [x] Auto-reconnect thread
- [x] Plugin hooks (startup, config_changed, shutdown, command, nav, status)
- [x] Validated REST routes
- [x] `/midi` NiceGUI page (connections, mappings with test and learn, monitor, panic)
- [x] `# ponytail:` note on `midi.note` for a future `duration_ms` auto Note Off
- [x] Tests (a fake `rtmidi` module patched into `midi.py`):
  - outputs open once and are reused across sends
  - name mismatch means no send (no index fallback)
  - `apply` closes removed connections
  - `refresh` reopens a port after it reappears
  - an incoming Note On calls `showrunner_command` with the mapped command
  - learn captures the next message
  - panic sends 32 messages per output
  - DB settings override `show.toml`
  - REST returns 422 on `channel=17`
- [x] Docs section; update `show.toml` example

## Verification

- `uv run pytest` (all pass, including the new `tests/test_midi.py`)
- `uv run ruff check src tests`
- Manual:
  1. Run `uv run uvicorn showrunner.app:app` and open `/midi`.
  2. Add an output on "Digital Piano MIDI 1" and press Test (expect sound). Add an
     input on "Digital Piano" and play keys (they should appear in the monitor).
  3. Learn a key and map it to `midi.panic` (or any command), then check that pressing
     the key logs the command in ShowLogger.
  4. Unplug the piano with auto-reconnect on: the status goes red, then green again
     after replugging.
  5. Hold a note via `POST /midi/note`, then press PANIC (the note stops).
  6. Switch shows in the header: the settings switch, and a show with no DB row uses
     `show.toml`.
