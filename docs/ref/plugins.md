# Plugins

This page lists all built-in ShowRunner plugins and their capabilities.

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

## ShowFamily

**Module:** `showrunner.plugins.family`

Delivers each fired cue's `family` block to the apps of the performing family. A
cue's `notes` JSON names each member and what it is asked to do:

```json
{"family": {"showstopper": {"clock": "start"},
            "carlos": {"patch": "<example name>"},
            "joe": {"take": {"notes": ["C4", "E4", "G4", "C5"], "seconds_per_note": 0.5}},
            "leo": {"song": "Blue Bossa"}}}
```

Every fire announces the `cue.fired` event -- ShowProgrammer's GO and
`POST /programmer/cue/{n}` both do -- and ShowFamily delivers from it. A **push**
member is sent `POST <url>/family/cue` with
`{"cue": {"number", "name", "fired_at"}, "action": {...}}`. A **pull** member's
action is stored, and the member reads it from the hub.

Delivery runs on worker threads, one queue per push member so its cues arrive in
the order they fired, and never holds up GO. Every request has a timeout. A member
that is down, slow or refuses the action (`422`) is recorded, never raised.

| Detail       | Value                                                         |
| ------------ | ------------------------------------------------------------- |
| Route prefix | `/family`                                                     |
| Endpoints    | `GET /family/`, `GET /family/members/{name}`                  |
| Event        | `cue.fired`, announced by ShowProgrammer on every fire        |
| Nav entry    | None (API-only)                                               |
| Dependencies | `httpx`                                                       |
| Config key   | `[plugins.family]`                                            |

**Endpoints:**

| Route                        | Returns                                                                                                   |
| ---------------------------- | --------------------------------------------------------------------------------------------------------- |
| `GET /family/`               | `{"members": {name: {"via", "url"}}, "last": [{"cue", "member", "via", "status", "ms", "done", "error"}]}` |
| `GET /family/members/{name}` | For a pull member, `{"cue": {...}, "action": {...}}` of the last cue that named it, or both `null`; 404 for any other name |

`status` is the member's HTTP status, or `null` when nothing answered; `ms` runs
from the cue firing to the member's answer, or to a pull member's first read
after the fire, which is when its delivery is counted. Both routes answer with
`Access-Control-Allow-Origin: *`, because a pull member reads them from a page in
its own origin.

**Configuration:**

```toml
[plugins.family]
pull = ["leo"]
timeout = 5.0            # seconds per delivery; optional

[plugins.family.members]
showstopper = "http://127.0.0.1:18411"
carlos = "http://127.0.0.1:18420"
```

`sr serve --family NAME=URL --family-pull NAME` (each repeatable) writes the same
table into the `show.toml` it makes.
