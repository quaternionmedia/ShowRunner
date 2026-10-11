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

Persistent MIDI input/output connections with incoming and outgoing message mappings. Maintains open MIDI ports and routes incoming messages to `showrunner_command` calls based on learned mappings. Outgoing mappings allow named MIDI presets to be sent via commands or the UI. Auto-reconnect on device hotplug. Built on the shared [connection plugin](#connection-plugins) base.

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
| `POST` | `/midi/note` | Send a Note On/Off message | `channel` (1–16), `note` (0–127), `velocity` (0–127), `connection`, `port` (optional) |
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
enabled = true                # Toggle on/off without removing config
ports = [                     # any mix of inputs and outputs
  { name = "out", direction = "out", port = "Digital Piano MIDI 1" },  # substring of the rtmidi port name
  { name = "in", direction = "in", port = "Digital Piano MIDI 1" },
  # { name = "patch", direction = "out", virtual = true },  # create a ShowRunner:<name> port to patch in
]

[[plugins.showmidi.mappings]]
name = "Piano mute"           # Display name
direction = "out"             # Outgoing preset
connection = "piano"          # Target connection (optional `port = "out"` selects one port)
type = "cc"                   # "note" or "cc"
channel = 1                   # 1–16
number = 7                    # Note or CC number (0–127)
value = 0                     # Velocity or CC value (0–127)
```

**Settings persistence:**

- Settings are loaded from the current show's `Config` table (`key='midi.settings'`) if available, falling back to `show.toml` `[plugins.showmidi]`.
- The UI page (`/midi`) saves changes to the database for the current show, not to `show.toml`.
- This allows per-show MIDI configurations without touching the config file.

## ShowOSC

**Module:** `showrunner.plugins.osc`

Persistent OSC connections with incoming and outgoing message mappings, on the same [connection base](#connection-plugins) as ShowMidi. A connection can hold several output ports (UDP or TCP, sent to all) and input ports (UDP listeners) in any mix. Incoming messages matching a mapping's glob fire a `showrunner_command` with the message `args`. Console dialects (Eos, QLab ...) can subclass `OscClient`/the plugin later; this is plain OSC.

| Detail       | Value                                                   |
| ------------ | ------------------------------------------------------- |
| Route prefix | `/osc`                                                  |
| Endpoints    | `GET /`, `GET /monitor`, `POST /send`, `POST /send/{mapping}` |
| Nav entry    | Yes (`/osc`, icon: settings_input_antenna)              |
| Dependencies | `python-osc>=1.9` (optional, `uv sync --extra osc`)     |
| Config key   | `[plugins.showosc]` or DB `Config` (`osc.settings`)     |

**REST endpoints:**

| Method | Path | Description | Body |
| ------ | ---- | ----------- | ---- |
| `GET` | `/osc/` | Plugin status and per-port connection state | — |
| `GET` | `/osc/monitor` | Recent in/out messages (`?after=<seq>`) | — |
| `POST` | `/osc/send` | Send a message | JSON `message` (starts with `/`), `args` (value or list, optional), `connection`, `port` (optional) |
| `POST` | `/osc/send/{mapping}` | Send a named outgoing mapping | — |

Errors: `400` no output connection configured, `404` unknown connection/port/mapping, `422` invalid body, `503` `python-osc` missing.

**Commands dispatched via `showrunner_command`:**

| Command | Data keys | Description |
| ------- | --------- | ----------- |
| `osc.send` | `message`, `args`, `connection`, `port` (last three optional) | Send an OSC message |
| `osc.mapping` | `mapping` | Send a named outgoing mapping |

**Configuration in `show.toml` or per-show in the database:**

```toml
[plugins.showosc]
# auto_reconnect = false      # UDP is connectionless; off by default

[[plugins.showosc.connections]]
name = "console"
ports = [
  { name = "tx", direction = "out", address = "192.168.1.50", port = 8000 },  # protocol = "udp" (default) or "tcp"
  { name = "rx", direction = "in",  address = "0.0.0.0",      port = 9000 },  # UDP only
]

[[plugins.showosc.mappings]]
name = "go"                   # outgoing: sent to every out port of `connection` (default: first)
connection = "console"
message = "/eos/cue/1/fire"
args = [1]                    # optional; 0 and "" are real values, omit for no arguments

[[plugins.showosc.mappings]]
name = "remote-go"            # incoming: message glob -> command (data gets `message` and `args`)
direction = "in"
message = "/showrunner/go*"
command = "stopper.go"
```

## Connection plugins

**Modules:** `showrunner.connection`, `showrunner.connection_ui`

ShowMidi and ShowOSC share one base so their configuration, logging, events and `/midi` / `/osc` pages behave the same.

- **Model:** a *connection* is a named client holding one or more *ports* (`direction = "in"` or `"out"`). *Mappings* are either outgoing (a named message) or incoming (a message pattern that fires a command), optionally pinned to a connection and port. Ports are keyed `(connection, port)`; unnamed ports become `in1`, `out1`, ...
- **Persistence:** per-show DB `Config` row (`<protocol>.settings`), falling back to `[plugins.<section>]` in `show.toml`. A flat `{name, direction, ...}` connection list is still accepted and folded into multi-port connections.
- **Events** (via `showrunner_event`): `<protocol>.connected`, `.disconnected`, `.in`, `.out`.
- **Add a protocol:** subclass `Port`/`Connection`/`Mapping`/`ConnectionSettings`, `ConnectionManager` (`open`, `close`, `matches`, `describe`, optional `present`/`release`/`payload`), `ConnectionPage` and `ConnectionPlugin`, then register the plugin in `showrunner.plugins`.
