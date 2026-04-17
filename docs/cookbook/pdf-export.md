# PDF Export

The **ShowPrinter** plugin exports scripts with cue annotations as PDF files. Cues appear as colour-coded badges in the margin beside their associated script lines.

## Exporting via the API

The plugin registers a single endpoint:

```
GET /export/script/{script_id}/pdf
```

| Parameter     | Type  | Description                                                    |
| ------------- | ----- | -------------------------------------------------------------- |
| `script_id`   | path  | Database ID of the script to render.                           |
| `cue_list_id` | query | Cue list to annotate. Defaults to the first list for the show. |
| `layout_path` | query | Filesystem path to a custom layout TOML override.              |

The response is a PDF file download. The filename includes the script title and a timestamp, for example `Hamlet 2026-04-16_1430.pdf`.

### Example with curl

```bash
# Export script 1 with its default cue list
curl -O http://localhost:8000/export/script/1/pdf

# Export script 1 with a specific cue list
curl -O "http://localhost:8000/export/script/1/pdf?cue_list_id=3"

# Export with a custom layout
curl -O "http://localhost:8000/export/script/1/pdf?layout_path=./my_layout.toml"
```

### Example with Python

```python
import httpx

resp = httpx.get("http://localhost:8000/export/script/1/pdf")
with open("script.pdf", "wb") as f:
    f.write(resp.content)
```

## Layout configuration

The PDF layout is controlled by a TOML file. The built-in default ships at `src/showrunner/pdf_layout.toml`. You can override it per-request with the `layout_path` query parameter, or copy it and customize it for your production.

> Distances are in **millimeters**. Fonts must be built-in PDF fonts: `Courier`, `Helvetica`, or `Times` (with `-Bold`, `-Oblique`, or `-BoldOblique` suffixes).

### Page settings

```toml
[page]
width = 215.9          # US Letter
height = 279.4
margin-top = 25.4
margin-bottom = 25.4
margin-left = 25.4
margin-right = 25.4
```

### Header and footer

Both support placeholder variables:

| Placeholder | Value                      |
| ----------- | -------------------------- |
| `{title}`   | Script title               |
| `{page}`    | Current page number        |
| `{date}`    | Export date (`YYYY-MM-DD`) |
| `{time}`    | Export time (`HH:MM`)      |

```toml
[page.header]
enabled = true
font = "Helvetica"
size = 8
text = "{title} - {date} {time}"

[page.footer]
enabled = true
font = "Helvetica"
size = 8
text = "Page {page}"
```

### Element styles

Each Fountain element type has its own section under `[elements.*]`. Available types: `heading`, `action`, `character`, `dialogue`, `parenthetical`, `lyric`, `transition`, `section`, `synopsis`.

```toml
[elements.heading]
font = "Courier-Bold"
size = 12
uppercase = true
align = "left"          # left, center, right, justify
space-before = 18       # vertical space before element (pt)
space-after = 12        # vertical space after element (pt)
margin-left = 0         # additional left indent (mm)
margin-right = 0        # additional right indent (mm)
```

### Cue annotation styles

Controls how cue markers appear in the right margin.

```toml
[cues]
font = "Helvetica-Bold"        # badge label font
size = 8                        # badge label size (pt)
name-font = "Helvetica"        # cue name font
name-size = 10                  # cue name size (pt)
max-width = 80                  # max width for cue name before wrapping (mm)
margin-right = 2                # distance from right page edge (mm)
space-between = 2               # vertical gap between stacked badges (mm)
border-width = 0.4              # border thickness (mm); 0 to disable
padding = 1.5                   # inner padding between border and content (mm)
background = [255, 255, 255]    # background fill (R, G, B)
background-opacity = 0.5        # 0.0 = transparent, 1.0 = opaque
```

### Layer colours

Per-layer RGB colours for cue badges and borders:

```toml
[cues.colors]
Lights = [255, 165, 0]     # orange
Sound  = [50, 100, 255]    # blue
Video  = [160, 32, 240]    # purple
Audio  = [0, 200, 200]     # cyan
Stage  = [0, 180, 0]       # green
```

## How it works

1. The script's Fountain content is parsed using the `screenplay-tools` library.
2. Each parsed element (scene headings, character names, dialogue, etc.) is rendered with the typography defined in the layout.
3. Cues from the selected cue list are indexed by their `script_line` and drawn as colour-coded badges in the right margin beside the element that covers that line.
4. Each badge shows the layer initial and cue number (e.g. `L42` for Lights cue 42), with the cue name displayed alongside and wrapped if it exceeds `max-width`.

## Limitations

- Built-in PDF fonts (`Courier`, `Helvetica`, `Times`) only support Latin-1 characters. Avoid Unicode characters like em dashes in the layout template text fields.
- The line-to-element mapping is heuristic. Non-standard Fountain constructs (multi-line boneyards, nested notes) may not map perfectly.

## Reference

See the [ShowPrinter plugin reference](../ref/plugins.md#showprinter) for a full list of configuration options and API details.
