"""ShowDB plugin for Showrunner.

Provides database management and storage for show data, cues, and logs.

This plugin uses SQLModel to define the database schema and manage interactions
with the SQLite database. It provides a structured way to store and retrieve
show-related data, such as cues, configurations, and logs. The plugin also
integrates with the Showrunner framework to expose API routes for accessing
and manipulating the database contents.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import select

import showrunner
from showrunner.database import ShowDatabase
from showrunner.models import Actor, Config, Cue, CueList, CueLog, Script, Show


class ScriptCreate(BaseModel):
    """Body for POST /db/shows/{show_id}/scripts."""

    title: str
    format: str = "fountain"
    content: Optional[str] = None


class CueListCreate(BaseModel):
    """Body for POST /db/shows/{show_id}/cue-lists."""

    name: str
    description: Optional[str] = None


class CueCreate(BaseModel):
    """Body for POST /db/cue-lists/{cue_list_id}/cues."""

    number: int
    point: int = 0
    name: str
    layer: Optional[str] = None
    notes: Optional[str] = None
    color: Optional[str] = None


class CueUpdate(BaseModel):
    """Partial update body for PATCH /db/cues/{cue_id}."""

    name: Optional[str] = None
    notes: Optional[str] = None
    layer: Optional[str] = None
    color: Optional[str] = None

router = APIRouter(prefix='/db', tags=['ShowDB'])

# Module-level reference set during startup so routes can access the database.
_db: ShowDatabase | None = None


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
        s.refresh(show)
        return show.model_dump()


@router.get('/shows/{show_id}/scripts')
async def list_scripts(show_id: int):
    """Return all scripts for a show."""
    with _db.session() as s:
        show = s.get(Show, show_id)
        if not show:
            raise HTTPException(status_code=404, detail='Show not found')
        scripts = s.exec(
            select(Script).where(Script.show_id == show_id).order_by(Script.id)
        ).all()
        return [sc.model_dump() for sc in scripts]


@router.post('/shows/{show_id}/scripts')
async def create_script(show_id: int, body: ScriptCreate):
    """Add a script to a show."""
    with _db.session() as s:
        show = s.get(Show, show_id)
        if not show:
            raise HTTPException(status_code=404, detail='Show not found')
        script = Script(
            show_id=show_id,
            title=body.title,
            format=body.format,
            content=body.content,
        )
        s.add(script)
        s.commit()
        s.refresh(script)
        return script.model_dump()


@router.get('/shows/{show_id}/cue-lists')
async def list_cue_lists(show_id: int):
    """Return all cue lists for a show."""
    with _db.session() as s:
        show = s.get(Show, show_id)
        if not show:
            raise HTTPException(status_code=404, detail='Show not found')
        cue_lists = s.exec(
            select(CueList).where(CueList.show_id == show_id).order_by(CueList.id)
        ).all()
        return [cl.model_dump() for cl in cue_lists]


@router.post('/shows/{show_id}/cue-lists')
async def create_cue_list(show_id: int, body: CueListCreate):
    """Create a new cue list for a show."""
    with _db.session() as s:
        show = s.get(Show, show_id)
        if not show:
            raise HTTPException(status_code=404, detail='Show not found')
        cue_list = CueList(
            show_id=show_id,
            name=body.name,
            description=body.description,
        )
        s.add(cue_list)
        s.commit()
        s.refresh(cue_list)
        return cue_list.model_dump()


@router.get('/cue-lists/{cue_list_id}/cues')
async def list_cues_by_list(cue_list_id: int):
    """Return all cues in a cue list ordered by (number, point)."""
    with _db.session() as s:
        cue_list = s.get(CueList, cue_list_id)
        if not cue_list:
            raise HTTPException(status_code=404, detail=f'CueList {cue_list_id} not found')
        cues = s.exec(
            select(Cue)
            .where(Cue.cue_list_id == cue_list_id)
            .order_by(Cue.number, Cue.point)
        ).all()
        return [cue.model_dump() for cue in cues]


@router.post('/cue-lists/{cue_list_id}/cues')
async def create_cue(cue_list_id: int, body: CueCreate):
    """Add a cue to a cue list."""
    with _db.session() as s:
        cue_list = s.get(CueList, cue_list_id)
        if not cue_list:
            raise HTTPException(status_code=404, detail=f'CueList {cue_list_id} not found')
        cue = Cue(
            cue_list_id=cue_list_id,
            number=body.number,
            point=body.point,
            name=body.name,
            layer=body.layer,
            notes=body.notes,
            color=body.color,
        )
        s.add(cue)
        s.commit()
        s.refresh(cue)
        return cue.model_dump()


@router.get('/shows/{show_id}/cues')
async def list_cues(
    show_id: int,
    cue_list_id: Optional[int] = Query(None, description="Filter by cue list ID"),
):
    """Return cues for a show, optionally filtered to one cue list."""
    with _db.session() as s:
        stmt = select(Cue).join(CueList).where(CueList.show_id == show_id)
        if cue_list_id is not None:
            stmt = stmt.where(Cue.cue_list_id == cue_list_id)
        stmt = stmt.order_by(Cue.number, Cue.point)
        cues = s.exec(stmt).all()
        return [cue.model_dump() for cue in cues]


@router.patch('/cues/{cue_id}')
async def update_cue(cue_id: int, body: CueUpdate):
    """Partially update a cue's name, notes, layer, or color."""
    with _db.session() as s:
        cue = s.get(Cue, cue_id)
        if not cue:
            raise HTTPException(status_code=404, detail=f'Cue {cue_id} not found')
        update_data = body.model_dump(exclude_unset=True)
        if not update_data:
            raise HTTPException(status_code=422, detail='No fields provided to update')
        for field, value in update_data.items():
            setattr(cue, field, value)
        s.add(cue)
        s.commit()
        s.refresh(cue)
        return cue.model_dump()


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
