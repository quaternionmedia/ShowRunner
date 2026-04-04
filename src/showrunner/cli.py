from pathlib import Path
from typing import Optional

import typer
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
# Helpers
# ---------------------------------------------------------------------------


def _db() -> ShowDatabase:
    cfg = load_config()
    db = ShowDatabase(db_path=cfg.database.path, echo=cfg.database.echo)
    db.create_schema()
    return db


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
    console.print(f"  API docs:   http://{effective_host}:{effective_port}/docs")
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
    show_id: int = Argument(..., help="ID of the show"),
):
    """Show details for a single show including cue lists and actors."""
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
    show_id: int = Argument(..., help="ID of the show to delete"),
    yes: bool = Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Delete a show and all its associated data."""
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
    show_id: int = Argument(..., help="ID of the show"),
):
    """List all cue lists for a show."""
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
    show_id: int = Argument(..., help="ID of the show"),
    name: str = Argument(..., help="Name for the cue list (e.g. 'Act 1')"),
    description: Optional[str] = Option(
        None, "--description", "-d", help="Optional description"
    ),
):
    """Create a new cue list for a show."""
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
    show_id: int = Argument(..., help="ID of the show"),
):
    """List all scripts for a show."""
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
    show_id: int = Argument(..., help="ID of the show"),
    title: str = Argument(..., help="Script title"),
    fmt: str = Option("fountain", "--format", "-f", help="Format: fountain, pdf, text"),
    content: Optional[str] = Option(
        None, "--content", "-c", help="Inline script content"
    ),
    file: Optional[str] = Option(
        None, "--file", help="Path to a file to read content from"
    ),
):
    """Add a script to a show."""
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
