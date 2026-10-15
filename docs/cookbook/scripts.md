# Scripts

This section covers how to manage scripts in ShowRunner, including adding scripts via the CLI and programmatically, as well as listing scripts for a show.

### Add a script via CLI

```bash
# From a file on disk
sr scripts add 1 Hamlet --format fountain --file ./examples/scripts/Hamles.fountain

# From inline content
sr scripts add 1 "Inline script" --format fountain --content "INT. STAGE - DAY"

```

### Add a script programmatically

```python
from showrunner.database import ShowDatabase
from showrunner.models import Script

db = ShowDatabase("show.db")
db.create_schema()

with open("./examples/scripts/Pirates-of-Penzance.fountain") as f:
    script_content = f.read()

with db.session() as s:
    script = Script(
        show_id=2,
        title="Pirates of Penzance",
        format="fountain",
        content=script_content,
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

### Format the script viewer

The `/script` viewer labels every line with a block type (using Fountain rules) and styles it:

| Block           | Fountain source                                   | Default style                      |
|-----------------|---------------------------------------------------|------------------------------------|
| `heading`       | `INT.` / `EXT.` / `EST.` / `I/E`, or a leading `.` | bold, uppercase, amber             |
| `action`        | anything else                                     | plain                              |
| `character`     | an uppercase line after a blank line, before text  | bold, uppercase, indented          |
| `parenthetical` | `(...)` inside a speech                           | italic, grey, indented             |
| `dialogue`      | the rest of a speech                              | indented, narrower                 |
| `lyric`         | a leading `~`                                     | italic, blue, indented             |
| `transition`    | `CUT TO:` or a leading `>`                        | uppercase, right-aligned           |
| `section`       | a leading `#`                                     | bold, larger                       |
| `synopsis`      | a leading `=`                                     | italic, grey                       |
| `comment`       | `/* boneyard */` or `[[note]]`                    | italic, grey, shaded               |

Override any block in `show.toml`. Each key is optional; unknown blocks, unknown keys and unsafe values are logged and ignored:

```toml
[plugins.showscripter]
line-height = "2.2"       # leave room above each line for the cue chips (default 2)
show-previews = false     # start with preview chips hidden

[plugins.showscripter.format.character]
indent = "30%"            # margin-left
bold = false

[plugins.showscripter.format.dialogue]
indent = "15%"
width = "55%"             # max-width
color = "#e0e0e0"
```

| Key          | Effect                        | Value          |
|--------------|-------------------------------|----------------|
| `indent`     | `margin-left`                 | CSS length     |
| `width`      | `max-width`                   | CSS length     |
| `align`      | `text-align`                  | `left`, `right`, `center` |
| `size`       | `font-size`                   | CSS length     |
| `color`      | text colour                   | CSS colour     |
| `background` | background colour             | CSS colour     |
| `font`       | `font-family`                 | CSS font name  |
| `bold`, `italic`, `uppercase` | text weight, style, case | `true` / `false` |

Cues float above the text at their character position, so they never move the words. The page bars stay pinned when you change pages.

Page bars sit above and below the script. The page and cue list you are on are remembered per browser and script, so a refresh reopens them.
