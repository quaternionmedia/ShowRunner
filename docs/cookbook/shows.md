# Managing shows

This section covers how to create and manage shows in ShowRunner.

## Create a show via CLI

```bash
sr create Hamlet --venue "Globe Theatre"

sr create "The Pirates of Penzance" --venue "Theatre Royal"
```

!!! note
Make sure to use quotes around multi-word names and venues.

### Create a show programmatically

```python
from showrunner.database import ShowDatabase
from showrunner.models import Show

db = ShowDatabase("show.db")
db.create_schema()

with db.session() as s:
    show = Show(name="Hamlet", venue="Globe Theatre")
    s.add(show)
    s.commit()
    s.refresh(show)
    print(f"Created show id={show.id}")

db.close()
```

### List all shows

```python
from showrunner.database import ShowDatabase

db = ShowDatabase("show.db")
for show in db.list_shows():
    print(show.name, show.venue)
db.close()
```

### Fetch a single show by id

```python
from showrunner.database import ShowDatabase

db = ShowDatabase("show.db")
show = db.get_show(1)
print(show.name)
db.close()
```
