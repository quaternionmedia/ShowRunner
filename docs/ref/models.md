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
| `prefix`   | str    | QLab-style number prefix, letters only; empty by default |

## CueGroup

A named box around cues in a cue list (`cue_groups` table). Groups nest through `parent_id`. A cue's `group_id` points at its innermost group.

| Field         | Type | Notes                                                        |
| ------------- | ---- | ------------------------------------------------------------ |
| `id`          | int  | Primary key                                                  |
| `cue_list_id` | int  | Cue list the group belongs to                                |
| `name`        | str  | Shown on the group's box in the script viewer                |
| `color`       | str  | Quasar colour name, e.g. `orange`                            |
| `parent_id`   | int  | Enclosing group; empty for a top-level group                 |
| `external_id` | str  | Id in a connected system (e.g. a QLab uniqueID), set by plugins |
| `layer`       | str  | Layer name; numbers the group like a cue on that layer       |
| `number`      | str  | Main number, e.g. `5`; empty = unnumbered                    |
