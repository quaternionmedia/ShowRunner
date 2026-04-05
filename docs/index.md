# ShowRunner

A flexible and extensible live show management system.

## About

**ShowRunner** is a collection of tools for live performances including script parsing, cue management, timing, prompting, recording, logging, and many more.

### :battery: "Batteries included"

**ShowRunner** comes with many built-in tools for stage managers, designers, directors, and crew members for many types of live productions.

### :toolbox: Plugin architecture

The plugin architecture allows users to choose which elements to include and enables users to build their own. Plugins can be developed and shared independently, and can be loaded and unloaded at runtime without restarting the server.

## Documentation

### Quick Start

See [Getting Started](usage/getting-started.md) for installation instructions, first show walkthrough, and CLI reference.

### Tools

See [Tools](about/tools.md) for detailed descriptions of the built-in plugins and their features.

### Cookbook

See [Cookbook](cookbook/index.md) for examples of how to use ShowRunner to manage shows, cues, and other production elements.

### Reference

See [Reference](ref/index.md) for reference documentation for ShowRunner models, as well as API and CLI usage.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, testing, linting, and plugin authoring guidelines.

```bash
uv run pytest          # run tests
uv run ruff check .    # lint
```

## Credit

Designed and developed by [Quaternion Media](https://quaternion.media)
