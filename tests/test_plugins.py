"""Tests for the ShowRunner plugin system."""

import showrunner
from showrunner.app import ShowRunner, get_plugin_manager
from showrunner.plugins import get_builtin_plugins

EXPECTED_PLUGIN_COUNT = 16


# ---------------------------------------------------------------------------
# Built-in plugin registry
# ---------------------------------------------------------------------------


def test_get_builtin_plugins_count():
    """Exactly 15 plugins are registered by default."""
    assert len(get_builtin_plugins()) == EXPECTED_PLUGIN_COUNT


def test_get_builtin_plugins_are_classes():
    for plugin_class in get_builtin_plugins():
        assert isinstance(plugin_class, type)


# ---------------------------------------------------------------------------
# PluginManager
# ---------------------------------------------------------------------------


def test_get_plugin_manager_registers_all_plugins():
    pm = get_plugin_manager()
    metadata = pm.hook.showrunner_register()
    assert len(metadata) == EXPECTED_PLUGIN_COUNT


def test_all_plugins_have_required_metadata_keys():
    pm = get_plugin_manager()
    for meta in pm.hook.showrunner_register():
        assert 'name' in meta, f"Plugin missing 'name': {meta}"
        assert 'description' in meta, f"Plugin missing 'description': {meta}"
        assert 'version' in meta, f"Plugin missing 'version': {meta}"


def test_all_plugin_names_are_strings():
    pm = get_plugin_manager()
    for meta in pm.hook.showrunner_register():
        assert isinstance(meta['name'], str)
        assert meta['name']  # non-empty


def test_plugin_names_are_unique():
    pm = get_plugin_manager()
    names = [meta['name'] for meta in pm.hook.showrunner_register()]
    assert len(names) == len(set(names)), f"Duplicate plugin names: {names}"


# ---------------------------------------------------------------------------
# ShowRunner application
# ---------------------------------------------------------------------------


def test_showrunner_list_plugins_count():
    runner = ShowRunner()
    assert len(runner.list_plugins()) == EXPECTED_PLUGIN_COUNT


def test_showrunner_list_plugins_returns_dicts():
    runner = ShowRunner()
    for meta in runner.list_plugins():
        assert isinstance(meta, dict)


def test_showrunner_list_commands_is_list():
    runner = ShowRunner()
    commands = runner.list_commands()
    assert isinstance(commands, list)


def test_showrunner_routes_include_db_prefix():
    """The ShowDB plugin mounts routes under /db."""
    runner = ShowRunner()
    paths = [r.path for r in runner.api.routes]
    assert any('/db' in path for path in paths), f"Expected a /db route, got: {paths}"


def test_showrunner_api_is_fastapi_instance():
    from fastapi import FastAPI

    runner = ShowRunner()
    assert isinstance(runner.api, FastAPI)


def test_showrunner_has_plugin_manager():
    import pluggy

    runner = ShowRunner()
    assert isinstance(runner.pm, pluggy.PluginManager)


# ---------------------------------------------------------------------------
# Public API surface (__init__.py)
# ---------------------------------------------------------------------------


def test_hookimpl_is_exported():
    assert hasattr(showrunner, 'hookimpl')


def test_hookimpl_is_callable():
    assert callable(showrunner.hookimpl)


def test_showrunner_class_is_exported():
    assert hasattr(showrunner, 'ShowRunner')


# ---------------------------------------------------------------------------
# ShowAdmin graceful no-op when sqladmin missing
# ---------------------------------------------------------------------------


def test_admin_plugin_has_register_metadata():
    """ShowAdminPlugin.showrunner_register() works regardless of sqladmin install."""
    from showrunner.plugins.admin import ShowAdminPlugin

    plugin = ShowAdminPlugin()
    meta = plugin.showrunner_register()
    assert meta['name'] == 'ShowAdmin'
    assert 'description' in meta
    assert 'version' in meta
