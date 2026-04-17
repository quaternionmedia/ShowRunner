# ShowRunner

An application to help running live shows!

## About

**ShowRunner** is a collection of tools for live performances including script parsing, cue management, and more. It is built using Python and is designed to be flexible, extensible, and powerful for stage managers, designers, directors, and crew members involved in live productions.

## Documentation

Documentation can be found in the `docs/` directory, or available online: [quaternionmedia.github.io/ShowRunner/](https://quaternionmedia.github.io/ShowRunner/)

## Quick Start

### Install with pip

```bash
pip install showrunner[all]
```

The `[all]` extra includes optional dependencies for the admin panel, CLI, and other tools. You can also install just the core API with `pip install showrunner` and add extras as needed.

### Install from source

Requires [uv](https://docs.astral.sh/uv/)

#### Clone the repo

```bash
git clone https://github.com/quaternionmedia/ShowRunner.git
cd ShowRunner
```

#### Create a virtual environment (recommended)

```bash
uv venv
# Activate the virtual environment:
source .venv/bin/activate
```

If you are not using a virtual environment, run commands with `uv run [cmd]`

#### Install dependencies

Including dev tools and optional extras (admin panel, CLI, etc.)

```bash
uv sync --all-extras
```

## Start the API server

```bash
sr start
```

Visit [http://localhost:8000](http://localhost:8000) to access the dashboard and tools.

Use `sr --help` for CLI options and `sr [tool] --help` for tool-specific commands.

## Tools

- **ShowDashboard**: Show selector and control dashboard mounted at [/](http://localhost:8000/)
- **ShowScripter**: Script viewer (PDF, Fountain, etc.) with inline cue placement mounted at [/script](http://localhost:8000/script)
- **ShowAdmin**: Web-based database admin panel mounted at [/admin](http://localhost:8000/admin) _(requires `admin` group)_
- **ShowDesigner**: Allows users to design cues based on the parsed script, including setting up cue layers for specific integration with other tools
- **ShowProgrammer**: Synchronization with QLab and other tools to automatically create and label cues from a script
- **ShowMixer**: Operates sound mixers to monitor and control channels and effects during a performance (Behringer, Allen & Heath, etc.)
- **ShowLighter**: Integrates cues directly with lighting control systems for design and performance (ETC Eos, Chamsys, MA Lighting, etc.)
- **ShowManager**: Designed for Stage Managers to manage cues during a live performance, including triggering cues
- **ShowStopper**: A stopwatch with helpful features for live performances, such as logging and cue timing
- **ShowPrompter**: A teleprompter application that can display scripts and cues for performers and crew
- **ShowComms**: A communication tool for crew members to coordinate during a performance, including messaging and cue notifications
- **ShowCmd**: A command-line interface to interact with the system with a CLI or TUI
- **ShowRecorder**: A tool for archiving, annotating, and reviewing rehearsals and performances, including cue logs and performance notes

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, testing, linting, and plugin authoring guidelines.

```bash
uv run pytest          # run tests
uv run ruff check .    # lint
```

## Credit

Designed and developed by [Quaternion Media](https://quaternion.media)
