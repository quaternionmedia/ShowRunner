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

`Cue.layer` is free text, but the ShowScripter viewer offers `Lights`, `Sound`, `Video`, `Audio`, `Stage` and `Mix` (the `Mix` layer is for TheatreMix cues). Cues from every cue list in a show appear in the script viewer, not only the selected list.

### Show preview chips in the script viewer

A plugin can put short labels under cues in the `/script` viewer by implementing `showrunner_script_previews`. It receives the viewer's cue dicts and returns chips keyed by cue id. Chips show under the cue's line, and at the top of each following page until a later cue with chips takes over.

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

Each chip is a dict with `text` (required), and optional `color` (Quasar colour, default `white`) and `tooltip`. If a plugin raises, the error is logged and no previews are shown for that page load.
