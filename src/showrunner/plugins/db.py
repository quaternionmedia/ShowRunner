"""ShowDB plugin for Showrunner.

Provides database management and storage for show data, cues, and logs.

This plugin uses SQLModel to define the database schema and manage interactions
with the SQLite database. It provides a structured way to store and retrieve
show-related data, such as cues, configurations, and logs. The plugin also
integrates with the Showrunner framework to expose API routes for accessing
and manipulating the database contents.
"""

from fastapi import APIRouter, HTTPException
from sqlmodel import select

import showrunner
from showrunner.database import ShowDatabase
from showrunner.layers import (
    add_layer,
    delete_layer,
    ensure_layers,
    get_layer_types,
    get_layers,
    update_layer,
)
from showrunner.models import CUE_ORDER, Actor, Config, Cue, CueList, CueLog, Layer, Show

router = APIRouter(prefix='/db', tags=['ShowDB'])

# Module-level reference set during startup so routes can access the database.
_db: ShowDatabase | None = None
_pm = None  # pluggy PluginManager, for layer-type lookups


def get_db() -> ShowDatabase:
    """Return the current database instance.

    Plugins and pages should call this instead of capturing the ``db``
    reference at startup so they always use the latest connection after
    a live config reload.
    """
    if _db is None:
        raise RuntimeError('Database not initialised — is ShowDBPlugin loaded?')
    return _db


# ---------------------------------------------------------------------------
# FastAPI routes
# ---------------------------------------------------------------------------


@router.get('/shows')
async def list_shows():
    """Return all shows."""
    with _db.session() as s:
        shows = s.exec(select(Show).order_by(Show.name)).all()
        return [show.model_dump() for show in shows]


@router.get('/shows/{show_id}')
async def get_show(show_id: int):
    """Return a single show with its cue lists."""
    with _db.session() as s:
        show = s.get(Show, show_id)
        if not show:
            raise HTTPException(status_code=404, detail='Show not found')
        return show.model_dump()


@router.post('/shows')
async def create_show(name: str, venue: str | None = None):
    """Create a new show."""
    with _db.session() as s:
        show = Show(name=name, venue=venue)
        s.add(show)
        s.commit()
        ensure_layers(s, show.id, _pm)  # commits; refresh after it
        s.refresh(show)
        return show.model_dump()


@router.get('/shows/{show_id}/cues')
async def list_cues(show_id: int):
    """Return all cues across every cue list for a show."""
    with _db.session() as s:
        stmt = (
            select(Cue)
            .join(CueList)
            .where(CueList.show_id == show_id)
            .order_by(*CUE_ORDER)
        )
        cues = s.exec(stmt).all()
        return [cue.model_dump() for cue in cues]


@router.get('/shows/{show_id}/actors')
async def list_actors(show_id: int):
    """Return all actors for a show."""
    with _db.session() as s:
        actors = s.exec(
            select(Actor).where(Actor.show_id == show_id).order_by(Actor.name)
        ).all()
        return [actor.model_dump() for actor in actors]


@router.get('/shows/{show_id}/logs')
async def list_logs(show_id: int, limit: int = 100):
    """Return recent cue log entries for a show."""
    with _db.session() as s:
        logs = s.exec(
            select(CueLog)
            .where(CueLog.show_id == show_id)
            .order_by(CueLog.triggered_at.desc())
            .limit(limit)
        ).all()
        return [log.model_dump() for log in logs]


@router.get('/shows/{show_id}/config')
async def list_config(show_id: int):
    """Return all config entries for a show."""
    with _db.session() as s:
        configs = s.exec(
            select(Config).where(Config.show_id == show_id).order_by(Config.key)
        ).all()
        return {c.key: c.value for c in configs}


@router.get('/layer-types')
async def list_layer_types():
    """Return the layer type registry (built-in and plugin types)."""
    return list(get_layer_types(_pm).values())


@router.get('/shows/{show_id}/layers')
async def list_layers(show_id: int):
    """Return a show's layers in order."""
    with _db.session() as s:
        if s.get(Show, show_id) is None:
            raise HTTPException(status_code=404, detail='Show not found')
        return get_layers(s, show_id, _pm)


@router.post('/shows/{show_id}/layers')
async def create_layer(show_id: int, name: str, type: str, color: str = 'grey'):
    """Add a layer to a show. Unknown type or colour, or a duplicate name, is a 422."""
    with _db.session() as s:
        if s.get(Show, show_id) is None:
            raise HTTPException(status_code=404, detail='Show not found')
        try:
            layer = add_layer(s, show_id, name, type, color, _pm)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {'id': layer.id, 'name': layer.name, 'type': layer.type, 'color': layer.color}


@router.put('/layers/{layer_id}')
async def change_layer(
    layer_id: int,
    name: str | None = None,
    type: str | None = None,
    color: str | None = None,
    sequence: int | None = None,
):
    """Change a layer. A rename also renames the cues that use it."""
    changes = {k: v for k, v in dict(name=name, type=type, color=color, sequence=sequence).items()
               if v is not None}
    with _db.session() as s:
        layer = s.get(Layer, layer_id)
        if layer is None:
            raise HTTPException(status_code=404, detail='Layer not found')
        try:
            layer = update_layer(s, layer, _pm, **changes)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {'id': layer.id, 'name': layer.name, 'type': layer.type, 'color': layer.color}


@router.delete('/layers/{layer_id}')
async def remove_layer(layer_id: int):
    """Delete a layer. Refused with 409 while cues still use it."""
    with _db.session() as s:
        layer = s.get(Layer, layer_id)
        if layer is None:
            raise HTTPException(status_code=404, detail='Layer not found')
        try:
            delete_layer(s, layer)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {'deleted': layer_id}


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------


class ShowDBPlugin:
    """Database management and storage for show data, cues, and logs."""

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            'name': 'ShowDB',
            'description': 'SQLite database backend for shows, cues, and logs',
            'version': '0.1.0',
        }

    @showrunner.hookimpl(tryfirst=True)
    def showrunner_startup(self, app):
        global _db
        self._app = app
        config = getattr(app, 'config', None)
        db_path = config.database.path if config else 'show.db'
        db_echo = config.database.echo if config else False
        _db = ShowDatabase(db_path=db_path, echo=db_echo)
        _db.create_schema()
        global _pm
        _pm = getattr(app, 'pm', None)
        with _db.session() as s:
            for show in s.exec(select(Show)).all():
                ensure_layers(s, show.id, _pm)
        # Store on the app so other plugins can access it
        app.db = _db

    @showrunner.hookimpl
    def showrunner_config_changed(self, config, previous_config):
        """Reconnect to the database if the path or echo setting changed."""
        global _db
        old_db = previous_config.database
        new_db = config.database
        if old_db.path == new_db.path and old_db.echo == new_db.echo:
            return
        print(f"\033[33m⟳ Database switching from {old_db.path} → {new_db.path}\033[0m")
        if _db is not None:
            _db.close()
        _db = ShowDatabase(db_path=new_db.path, echo=new_db.echo)
        _db.create_schema()
        self._app.db = _db

    @showrunner.hookimpl
    def showrunner_shutdown(self, app):
        global _db
        if _db is not None:
            _db.close()
            _db = None

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
