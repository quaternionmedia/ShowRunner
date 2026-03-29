from showrunner import ShowRunner
from typer import Typer

cli = Typer(help="ShowRunner CLI - Manage your live performance plugins and commands.")


@cli.command()
def create():
    """Create a new show"""
    print("Creating a new show... (not implemented yet)")


@cli.command()
def serve():
    """Start the ShowRunner API server."""
    from uvicorn import run

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
