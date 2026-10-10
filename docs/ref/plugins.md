# Plugins

This page lists all built-in ShowRunner plugins and their capabilities. See also the [plugin architecture](../about/plugin-architecture.md) for an overview of how plugins extend ShowRunner.

## ShowPrinter

**Module:** `showrunner.plugins.printer`

PDF export of scripts with cue annotations. Parses Fountain scripts using the `screenplay-tools` library and renders them to PDF with configurable typography and colour-coded cue badges in the margin.

| Detail       | Value                                |
| ------------ | ------------------------------------ |
| Route prefix | `/export`                            |
| Endpoint     | `GET /export/script/{script_id}/pdf` |
| Nav entry    | None (API-only)                      |
| Dependencies | `fpdf2`, `screenplay-tools`          |
| Config key   | `[plugins.showprinter]`              |

**Query parameters:**

| Name          | Type  | Default            | Description                       |
| ------------- | ----- | ------------------ | --------------------------------- |
| `script_id`   | `int` | _(required, path)_ | Database ID of the script         |
| `cue_list_id` | `int` | First for the show | Which cue list to annotate        |
| `layout_path` | `str` | Built-in template  | Path to a custom layout TOML file |

**Layout template:** See the [PDF Export cookbook](../cookbook/pdf-export.md) for a full guide to the layout configuration.

## ShowMidi

**Module:** `showrunner.plugins.midi`

Persistent MIDI input/output connections with incoming and outgoing message mappings. Maintains open MIDI ports and routes incoming messages to `showrunner_command` calls based on learned mappings. Outgoing mappings allow named MIDI presets to be sent via commands or the UI. Auto-reconnect on device hotplug.

| Detail       | Value                               |
| ------------ | ----------------------------------- |
| Route prefix | `/midi`                             |
| Endpoints    | `GET /ports`, `POST /note`, `POST /cc`, `POST /send/{mapping}`, `POST /panic` |
| Nav entry    | Yes (`/midi`, icon: piano)          |
| Dependencies | `python-rtmidi>=1.5` (optional)     |
| Config key   | `[plugins.showmidi]` or DB `Config` |

**REST endpoints:**

| Method | Path | Description | Query params |
| ------ | ---- | ----------- | ------------ |
| `GET` | `/midi/` | Plugin status and connection state | — |
| `GET` | `/midi/ports` | Available MIDI input/output ports | — |
| `POST` | `/midi/note` | Send a Note On/Off message | `channel` (1–16), `note` (0–127), `velocity` (0–127), `connection` (optional) |
| `POST` | `/midi/cc` | Send a Control Change message | `channel` (1–16), `control` (0–127), `value` (0–127), `connection` (optional) |
| `POST` | `/midi/send/{mapping}` | Send a named outgoing mapping | — |
| `POST` | `/midi/panic` | All Notes Off + All Sound Off on all channels | — |

**Commands dispatched via `showrunner_command`:**

| Command | Data keys | Description |
| ------- | --------- | ----------- |
| `midi.note` | `channel`, `note`, `velocity`, `connection` (optional) | Send a Note On/Off |
| `midi.cc` | `channel`, `control`, `value`, `connection` (optional) | Send a Control Change |
| `midi.send` | `mapping` | Send a named mapping by name |
| `midi.panic` | — | Send All Notes Off to all outputs |

**Configuration in `show.toml` or per-show in the database:**

```toml
[plugins.showmidi]
# port_name = "Digital Piano" # shorthand for a single output named "default"
auto_reconnect = true         # Reconnect on device hotplug
reconnect_interval = 2.0      # Seconds between reconnect attempts

[[plugins.showmidi.connections]]
name = "piano"                # Alias for commands and UI
port = "Digital Piano MIDI 1" # Substring of the rtmidi port name
direction = "out"             # "in" or "out"
enabled = true                # Toggle on/off without removing config
# virtual = true              # create a ShowRunner:<name> port to patch in instead of `port`

[[plugins.showmidi.mappings]]
name = "Piano mute"           # Display name
direction = "out"             # Outgoing preset
connection = "piano"          # Target connection
type = "cc"                   # "note" or "cc"
channel = 1                   # 1–16
number = 7                    # Note or CC number (0–127)
value = 0                     # Velocity or CC value (0–127)
```

**Settings persistence:**

- Settings are loaded from the current show's `Config` table (`key='midi.settings'`) if available, falling back to `show.toml` `[plugins.showmidi]`.
- The UI page (`/midi`) saves changes to the database for the current show, not to `show.toml`.
- This allows per-show MIDI configurations without touching the config file.
