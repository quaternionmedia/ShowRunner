# Models

## Layer

A cue layer in a show (`layers` table).

| Field      | Type   | Notes                                              |
| ---------- | ------ | -------------------------------------------------- |
| `id`       | int    | Primary key                                        |
| `show_id`  | int    | Show the layer belongs to; `(show_id, name)` is unique |
| `name`     | str    | Stored in `Cue.layer`                              |
| `color`    | str    | Quasar colour name, e.g. `orange`                  |
| `type`     | str    | Layer type from the type registry                  |
| `sequence` | int    | Display order                                      |
