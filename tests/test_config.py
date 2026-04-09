"""Tests for the ShowRunner configuration module."""

from pathlib import Path

import pytest

from showrunner.config import (
    ShowRunnerConfig,
    find_config,
    load_config,
)


# ---------------------------------------------------------------------------
# find_config
# ---------------------------------------------------------------------------


class TestFindConfig:
    def test_finds_file_in_start_dir(self, tmp_path):
        config_file = tmp_path / "show.toml"
        config_file.write_text("[showrunner]\n")
        assert find_config(start=tmp_path) == config_file

    def test_returns_none_when_missing(self, tmp_path):
        assert find_config(start=tmp_path) is None

    def test_prefers_local_over_global(self, tmp_path, monkeypatch):
        local = tmp_path / "show.toml"
        local.write_text("[showrunner]\ncurrent-show = 1\n")

        global_dir = tmp_path / "home" / ".config" / "showrunner"
        global_dir.mkdir(parents=True)
        global_file = global_dir / "show.toml"
        global_file.write_text("[showrunner]\ncurrent-show = 99\n")

        assert find_config(start=tmp_path) == local


# ---------------------------------------------------------------------------
# load_config — defaults
# ---------------------------------------------------------------------------


class TestLoadConfigDefaults:
    def test_defaults_when_no_file(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent.toml")
        assert cfg.database.path == "show.db"
        assert cfg._source_path is None

    def test_default_values(self):
        cfg = ShowRunnerConfig()
        assert cfg.current_show is None
        assert cfg.database.path == "show.db"
        assert cfg.database.echo is False
        assert cfg.server.host == "0.0.0.0"
        assert cfg.server.port == 8000
        assert cfg.server.storage_secret == "showrunner"
        assert cfg.logging.level == "INFO"
        assert cfg.paths.scripts == "./scripts"
        assert cfg.paths.exports == "./exports"
        assert cfg.plugins.disabled == []
        assert cfg.plugins.settings == {}


# ---------------------------------------------------------------------------
# load_config — parsing
# ---------------------------------------------------------------------------


class TestLoadConfigParsing:
    def test_minimal_file(self, tmp_path):
        f = tmp_path / "show.toml"
        f.write_text("[showrunner]\ncurrent-show = 3\n")
        cfg = load_config(f)
        assert cfg.current_show == 3
        # Everything else should be defaults
        assert cfg.database.path == "show.db"

    def test_full_file(self, tmp_path):
        f = tmp_path / "show.toml"
        f.write_text(
            """\
[showrunner]
current-show = 5

[database]
path = "my.db"
echo = true

[server]
host = "127.0.0.1"
port = 9000
storage-secret = "s3cret"

[logging]
level = "DEBUG"

[paths]
scripts = "/opt/scripts"
exports = "/opt/exports"

[plugins]
disabled = ["admin", "recorder"]

[plugins.lighter]
console-ip = "10.0.0.5"
"""
        )
        cfg = load_config(f)
        assert cfg.current_show == 5
        assert cfg.database.path == "my.db"
        assert cfg.database.echo is True
        assert cfg.server.host == "127.0.0.1"
        assert cfg.server.port == 9000
        assert cfg.server.storage_secret == "s3cret"
        assert cfg.logging.level == "DEBUG"
        assert cfg.paths.scripts == "/opt/scripts"
        assert cfg.paths.exports == "/opt/exports"
        assert cfg.plugins.disabled == ["admin", "recorder"]
        assert cfg.plugins.settings == {"lighter": {"console-ip": "10.0.0.5"}}

    def test_empty_file(self, tmp_path):
        """An empty TOML file should produce all defaults."""
        f = tmp_path / "show.toml"
        f.write_text("")
        cfg = load_config(f)
        assert cfg.database.path == "show.db"
        assert cfg.server.port == 8000
        assert cfg._source_path == f

    def test_partial_sections(self, tmp_path):
        """Only supplied sections override; others stay default."""
        f = tmp_path / "show.toml"
        f.write_text("[database]\npath = \"other.db\"\n")
        cfg = load_config(f)
        assert cfg.database.path == "other.db"
        assert cfg.server.port == 8000  # untouched

    def test_source_path_tracked(self, tmp_path):
        f = tmp_path / "show.toml"
        f.write_text("[showrunner]\n")
        cfg = load_config(f)
        assert cfg._source_path == f

    def test_source_path_none_when_no_file(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent.toml")
        assert cfg._source_path is None


# ---------------------------------------------------------------------------
# load_config — error handling
# ---------------------------------------------------------------------------


class TestLoadConfigErrors:
    def test_invalid_toml_raises(self, tmp_path):
        f = tmp_path / "show.toml"
        f.write_text("not valid [[[ toml !!!!")
        with pytest.raises(Exception):
            load_config(f)

    def test_nonexistent_path_returns_defaults(self, tmp_path):
        cfg = load_config(tmp_path / "nope.toml")
        assert cfg.database.path == "show.db"
        assert cfg._source_path is None


# ---------------------------------------------------------------------------
# Plugin disable filtering (integration with app)
# ---------------------------------------------------------------------------


class TestPluginDisabling:
    def test_disabled_plugins_skipped(self):
        from showrunner.app import get_plugin_manager

        cfg = ShowRunnerConfig(plugins={"disabled": ["showdbplugin"], "settings": {}})
        pm = get_plugin_manager(config=cfg)
        names = [
            p.showrunner_register()["name"]
            for p in pm.get_plugins()
            if hasattr(p, "showrunner_register")
        ]
        assert "ShowDB" not in names

    def test_all_plugins_loaded_by_default(self):
        from showrunner.app import get_plugin_manager

        pm = get_plugin_manager()
        names = [
            p.showrunner_register()["name"]
            for p in pm.get_plugins()
            if hasattr(p, "showrunner_register")
        ]
        assert "ShowDB" in names
