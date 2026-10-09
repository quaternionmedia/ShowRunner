# 01 — Onboarding

> **Runtime: hermetic.** Once `uv sync` has run, this page needs no port, no
> network and no browser. `uv run pytest` runs every example on it.

## Install

The same on Windows, macOS and Linux, given [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/quaternionmedia/ShowRunner
cd ShowRunner
uv sync                             # dev tools, the CLI, and Playwright
uv run playwright install chromium  # once per machine; page 03 drives it
uv run pytest                       # the tests and these pages
```

What goes wrong first:

- `Warning: fpdf2 is not installed. ShowPrinterPlugin will be unavailable.` is
  printed on import without the `pdf` extra. It is harmless; `uv sync --extra pdf`
  installs it.
- Page 03 fails, rather than skips, when Chromium is missing:
  `Executable doesn't exist at ...ms-playwright...`. Run the `playwright install`
  line above.
- `sr start` wants port 8000. If something else holds it: `sr start --port 8001`.

## The package imports

```python
>>> import contextlib, io
>>> with contextlib.redirect_stdout(io.StringIO()) as said:
...     from showrunner.plugins import get_builtin_plugins
>>> said.getvalue() in ('', 'Warning: fpdf2 is not installed. ShowPrinterPlugin will be unavailable.\n')
True
>>> names = [plugin.__name__ for plugin in get_builtin_plugins()]
>>> {'ShowDBPlugin', 'ShowDashboardPlugin', 'ShowScripterPlugin'} <= set(names)
True

```

## A show in a database of your own

Everything ShowRunner keeps lives in one SQLite file:

```python
>>> import tempfile
>>> from pathlib import Path
>>> from sqlmodel import select
>>> from showrunner.database import ShowDatabase
>>> from showrunner.models import Show
>>> db = ShowDatabase(Path(tempfile.mkdtemp()) / 'show.db')
>>> db.create_schema()
>>> with db.session() as s:
...     s.add(Show(name='Hamlet', venue='The Globe'))
...     s.commit()
>>> with db.session() as s:
...     [(show.name, show.venue) for show in s.exec(select(Show))]
[('Hamlet', 'The Globe')]
>>> db.close()

```

## The command line

`sr` is the CLI. Through this interpreter it is `python -m showrunner.cli`:

```python
>>> import subprocess, sys
>>> help = subprocess.run([sys.executable, '-m', 'showrunner.cli', '--help'],
...                       capture_output=True, text=True, check=True).stdout
>>> all(command in help for command in ('start', 'shows', 'cues', 'scripts'))
True

```

Next: [02 — Cookbook](02-cookbook.md) for the commands you'll use day to day.
