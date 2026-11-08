# Configuration

This section covers configuration options for ShowRunner, including the `show.toml` file and environment variables.

## Logging

ShowRunner logs through the **ShowLogger** plugin. It always writes to stdout, and it also writes to a file when `file` is set. Configure it in a `[plugins.showlogger]` table in `show.toml`:

```toml
[plugins.showlogger]
level = "INFO"                  # stdout level
file = "show.log"               # omit to disable file logging
file_level = "DEBUG"            # file level
file_format = "{time} | {level: <8} | {name} | {message}"
rotation = "10 MB"              # start a new file at this size
retention = "1 week"            # keep rotated files for this long
```

| Key           | Default                 | Description                                                                                  |
| ------------- | ----------------------- | -------------------------------------------------------------------------------------------- |
| `level`       | `"INFO"`                | Minimum level written to stdout.                                                             |
| `format`      | Coloured timestamp line | Loguru format string for stdout.                                                             |
| `file`        | _(none)_                | Path to the log file. Relative paths resolve from the directory you run `sr start` in. Omit to disable the file sink. |
| `file_level`  | `"DEBUG"`               | Minimum level written to the file.                                                           |
| `file_format` | Same as `format`        | Loguru format string for the file.                                                           |
| `rotation`    | `"10 MB"`               | Size or time at which the file is rotated. Accepts any loguru rotation value.                |
| `retention`   | `"1 week"`              | How long rotated files are kept. Accepts any loguru retention value.                         |

Levels are case-insensitive and use loguru's names: `TRACE`, `DEBUG`, `INFO`, `SUCCESS`, `WARNING`, `ERROR`, `CRITICAL`.
