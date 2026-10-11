# Generic `Connection` base + ShowOSC (built on PR #22)

## Context

PR #22 (`copilot/add-cue-type-midi-control-changes`, head `f6b4315`, not yet on `main`) adds ShowMidi: a 845-line
`plugins/midi.py` with settings models, a manager holding open connections, per-show DB settings, a plugin class,
REST routes and a NiceGUI page. Roughly two-thirds of it is not MIDI-specific (settings persistence, connection
lifecycle, error tracking, monitor, learn, mapping dispatch to `showrunner_command`, auto-reconnect thread, status
colours, page layout). We want OSC to reuse that, so we first pull the generic part out into a `Connection` base,
then build MIDI and OSC on it with shared config, logging and UI.

The generic backend shape follows `Eos` (QLab-python `eos.py`): a tiny protocol client (`address`, `port`, `tcp`,
`send(message, value=None)`) over `python-osc`. Console-specific subclasses (Eos etc.) come later, out of scope.

Confirmed scope: OSC send **and** receive; per-show DB settings with `show.toml` default; a simple `/osc` page
(connections, live messages, test); plain OSC only; name `ShowOSC`, `osc.*` commands, config key `showosc`.
`lighter.py` / `mixer.py` stay untouched stubs.

## Branch

`mrharpo/osc` currently sits on `main`. Reset/rebase it onto `origin/copilot/add-cue-type-midi-control-changes`
(`f6b4315`) so the work is a stack on PR #22. Open the PR against that branch (retarget to `main` once #22 merges).
The refactor lands as its own commit(s) before OSC, so the PR #22 behaviour stays reviewable.

## Design

### New: `src/showrunner/connection.py` (protocol-agnostic, no `rtmidi` / `pythonosc` import)

- **Models** (pydantic, subclassed per protocol)
  - **`Port`**: one endpoint: `name` (unique within its connection, default `in1` / `out1` ...), `direction in|out`, `enabled`.
    MIDI adds `port` (system port substring), `virtual`; OSC adds `address`, `port`, `protocol` (out: remote target; in: local bind address and port).
  - **`Connection`**: a named client holding **`ports: list[Port]`** with any mix of directions: one in, one out, several outs
    (fan-out), several ins, or in + out together. Fields: `name` (unique), `enabled` (switches the whole connection), `ports` (at least one).
    Examples: one MIDI "piano" with an out and an in port; one MIDI "rig" with two out ports (piano + synth); one OSC "eos" that sends to
    `192.168.1.100:8000` and listens on `:9000`; one OSC "lights" sending to two consoles.
  - `Mapping`: `name`, `direction`, `connection` (in: `''` = any connection), optional `port` (port name; empty = all matching ports for out,
    any port for in), `command`, `data`. MIDI adds `type/channel/number/value`; OSC adds `message` (glob for `in`), `args`.
  - `ConnectionSettings`: `auto_reconnect`, `reconnect_interval`, `connections`, `mappings`. Subclasses narrow the two list types.
  - Shared validation: connection names unique, port names unique within a connection, mappings reference a known connection and port.
  - **Compatibility**: a `mode='before'` validator on `ConnectionSettings` accepts PR #22's flat form
    (`{name, port, direction, virtual}`, plus the `port_name` shorthand) and folds entries with the same `name` into one connection with several ports
    (a flat `piano` out + `piano` in becomes one connection with two ports). Saving always writes the `ports` form.
  - Config shape (same for DB and `show.toml`):
    ```toml
    [[plugins.showosc.connections]]
    name = "eos"
    enabled = true
    ports = [
      { name = "cmd",   direction = "out", address = "192.168.1.100", port = 8000, protocol = "udp" },
      { name = "reply", direction = "in",  address = "0.0.0.0",       port = 9000 },
    ]
    [[plugins.showosc.mappings]]
    name = "Eos go"; direction = "out"; connection = "eos"; message = "/eos/key/go_0"   # port omitted = all out ports
    ```
- **Persistence**, keyed by class attributes `settings_key` (`midi.settings` / `osc.settings`), `toml_section` (`showmidi` / `showosc`):
  `load_settings(config, show_id)` (DB row for the show, else `[plugins.<section>]`, else defaults), `save_settings(show_id, settings)`,
  `first_show_id()`. Same fallbacks and warnings as PR #22.
- **`ConnectionManager`** (what `MidiManager` is today, minus MIDI)
  - Owns `RLock`, `settings`, `show_id`, `config`, `_pm`, open handles keyed `(connection, port)` (port name), `errors` keyed the same way, `monitor` deque + `seq`,
    learn state, reconnect thread.
  - Concrete: `apply(settings)`, `refresh()` loop thread (`_update_thread/_stop_thread/_reconnect_loop`), `close_all()`, `start_learn()`,
    `dispatch(msg)` (loops `in` mappings, fires `showrunner_command(command_name, {**m.data, 'value'/'args': ..., 'mapping': m.name})`),
    `get_status()` (per connection: aggregate state plus per-port state and error), `default_output()` (first enabled connection with an enabled out port),
    `is_open(connection, port)`, `output_ports(connection, port=None)`, `record(direction, msg)` (monitor append).
  - Sending to a connection is a fan-out over its out ports (or just `port` when given); a failure on one port is logged and recorded in `errors`
    and does not stop the others. Receiving on any in port tags the message with `connection` and `port`.
  - Abstract per protocol: `open(conn, port)`, `close(conn, port)`, `present(conn, port) -> bool` (for `refresh`; OSC returns True), `matches(mapping, msg) -> bool`,
    `decode_payload(mapping, msg) -> dict`, `describe(msg) -> str` (monitor line).
  - **Logging**: one prefix helper `self.log(level, conn, text)` -> loguru `"{PROTO} {connection}/{port} {direction}: ..."`; one place for
    open/closed/disconnected messages. Events via plugin `emit` (payload has `connection` and `port`): `<proto>.connected`, `<proto>.disconnected`,
    `<proto>.in`, `<proto>.out`.
- **`ConnectionPlugin(ShowRunnerPlugin)`**: `startup` (store `_pm` and config, `activate(show)`, build page; log + skip when the optional dep is missing),
  `shutdown`, `config_changed` (reload, re-apply only if settings differ), `get_routes`, `get_commands`, `get_nav`, `get_status` (grey / green / amber / red as in PR #22,
  `path` set), and `showrunner_command` that filters by `command_name.startswith(prefix + '.')`, swallows and logs errors, and calls the subclass's `handle_command(name, data)`.
  Class attributes: `protocol`, `label`, `icon`, `nav_order`, `manager`, `router`, `has_backend`.
- **REST helper**: `common_routes(router, manager, require_backend)` adds `GET /` (status, connections, errors) and `GET /monitor?after=<seq>`;
  each protocol adds its own send routes and keeps the same 404 / 422 / 400 / 503 conventions.

### New: `src/showrunner/connection_ui.py` (NiceGUI imported lazily, as in PR #22)

`ConnectionPage` extracted from `midi._build_page`: `ui.page(path)`, header, follow the header's show, `draft = settings.model_dump()`,
`save()` (validate -> `save_settings` -> `apply` -> notify errors), `setter()` helper, status labels, monitor `ui.log` drained by `seq`
from a 0.2 s `ui.timer`, learn polling, connections card (one block per connection: name + On switch + state, with a nested row per port,
"+ In port" / "+ Out port" buttons and per-port delete; plus Add connection + Rescan) and mappings card (send / receive rows + Test + Learn),
install hint when the backend is missing. Subclass hooks: `mapping_fields(m, direction, refresh)`,
`port_fields(port, refresh)`, `new_port(direction)`, `new_mapping(direction)`, `test(mapping)`, `apply_learned(m, msg)`, `toolbar()` (MIDI: PANIC; OSC: nothing),
`extra_cards()` (OSC: test sender), `format(msg)`. `_valid_json` / `_set_json` move here.

### `plugins/midi.py` (refactor, behaviour unchanged)

Keep: `decode`, `stable_port_name`, rtmidi client reuse (one client per port: `_clients` keyed `(connection, port, virtual)`; `_probe`), port matching, `send_note/cc/mapping`, `panic`, REST note/cc/send/panic/ports,
legacy `port_name` shorthand. Change: `MidiPort(Port)`, `MidiConnection(Connection)`, `MidiMapping(Mapping)`, `ShowMidiSettings(ConnectionSettings)`,
`MidiManager(ConnectionManager)`, `ShowMidiPlugin(ConnectionPlugin)`, `MidiPage(ConnectionPage)`. File shrinks to roughly 450 lines.

### New: `plugins/osc.py`

- `OscClient(address='127.0.0.1', port=8000, tcp=False)` (Eos-style, lazy `pythonosc` imports); `send(message, value=None)` uses `value is None`
  (Eos's truthiness check drops `0`, `""`, `[]`).
- `OscPort(Port)`: `address`, `port` 1-65535, `protocol udp|tcp` (out only); `OscConnection(Connection)` holds `ports: list[OscPort]`. `OscMapping(Mapping)`: `message` starting with `/`, `args` (int/float/str/bool).
- `OscManager(ConnectionManager)`: `open(conn, port)` builds an `OscClient` per out port (fan-out on send) or a `ThreadingOSCUDPServer` per in port on a daemon thread with
  `dispatcher.set_default_handler(..., needs_reply_address=True)` (in); `close` does `shutdown()` + `server_close()` + join;
  bind failures go to `errors`. `matches` uses `fnmatch` on `message`; `decode_payload` gives `{'args': [...], 'address': msg, 'mapping': name}`.
  - shortcut: receive is UDP only; add `ThreadingOSCTCPServer` (python-osc >= 1.9) when a console needs TCP in.
  - `present()` is always True, so no hotplug scan; `auto_reconnect` defaults False for OSC. The page's Reconnect re-applies.
- `ShowOscPlugin(ConnectionPlugin)`: commands `osc.send {message, args?, connection?, port?}` and `osc.mapping {mapping}`; REST `POST /osc/send`
  (body `{message, args?, connection?, port?}`) and `POST /osc/send/{mapping}`; nav `{label: 'OSC', path: '/osc', icon: 'settings_input_antenna', order: 31}`.
- `OscPage(ConnectionPage)`: port row = name / direction / address / port / protocol / On / state, under each connection's header; mapping rows = message + args JSON (send) or
  message + command + data JSON (receive); Learn fills `message`; **test sender** card (connection, message, args JSON, Send); monitor shows both
  directions (`->` sent, `<-` received).

## Files to modify / create

- `src/showrunner/connection.py` (new), `src/showrunner/connection_ui.py` (new)
- `src/showrunner/plugins/midi.py` (refactor onto the base)
- `src/showrunner/plugins/osc.py` (new); `src/showrunner/plugins/__init__.py` (register `ShowOscPlugin`)
- `pyproject.toml` + `uv.lock`: `osc = ["python-osc>=1.9"]`, add to `all`
- `show.toml`: commented `[plugins.showosc]` example only
- `tests/test_connection.py` (new), `tests/test_midi.py` (renames only), `tests/test_osc.py` (new), `tests/test_plugins.py` (count 17 -> 18)
- `docs/ref/plugins.md`: ShowOSC section, short "Connection plugins" section describing the base, MIDI section touched only for any renames
- `plans/osc.md` (this file; `plans/midi.md` untouched)

## Reuse

- Everything in PR #22 `midi.py` / `ui.py` / `tests/test_midi.py` that is moving into the base (it is moved, not rewritten; error handling and validation are kept)
- `ShowRunnerPlugin.emit` (`src/showrunner/plugin.py`), `get_db()` (`plugins/db.py`), `Config` / `Show` (`models.py`), `header()` / `_current_show_id()` (`ui.py`)
- `db` fixture (`tests/conftest.py`)

## Steps

- [x] Rebase `mrharpo/osc` onto the PR #22 branch; run `uv run pytest` for a green baseline (107 passed per PR)
- [x] `connection.py`: models, persistence, `ConnectionManager`, `ConnectionPlugin`, `common_routes`
- [x] Port `midi.py` onto it; `tests/test_midi.py` must still pass with renames only (this is the refactor's regression guard)
- [x] `connection_ui.py`: extract the page; port `/midi` onto it, check the page by hand
- [x] `tests/test_connection.py`: a dummy `ConnectionManager` subclass with fake `open/close/present`: apply closes removed / opens new, `errors`, refresh thread toggle,
      dispatch (match, connection + port filter, learn, note-release skipped via `matches`), status colours (per-port and aggregate), DB overrides toml,
      duplicate connection / port names rejected, legacy flat settings fold into multi-port connections, fan-out to several out ports with one failing,
      one connection with in + out ports, several in ports tagged with their port name
- [x] `osc.py` (+ register, extra, `uv lock`)
- [x] `tests/test_osc.py`: fake `pythonosc` client / server; send args incl. `0`; one connection with two out ports (fan-out) and in + out together; default output; 404 / 422 / 400 / 503; incoming mapping fires command with `args`;
      glob; learn; bind failure in `errors`; one real UDP loopback test (skipped without python-osc)
- [x] Docs and `show.toml` example

## Verification

- `uv sync --all-extras && uv run pytest && uv run ruff check src tests`
- Manual `/midi` (regression, same checklist as `plans/midi.md`): connect, test, learn, unplug / replug, panic, switch shows
- Manual `/osc`: `uv run uvicorn showrunner.app:app`; add out `127.0.0.1:9001` (watch with `nc -ul 9001`) and in `:9000`; send
  `python -c "from pythonosc.udp_client import SimpleUDPClient as C; C('127.0.0.1',9000).send_message('/x',[1,2])"` and see it in the monitor;
  Learn it, map it to a command, confirm ShowLogger shows the command; switch shows and check the settings switch

## Risks

- Refactoring unmerged code in PR #22 means MIDI churn lands in this PR; the existing `test_midi.py` is the only guard (hence the order above).
- `midi.py` private names that tests touch (`_outputs`, `_inputs`, `_clients`, `_probe`) will change (keys become `(connection, port)`); tests are updated for names/shape only.
- Multi-port connections change the stored settings shape; the compatibility validator is what keeps PR #22 `show.toml` and any DB rows loading.
- `ui.py`'s status-icon polling comes from PR #22, so this stacks on it and can't merge before it.
