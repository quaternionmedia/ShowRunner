# Cues & Cue Lists

### Add a cue list and cues to a show

```python
from showrunner.database import ShowDatabase
from showrunner.models import Cue, CueList

db = ShowDatabase("show.db")
db.create_schema()

with db.session() as s:
    cue_list = CueList(show_id=1, name="Act 1")
    s.add(cue_list)
    s.commit()
    s.refresh(cue_list)

    cue = Cue(cue_list_id=cue_list.id, number=1, name="Lights Up")
    s.add(cue)
    s.commit()
    print(f"Cue '{cue.name}' added to list '{cue_list.name}'")

db.close()
```

### Place a cue at a specific script position

Cues can be anchored to a line and character position within a script. The ShowScripter UI reads these fields to render visual cue markers inline with the script text — and updates them when you drag a cue marker to a new position.

```python
cue = Cue(
    cue_list_id=cue_list.id,
    number=5,
    name="Blackout",
    layer="Lights",
    script_line=42,   # line number in the script text
    script_char=0,    # character offset within that line (0 = start of line)
)
```

### Layers

A layer has a name, a colour and a type, and belongs to one show. Layers live in the `layers` table, and `Cue.layer` stores the layer's name. A new show starts with `Lights`, `Sound`, `Video`, `Audio` and `Stage`. Add, rename, recolour or delete layers from the ShowScripter **Layers** button, with `sr layers`, or at `/db/shows/{id}/layers`. A layer can't be deleted while cues use it.

The type says what kind of cue the layer holds. Built-in types are `generic`, `lights`, `sound`, `video`, `audio` and `stage`. A plugin adds types and layers with `showrunner_get_layer_types` and `showrunner_get_layers`. For example, a TheatreMix-style plugin could register a `mix` type and a `Mix` layer:

```python
import showrunner


class TheatreMix:
    @showrunner.hookimpl
    def showrunner_get_layer_types(self):
        return {'type': 'mix', 'label': 'Mix', 'description': 'TheatreMix cues', 'color': 'pink'}

    @showrunner.hookimpl
    def showrunner_get_layers(self):
        return {'name': 'Mix', 'color': 'pink', 'type': 'mix'}
```

A plugin's layers are added to each show that doesn't have them yet. Existing layers are never changed. If the plugin is removed, its layers stay with their type marked as not loaded, and they behave as generic.

The viewer's **Cue list** filter shows one list (the script's list by default) or every list in the show, so plugin lists such as TheatreMix cues appear when you pick **All lists**.

### Show preview chips in the script viewer

A plugin can put short labels under cues in the `/script` viewer by implementing `showrunner_script_previews`. It receives the viewer's cue dicts and returns chips keyed by cue id. Chips show under the cue's line, and at the top of each following page until a later cue with chips takes over. Each group is drawn in a box with a pointer up to its source cue, coloured by the cue's layer. Use the **Previews** button in the viewer toolbar to hide or show them (`show-previews` in `[plugins.showscripter]` sets the starting state; see [Format the script viewer](scripts.md#format-the-script-viewer)).

```python
import showrunner


class JudgeNotes:
    @showrunner.hookimpl
    def showrunner_script_previews(self, cues):
        return {
            c['id']: [{'text': f'Judge {c["number"]}', 'color': 'amber', 'tooltip': c['name']}]
            for c in cues
            if c['layer'] == 'Sound'
        }
```

Each chip is a dict with `text` (required), and optional `color` (Quasar colour, default `white`) and `tooltip`. If a plugin raises, the error is logged and the other plugins' chips still show.
