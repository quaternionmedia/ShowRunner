from typing import Optional

from typer import Argument, Option, Typer

from showrunner import ShowRunner
from showrunner.database import ShowDatabase
from showrunner.models import Show

cli = Typer(
    help="ShowRunner CLI - Manage your live performance plugins and commands.",
    context_settings={"help_option_names": ["-h", "--help"]},
)


@cli.command()
def create(
    name: list[str] = Argument(..., help="Name of the show"),
    venue: Optional[str] = Option(None, '--venue', '-v', help="Venue name"),
):
    """Create a new show."""
    show_name = ' '.join(name)
    db = ShowDatabase()
    db.create_schema()
    with db.session() as s:
        show = Show(name=show_name, venue=venue)
        s.add(show)
        s.commit()
        s.refresh(show)
        print(f'Created show "{show.name}" (id={show.id})')
    db.close()


@cli.command()
def start():
    """Start the ShowRunner API server."""
    try:
        from uvicorn import run
    except ImportError:
        raise Exception(
            """Uvicorn not found
        
            Uvicorn is required to run the API server. Please install it with `pip install uvicorn` or `uv sync`."""
        )

    show = ShowRunner()
    show.startup()
    try:
        run(show.api, host="0.0.0.0", port=8000)
    except KeyboardInterrupt:
        pass
    finally:
        show.shutdown()


if __name__ == "__main__":
    cli()
