"""ShowDB plugin for Showrunner.

Provides database management and storage for show data, cues, and logs.

This plugin uses SQLModel to define the database schema and manage interactions
with the SQLite database. It provides a structured way to store and retrieve
show-related data, such as cues, configurations, and logs. The plugin also
integrates with the Showrunner framework to expose API routes for accessing
and manipulating the database contents.
"""

from fastapi import APIRouter
from sqlmodel import select

import showrunner
from showrunner.database import ShowDatabase
from showrunner.models import Actor, Config, Cue, CueList, CueLog, Script, Show

router = APIRouter(prefix='/db', tags=['ShowDB'])

# Module-level reference set during startup so routes can access the database.
_db: ShowDatabase | None = None


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
            return {'error': 'Show not found'}, 404
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


@router.get('/shows/{show_id}/cues')
async def list_cues(show_id: int):
    """Return all cues across every cue list for a show."""
    with _db.session() as s:
        stmt = (
            select(Cue)
            .join(CueList)
            .where(CueList.show_id == show_id)
            .order_by(Cue.number, Cue.point)
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
        _db = ShowDatabase()
        _db.create_schema()
        # Store on the app so other plugins can access it
        app.db = _db

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
