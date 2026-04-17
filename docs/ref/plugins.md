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
