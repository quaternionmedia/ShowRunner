# CLI

```
sr --help
```

| Command                                  | Description                        |
| ---------------------------------------- | ---------------------------------- |
| `sr start`                               | Start the API server               |
| `sr create <name>`                       | Create a show (shorthand)          |
| `sr list`                                | List shows (shorthand)             |
| `sr plugins`                             | Show all loaded plugins            |
| `sr plugin list`                         | List all loaded plugins            |
| `sr plugin create <name>`               | Scaffold a new external plugin     |
| `sr shows list`                          | List all shows                     |
| `sr shows create <name>`                | Create a show                      |
| `sr shows info <id>`                     | Show details                       |
| `sr shows delete <id>`                   | Delete a show                      |
| `sr scripts list <show-id>`              | List scripts for a show            |
| `sr scripts add <show-id> <title>`       | Add a script                       |
| `sr scripts delete <id>`                 | Delete a script                    |
| `sr cue-lists list <show-id>`            | List cue lists                     |
| `sr cue-lists create <show-id> <name>`   | Create a cue list                  |
| `sr cues list <cue-list-id>`             | List cues                          |
| `sr cues add <cue-list-id> <num> <name>` | Add a cue                          |

## Plugin scaffolding

`sr plugin create` generates a ready-to-use plugin package:

```bash
sr plugin create "My Plugin" -d "Controls the fog machine"
```

This creates:

```
my-plugin/
├── pyproject.toml          # entry-point registered under [project.entry-points."showrunner"]
└── my_plugin/
    └── __init__.py         # plugin class with all hook stubs
```

By default the plugin is installed in editable mode (`pip install -e`) so
ShowRunner discovers it on the next `sr start`. Pass `--no-install` to skip
installation.

| Option              | Description                              |
| ------------------- | ---------------------------------------- |
| `--output, -o`      | Parent directory for the package (default: `.`) |
| `--description, -d` | Short description for the plugin         |
| `--no-install`      | Skip automatic `pip install -e`          |
