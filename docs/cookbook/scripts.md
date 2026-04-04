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
