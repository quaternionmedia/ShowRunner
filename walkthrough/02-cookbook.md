# 02 — Cookbook

> **Runtime: hermetic.** Commands for somebody who has finished
> [01 — Onboarding](01-onboarding.md). No port and no browser; each example runs
> against a database in a temporary directory.

| To | Run |
|---|---|
| start the server and UI | `uv run sr start` (then <http://localhost:8000>) |
| start it with reload | `uv run python scripts/dev` |
| create a show | `uv run sr create Hamlet --venue "The Globe"` |
| list shows | `uv run sr list` |
| see the API | <http://localhost:8000/api> |
| run tests and these pages | `uv run pytest` |
| run the browser tests | `uv run pytest tests/e2e` |

## The CLI against a config of your own

`sr` reads `show.toml` from the directory it runs in:

```python
>>> import subprocess, sys, tempfile
>>> from pathlib import Path
>>> here = Path(tempfile.mkdtemp())
>>> _ = (here / 'show.toml').write_text('[database]\npath = "show.db"\n')
>>> def sr(*args):
...     return subprocess.run([sys.executable, '-m', 'showrunner.cli', *args], cwd=here,
...                           capture_output=True, text=True, check=True).stdout
>>> print(sr('create', 'Hamlet', '--venue', 'The Globe').splitlines()[-1])
Created show "Hamlet" (id=1)
>>> 'The Globe' in sr('list')
True

```

## The REST API, in process

The ShowDB routes on a `TestClient`, which is how `tests/test_api.py` exercises
them:

```python
>>> from fastapi import FastAPI
>>> from fastapi.testclient import TestClient
>>> import showrunner.plugins.db as showdb
>>> from showrunner.database import ShowDatabase
>>> showdb._db = ShowDatabase(here / 'api.db')
>>> showdb._db.create_schema()
>>> api = FastAPI()
>>> api.include_router(showdb.router)
>>> client = TestClient(api)
>>> client.post('/db/shows', params={'name': 'Pirates of Penzance'}).json()['id']
1
>>> [show['name'] for show in client.get('/db/shows').json()]
['Pirates of Penzance']
>>> client.get('/db/shows/99').status_code
404
>>> showdb._db.close()
>>> showdb._db = None

```
