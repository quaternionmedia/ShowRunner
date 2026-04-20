from loguru import logger
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

try:
    import typer
except ImportError:
    print(
        "Typer is required for the CLI. "
        "Run: `uv sync --extra cli` or `pip install showrunner[cli]`",
        file=sys.stderr,
    )
    sys.exit(1)
from rich.console import Console
from rich.table import Table
from sqlmodel import select
from typer import Argument, Option, Typer

from showrunner import ShowRunner
from showrunner.config import find_config, load_config
from showrunner.database import ShowDatabase
from showrunner.models import Cue, CueList, Script, Show

console = Console()

# ---------------------------------------------------------------------------
# Root CLI
# ---------------------------------------------------------------------------

cli = Typer(
    help="ShowRunner CLI - Manage your live performance plugins and commands.",
    context_settings={"help_option_names": ["-h", "--help"]},
    no_args_is_help=True,
)


@cli.callback()
def main(
    log_level: Optional[str] = Option(
        None,
        "--log-level",
        "-L",
        help="Set log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    ),
    verbose: bool = Option(
        False, "--verbose", "-v", help="Shorthand for --log-level DEBUG"
    ),
    quiet: bool = Option(
        False, "--quiet", "-q", help="Shorthand for --log-level WARNING"
    ),
):
    """ShowRunner CLI - Manage your live performance plugins and commands."""
    if verbose and quiet:
        console.print("[red]Cannot use --verbose and --quiet together.[/red]")
        raise typer.Exit(1)

    level = log_level.upper() if log_level else None
    if verbose:
        level = "DEBUG"
    elif quiet:
        level = "WARNING"
    logger.remove()
    if level is not None:
        logger.add(
            sink=sys.stderr,
            level=level,
            format="{time:YYYY-MM-DD HH:mm:ss} | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        )


# ---------------------------------------------------------------------------
# Sub-app: shows
# ---------------------------------------------------------------------------

shows_app = Typer(
    help="Manage shows.",
    no_args_is_help=True,
)
cli.add_typer(shows_app, name="shows")

# ---------------------------------------------------------------------------
# Sub-app: cue-lists
# ---------------------------------------------------------------------------

cue_lists_app = Typer(
    help="Manage cue lists within a show.",
    no_args_is_help=True,
)
cli.add_typer(cue_lists_app, name="cue-lists")

# ---------------------------------------------------------------------------
# Sub-app: cues
# ---------------------------------------------------------------------------

cues_app = Typer(
    help="Manage cues within a cue list.",
    no_args_is_help=True,
)
cli.add_typer(cues_app, name="cues")

# ---------------------------------------------------------------------------
# Sub-app: scripts
# ---------------------------------------------------------------------------

scripts_app = Typer(
    help="Manage scripts within a show.",
    no_args_is_help=True,
)
cli.add_typer(scripts_app, name="scripts")

# ---------------------------------------------------------------------------
# Sub-app: plugin
# ---------------------------------------------------------------------------

plugin_app = Typer(
    help="Manage ShowRunner plugins.",
    no_args_is_help=True,
)
cli.add_typer(plugin_app, name="plugin")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db() -> ShowDatabase:
    cfg = load_config()
    db = ShowDatabase(db_path=cfg.database.path, echo=cfg.database.echo)
    db.create_schema()
    return db


def _show_id(show_id: int | None) -> int:
    """Resolve show_id from explicit value or config default."""
    if show_id is None:
        show_id = load_config().current_show
    if show_id is None:
        console.print("[red]No show specified and no current-show set in config.[/red]")
        raise typer.Exit(1)
    return show_id


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------


@cli.command()
def start(
    host: Optional[str] = Option(None, "--host", "-H", help="Host address to bind to"),
    port: Optional[int] = Option(None, "--port", "-p", help="Port to listen on"),
    config: Optional[Path] = Option(
        None, "--config", "-c", help="Path to `show.toml` config file"
    ),
    reload: bool = Option(
        False,
        "--reload",
        "-r",
        help="Enable auto-reload (development). Requires uvicorn directly.",
    ),
):
    """Start the ShowRunner API server."""
    try:
        from uvicorn import run as uvicorn_run
    except ImportError:
        typer.echo("Uvicorn is required. Run: uv sync", err=True)
        raise typer.Exit(1)

    if reload:
        typer.echo(
            "Error: --reload is not supported via `sr start`.\n"
            "Use: uvicorn showrunner.app:app --reload  (see scripts/dev)\n"
            "Or:  uv run python scripts/dev",
            err=True,
        )
        raise typer.Exit(1)

    show = ShowRunner(config_path=config)
    # CLI flags override config file values
    effective_host = host or show.config.server.host
    effective_port = port or show.config.server.port
    show.startup()
    console.print(
        f"[bold green]ShowRunner[/bold green] listening on [cyan]http://{effective_host}:{effective_port}[/cyan]"
    )
    console.print(f"  Dashboard:  http://{effective_host}:{effective_port}/")
    console.print(f"  Scripts:    http://{effective_host}:{effective_port}/script")
    console.print(f"  Admin:      http://{effective_host}:{effective_port}/admin")
    console.print(f"  API docs:   http://{effective_host}:{effective_port}/api")
    try:
        uvicorn_run(show.api, host=effective_host, port=effective_port)
    except KeyboardInterrupt:
        pass
    finally:
        show.shutdown()
        console.print("[yellow]ShowRunner stopped.[/yellow]")


@cli.command()
def plugins():
    """List all loaded ShowRunner plugins."""
    _plugin_list()


# ---------------------------------------------------------------------------
# Plugin sub-commands
# ---------------------------------------------------------------------------


def _plugin_list() -> None:
    app = ShowRunner()
    metadata = app.list_plugins()

    table = Table(title="Loaded Plugins", show_lines=True)
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Version", style="dim")
    table.add_column("Description")

    for meta in metadata:
        table.add_row(
            meta.get("name", ""), meta.get("version", ""), meta.get("description", "")
        )

    console.print(table)


_PLUGIN_INIT_TEMPLATE = '''\
"""{{class_name}} - A ShowRunner plugin."""

from fastapi import APIRouter

import showrunner

router = APIRouter(prefix="/{{slug}}", tags=["{{class_name}}"])


@router.get("/")
async def index():
    return {"plugin": "{{class_name}}", "status": "ok"}


class {{class_name}}:
    """{{description}}"""

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "{{class_name}}",
            "description": "{{description}}",
            "version": "0.1.0",
        }

    @showrunner.hookimpl
    def showrunner_startup(self, app):
        pass

    @showrunner.hookimpl
    def showrunner_shutdown(self, app):
        pass

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        return router

    @showrunner.hookimpl
    def showrunner_get_commands(self):
        return []

    @showrunner.hookimpl
    def showrunner_get_nav(self):
        return None

    @showrunner.hookimpl
    def showrunner_get_status(self):
        return None


plugin = {{class_name}}()
'''

_PLUGIN_PYPROJECT_TEMPLATE = """\
[project]
name = "{{package_name}}"
version = "0.1.0"
description = "{{description}}"
requires-python = ">=3.10"
dependencies = ["showrunner"]

[project.entry-points."showrunner"]
{{module_name}} = "{{module_name}}:plugin"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""


def _slugify(name: str) -> str:
    """Convert a name like 'My Plugin' to 'my-plugin'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _to_module(slug: str) -> str:
    """Convert 'my-plugin' to 'my_plugin'."""
    return slug.replace("-", "_")


def _to_class(slug: str) -> str:
    """Convert 'my-plugin' to 'MyPlugin'."""
    return "".join(part.capitalize() for part in slug.split("-"))


def _render(template: str, **kwargs: str) -> str:
    text = template
    for key, value in kwargs.items():
        text = text.replace("{{" + key + "}}", value)
    return text


@plugin_app.command("list")
def plugin_list():
    """List all loaded ShowRunner plugins."""
    _plugin_list()


@plugin_app.command("create")
def plugin_create(
    name: list[str] = Argument(..., help="Plugin name (e.g. 'My Plugin')"),
    output_dir: Path = Option(
        ".", "--output", "-o", help="Parent directory for the new plugin package"
    ),
    description: str = Option(
        "A ShowRunner plugin.", "--description", "-d", help="Short description"
    ),
    install: bool = Option(
        True, "--install/--no-install", help="Install the plugin in editable mode"
    ),
):
    """Scaffold a new ShowRunner plugin package.

    Creates a ready-to-use plugin directory with the entry point
    pre-configured so ShowRunner discovers it automatically.
    """
    plugin_name = " ".join(name)
    slug = _slugify(plugin_name)
    module_name = _to_module(slug)
    class_name = _to_class(slug)
    package_name = slug

    plugin_dir = output_dir.resolve() / package_name
    src_dir = plugin_dir / module_name

    if plugin_dir.exists():
        console.print(f"[red]Directory already exists: {plugin_dir}[/red]")
        raise typer.Exit(1)

    src_dir.mkdir(parents=True)

    init_content = _render(
        _PLUGIN_INIT_TEMPLATE,
        class_name=class_name,
        slug=slug,
        description=description,
    )
    (src_dir / "__init__.py").write_text(init_content, encoding="utf-8")

    pyproject_content = _render(
        _PLUGIN_PYPROJECT_TEMPLATE,
        package_name=package_name,
        module_name=module_name,
        class_name=class_name,
        description=description,
    )
    (plugin_dir / "pyproject.toml").write_text(pyproject_content, encoding="utf-8")

    console.print(f"[green]Created[/green] plugin package at [bold]{plugin_dir}[/bold]")
    console.print(f"  Module:     {module_name}")
    console.print(f"  Class:      {class_name}")
    console.print(f'  Entry point: {module_name} = "{module_name}:plugin"')

    if install:
        console.print("\n[dim]Installing in editable mode…[/dim]")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(plugin_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            console.print(
                f"[green]Installed[/green] [bold]{package_name}[/bold] – "
                "it will load on the next [cyan]sr start[/cyan]"
            )
        else:
            console.print(f"[yellow]pip install failed:[/yellow]\n{result.stderr}")
            console.print(f"Install manually: [cyan]pip install -e {plugin_dir}[/cyan]")
    else:
        console.print(f"\nTo activate: [cyan]pip install -e {plugin_dir}[/cyan]")


@cli.command("list")
def list_shows():
    """List all shows (shorthand for `sr shows list`)."""
    _shows_list()


@cli.command("create")
def create(
    name: list[str] = Argument(..., help="Name of the show"),
    venue: Optional[str] = Option(None, "--venue", "-v", help="Venue name"),
):
    """Create a new show (shorthand for `sr shows create`)."""
    _shows_create(name, venue)


# ---------------------------------------------------------------------------
# Shows sub-commands
# ---------------------------------------------------------------------------


def _shows_list() -> None:
    db = _db()
    shows = db.list_shows()
    db.close()

    if not shows:
        console.print("[dim]No shows found. Create one with: sr create <name>[/dim]")
        return

    table = Table(title="Shows", show_lines=True)
    table.add_column("ID", style="cyan", justify="right")
    table.add_column("Name")
    table.add_column("Venue", style="dim")
    table.add_column("Created", style="dim")

    for s in shows:
        table.add_row(
            str(s.id),
            s.name,
            s.venue or "—",
            s.created_at.strftime("%Y-%m-%d") if s.created_at else "",
        )

    console.print(table)


def _shows_create(name: list[str], venue: Optional[str]) -> None:
    show_name = " ".join(name)
    db = _db()
    with db.session() as session:
        show = Show(name=show_name, venue=venue)
        session.add(show)
        session.commit()
        session.refresh(show)
        console.print(
            f'[green]Created[/green] show [bold]"{show.name}"[/bold] (id={show.id})'
        )
    db.close()


@shows_app.command("list")
def shows_list():
    """List all shows."""
    _shows_list()


@shows_app.command("create")
def shows_create(
    name: list[str] = Argument(..., help="Name of the show"),
    venue: Optional[str] = Option(None, "--venue", "-v", help="Venue name"),
):
    """Create a new show."""
    _shows_create(name, venue)


@shows_app.command("info")
def shows_info(
    show_id: Optional[int] = Option(
        None, "--show", "-s", help="ID of the show (default: current-show from config)"
    ),
):
    """Show details for a single show including cue lists and actors."""
    show_id = _show_id(show_id)
    db = _db()
    show = db.get_show(show_id)
    db.close()

    if not show:
        console.print(f"[red]Show {show_id} not found.[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]{show.name}[/bold] (id={show.id})")
    if show.venue:
        console.print(f"  Venue:   {show.venue}")
    created = show.created_at.strftime("%Y-%m-%d %H:%M") if show.created_at else "—"
    console.print(f"  Created: {created}")


@shows_app.command("delete")
def shows_delete(
    show_id: Optional[int] = Option(
        None,
        "--show",
        "-s",
        help="ID of the show to delete (default: current-show from config)",
    ),
    yes: bool = Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Delete a show and all its associated data."""
    show_id = _show_id(show_id)
    db = _db()
    show = db.get_show(show_id)

    if not show:
        console.print(f"[red]Show {show_id} not found.[/red]")
        db.close()
        raise typer.Exit(1)

    if not yes:
        typer.confirm(
            f'Delete show "{show.name}" (id={show_id}) and all its data?', abort=True
        )

    with db.session() as session:
        s = session.get(Show, show_id)
        session.delete(s)
        session.commit()

    db.close()
    console.print(
        f'[yellow]Deleted[/yellow] show [bold]"{show.name}"[/bold] (id={show_id})'
    )


# ---------------------------------------------------------------------------
# Cue lists sub-commands
# ---------------------------------------------------------------------------


@cue_lists_app.command("list")
def cue_lists_list(
    show_id: Optional[int] = Option(
        None, "--show", "-s", help="ID of the show (default: current-show from config)"
    ),
):
    """List all cue lists for a show."""
    show_id = _show_id(show_id)
    db = _db()
    with db.session() as session:
        cue_lists = list(
            session.exec(
                select(CueList).where(CueList.show_id == show_id).order_by(CueList.name)
            )
        )

    db.close()

    if not cue_lists:
        console.print(f"[dim]No cue lists found for show {show_id}.[/dim]")
        return

    table = Table(title=f"Cue Lists – Show {show_id}", show_lines=True)
    table.add_column("ID", style="cyan", justify="right")
    table.add_column("Name")
    table.add_column("Description", style="dim")

    for cl in cue_lists:
        table.add_row(str(cl.id), cl.name, cl.description or "—")

    console.print(table)


@cue_lists_app.command("create")
def cue_lists_create(
    name: str = Argument(..., help="Name for the cue list (e.g. 'Act 1')"),
    show_id: Optional[int] = Option(
        None, "--show", "-s", help="ID of the show (default: current-show from config)"
    ),
    description: Optional[str] = Option(
        None, "--description", "-d", help="Optional description"
    ),
):
    """Create a new cue list for a show."""
    show_id = _show_id(show_id)
    db = _db()

    show = db.get_show(show_id)
    if not show:
        console.print(f"[red]Show {show_id} not found.[/red]")
        db.close()
        raise typer.Exit(1)

    with db.session() as session:
        cue_list = CueList(show_id=show_id, name=name, description=description)
        session.add(cue_list)
        session.commit()
        session.refresh(cue_list)
        console.print(
            f'[green]Created[/green] cue list [bold]"{cue_list.name}"[/bold] (id={cue_list.id}) '
            f'for show [bold]"{show.name}"[/bold]'
        )

    db.close()


# ---------------------------------------------------------------------------
# Cues sub-commands
# ---------------------------------------------------------------------------


@cues_app.command("list")
def cues_list(
    cue_list_id: int = Argument(..., help="ID of the cue list"),
):
    """List all cues in a cue list."""
    db = _db()
    with db.session() as session:
        cues = list(
            session.exec(
                select(Cue)
                .where(Cue.cue_list_id == cue_list_id)
                .order_by(Cue.sequence, Cue.number, Cue.point)
            )
        )
    db.close()

    if not cues:
        console.print(f"[dim]No cues found in cue list {cue_list_id}.[/dim]")
        return

    table = Table(title=f"Cues – Cue List {cue_list_id}", show_lines=True)
    table.add_column("ID", style="cyan", justify="right")
    table.add_column("Number", justify="right")
    table.add_column("Name")
    table.add_column("Layer", style="dim")
    table.add_column("Type", style="dim")
    table.add_column("Notes", style="dim")

    for cue in cues:
        num = f"{cue.number}.{cue.point}" if cue.point else str(cue.number)
        table.add_row(
            str(cue.id),
            num,
            cue.name or "—",
            cue.layer or "—",
            cue.cue_type or "—",
            cue.notes or "—",
        )

    console.print(table)


@cues_app.command("add")
def cues_add(
    cue_list_id: int = Argument(..., help="ID of the cue list"),
    number: int = Argument(..., help="Cue number"),
    name: str = Argument(..., help="Cue name or label"),
    layer: Optional[str] = Option(
        None, "--layer", "-l", help="Layer: Lights, Sound, Video, Audio, Stage"
    ),
    cue_type: Optional[str] = Option(
        None, "--type", "-t", help="Cue type: Network, MIDI, Audio, Video, etc."
    ),
    notes: Optional[str] = Option(None, "--notes", "-n", help="Optional notes"),
    point: int = Option(0, "--point", help="Sub-cue point (e.g. 1 for cue 5.1)"),
):
    """Add a cue to a cue list."""
    db = _db()
    with db.session() as session:
        cue_list = session.get(CueList, cue_list_id)
        if not cue_list:
            console.print(f"[red]Cue list {cue_list_id} not found.[/red]")
            db.close()
            raise typer.Exit(1)

        cue = Cue(
            cue_list_id=cue_list_id,
            number=number,
            point=point,
            name=name,
            layer=layer,
            cue_type=cue_type,
            notes=notes,
        )
        session.add(cue)
        session.commit()
        session.refresh(cue)

        num_str = f"{cue.number}.{cue.point}" if cue.point else str(cue.number)
        console.print(
            f'[green]Added[/green] cue [bold]{num_str} "{cue.name}"[/bold] (id={cue.id}) '
            f'to cue list [bold]"{cue_list.name}"[/bold]'
        )

    db.close()


# ---------------------------------------------------------------------------
# Scripts sub-commands
# ---------------------------------------------------------------------------


@scripts_app.command("list")
def scripts_list(
    show_id: Optional[int] = Option(
        None, "--show", "-s", help="ID of the show (default: current-show from config)"
    ),
):
    """List all scripts for a show."""
    show_id = _show_id(show_id)
    db = _db()
    with db.session() as session:
        script_rows = list(
            session.exec(
                select(Script).where(Script.show_id == show_id).order_by(Script.title)
            )
        )
    db.close()

    if not script_rows:
        console.print(f"[dim]No scripts found for show {show_id}.[/dim]")
        return

    table = Table(title=f"Scripts – Show {show_id}", show_lines=True)
    table.add_column("ID", style="cyan", justify="right")
    table.add_column("Title")
    table.add_column("Format", style="dim")
    table.add_column("Created", style="dim")

    for sc in script_rows:
        table.add_row(
            str(sc.id),
            sc.title,
            sc.format,
            sc.created_at.strftime("%Y-%m-%d") if sc.created_at else "",
        )

    console.print(table)


@scripts_app.command("add")
def scripts_add(
    title: str = Argument(..., help="Script title"),
    show_id: Optional[int] = Option(
        None, "--show", "-s", help="ID of the show (default: current-show from config)"
    ),
    fmt: str = Option("fountain", "--format", "-f", help="Format: fountain, pdf, text"),
    content: Optional[str] = Option(
        None, "--content", "-c", help="Inline script content"
    ),
    file: Optional[str] = Option(
        None, "--file", help="Path to a file to read content from"
    ),
):
    """Add a script to a show."""
    show_id = _show_id(show_id)
    db = _db()

    show = db.get_show(show_id)
    if not show:
        console.print(f"[red]Show {show_id} not found.[/red]")
        db.close()
        raise typer.Exit(1)

    body: str | None = content
    if file:
        from pathlib import Path

        path = Path(file)
        if not path.exists():
            console.print(f"[red]File not found: {file}[/red]")
            db.close()
            raise typer.Exit(1)
        body = path.read_text(encoding="utf-8")

    with db.session() as session:
        script = Script(show_id=show_id, title=title, format=fmt, content=body)
        session.add(script)
        session.commit()
        session.refresh(script)
        console.print(
            f'[green]Added[/green] script [bold]"{script.title}"[/bold] '
            f'(id={script.id}, format={script.format}) '
            f'to show [bold]"{show.name}"[/bold]'
        )

    db.close()


@scripts_app.command("delete")
def scripts_delete(
    script_id: int = Argument(..., help="ID of the script to delete"),
    yes: bool = Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Delete a script."""
    db = _db()
    with db.session() as session:
        script = session.get(Script, script_id)
        if not script:
            console.print(f"[red]Script {script_id} not found.[/red]")
            db.close()
            raise typer.Exit(1)

        if not yes:
            typer.confirm(
                f'Delete script "{script.title}" (id={script_id})?', abort=True
            )

        session.delete(script)
        session.commit()

    db.close()
    console.print(
        f'[yellow]Deleted[/yellow] script [bold]"{script.title}"[/bold] (id={script_id})'
    )


# ---------------------------------------------------------------------------
# Sub-app: config
# ---------------------------------------------------------------------------

config_app = Typer(
    help="Manage ShowRunner configuration.",
    no_args_is_help=True,
)
cli.add_typer(config_app, name="config")


@config_app.command("path")
def config_path():
    """Print the resolved config file path."""
    path = find_config()
    if path is None:
        console.print("[dim]No config file found (using built-in defaults).[/dim]")
    else:
        console.print(str(path))


@config_app.command("show")
def config_show(
    config: Optional[Path] = Option(None, "--config", "-c", help="Path to show.toml"),
):
    """Print the resolved configuration."""
    cfg = load_config(config)
    console.print(cfg.model_dump_json(indent=2))


@config_app.command("init")
def config_init(
    force: bool = Option(False, "--force", "-f", help="Overwrite existing file"),
):
    """Create a default show.toml in the current directory."""
    target = Path.cwd() / "show.toml"
    if target.exists() and not force:
        console.print(
            f"[yellow]{target} already exists. Use --force to overwrite.[/yellow]"
        )
        raise typer.Exit(1)

    target.write_text(
        """[showrunner]
current-show = 1

[database]
# path = "show.db"
# echo = false

[server]
# host = "0.0.0.0"
# port = 8000
# storage-secret = "showrunner"

[logging]
# level = "INFO"

[paths]
# scripts = "./scripts"
# exports = "./exports"

[plugins]
# disabled = []

# Per-plugin settings use [plugins.<name>] sections:
# [plugins.lighter]
# console-ip = "192.168.1.100"
""",
        encoding="utf-8",
    )
    console.print(f"[green]Created[/green] {target}")


if __name__ == "__main__":
    cli()
