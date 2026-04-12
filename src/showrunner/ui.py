"""Shared NiceGUI UI components for ShowRunner pages.

Provides the global navigation header bar used across all ShowRunner apps.
Plugins contribute navigation links and status icons via hooks.
"""

from __future__ import annotations
from contextlib import suppress

from nicegui import app as nicegui_app, ui
from sqlmodel import select

from showrunner.models import Show
from showrunner.plugins.db import get_db

# Module-level reference to the PluginManager, set during startup.
_pm = None


def set_plugin_manager(pm) -> None:
    """Store the plugin manager for use by shared UI components."""
    global _pm
    _pm = pm


def _current_show_name() -> str:
    """Return the name of the current show, or a fallback."""
    try:
        show_id = nicegui_app.storage.general.get("current_show")
        if show_id is not None:
            with get_db().session() as s:
                show = s.get(Show, int(show_id))
                if show:
                    return str(show)
    except Exception:
        pass
    return "No show selected"


def _get_show_options() -> dict[int, str]:
    """Return {id: name} for all shows, ordered by name."""
    try:
        with get_db().session() as s:
            shows = s.exec(select(Show).order_by(Show.name)).all()
            return {show.id: str(show) for show in shows}
    except Exception:
        return {}


def _current_show_id() -> int | None:
    """Return the stored current show id, or the first available show."""
    stored = nicegui_app.storage.general.get("current_show")
    if stored is not None:
        return int(stored)
    options = _get_show_options()
    if options:
        first = next(iter(options))
        nicegui_app.storage.general["current_show"] = first
        return first
    return None


def _current_script_id() -> int | None:
    """Return the stored current script id, or None."""
    stored = nicegui_app.storage.general.get("current_script")
    if stored is not None:
        return int(stored)
    return None


def _get_nav_items(pm) -> list[dict]:
    """Collect navigation items from all plugins via the hook."""
    results = pm.hook.showrunner_get_nav()
    items = []
    for group in results:
        if group:
            if isinstance(group, dict):
                items.append(group)
            elif isinstance(group, list):
                items.extend(group)
    return sorted(items, key=lambda x: x.get("order", 50))


def _get_status_icons(pm) -> list[dict]:
    """Collect status icons from all plugins via the hook."""
    results = pm.hook.showrunner_get_status()
    icons = []
    for group in results:
        if group:
            if isinstance(group, dict):
                icons.append(group)
            elif isinstance(group, list):
                icons.extend(group)
    return icons


def header(pm=None) -> None:
    """Render the shared ShowRunner header bar.

    Args:
        pm: The pluggy PluginManager instance. When ``None``, uses the
            module-level reference set by :func:`set_plugin_manager`.
    """
    if pm is None:
        pm = _pm
    nav_items = _get_nav_items(pm) if pm else []
    status_icons = _get_status_icons(pm) if pm else []

    with (
        ui.header()
        .classes("items-center justify-between px-4 py-0 shadow-md")
        .style("background: #1a1a2e; height: 48px;")
    ):
        # -- LEFT: hamburger menu + branding --------------------------------
        with ui.row().classes("items-center gap-2 no-wrap"):
            with ui.button(icon="menu").props("flat round dense color=white"):
                with ui.menu().classes("min-w-48") as nav_menu:
                    # Home always first
                    ui.menu_item(
                        "Dashboard",
                        on_click=lambda: ui.navigate.to("/"),
                    ).classes("gap-2")

                    # Plugin-contributed nav items (skip "/" — it's hardcoded above)
                    for item in nav_items:
                        if item.get("path") == "/":
                            continue
                        label = item.get("label", "")
                        path = item.get("path", "/")
                        icon = item.get("icon")
                        with ui.menu_item(
                            on_click=lambda _, p=path: ui.navigate.to(p),
                        ).classes("gap-2"):
                            if icon:
                                ui.icon(icon).classes("text-lg")
                            ui.label(label)

                    ui.separator()
                    # /api docs link
                    with ui.menu_item(
                        on_click=lambda: ui.navigate.to("/api"),
                    ).classes("gap-2"):
                        ui.icon("api").classes("text-lg")
                        ui.label("API Docs")
                    # If the admin plugin is present, show an admin link
                    with suppress(ImportError):
                        import sqladmin  # noqa: F401

                        with ui.menu_item(
                            on_click=lambda: ui.navigate.to("/admin/"),
                        ).classes("gap-2"):
                            ui.icon("admin_panel_settings").classes("text-lg")
                            ui.label("Admin")

            ui.link("ShowRunner", "/").classes(
                "text-white text-weight-bold text-body1 no-underline"
            )

        # -- CENTER: show selector + plugin status icons --------------------
        with ui.row().classes("items-center gap-3 no-wrap"):
            ui.icon("theaters").classes("text-amber text-lg")
            show_options = _get_show_options()
            current = _current_show_id()

            def _on_show_change(e):
                nicegui_app.storage.general["current_show"] = e.value
                ui.navigate.reload()

            if show_options:
                ui.select(
                    options=show_options,
                    value=current,
                    on_change=_on_show_change,
                ).props('dense dark borderless').classes(
                    "text-white min-w-[12rem]"
                ).style(
                    "color: white;"
                )
            else:
                ui.label("No shows").classes("text-white text-body2 text-grey")

            # Plugin status icons
            for si in status_icons:
                icon_name = si.get("icon", "circle")
                tooltip = si.get("tooltip", "")
                color = si.get("color", "grey")
                btn = ui.icon(icon_name).classes(f"text-{color} text-lg cursor-pointer")
                if tooltip:
                    btn.tooltip(tooltip)

        # -- RIGHT: user/settings icon --------------------------------------
        with ui.row().classes("items-center gap-1 no-wrap"):
            with ui.button(icon="person").props("flat round dense color=white"):
                with ui.menu().classes("min-w-40"):
                    with ui.menu_item(
                        on_click=lambda: ui.navigate.to("/admin/"),
                    ).classes("gap-2"):
                        ui.icon("admin_panel_settings").classes("text-lg")
                        ui.label("Admin")
