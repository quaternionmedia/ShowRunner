"""SQLModel models for the ShowRunner database schema.

Defines the core data models for shows, scripts, cue lists, cues, actors,
performance logs, and configuration. All models use SQLModel for ORM operations
with SQLite by default.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Integer, UniqueConstraint, cast, event, inspect, select
from sqlalchemy.orm import Session
from sqlmodel import Field, Relationship, SQLModel

from showrunner.scriptformat import map_script_lines


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Show(SQLModel, table=True):
    """A production / show — the top-level container for all data."""

    __tablename__ = 'shows'

    id: int | None = Field(default=None, primary_key=True)
    name: str
    venue: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def __str__(self) -> str:
        parts = [self.name or '']
        if self.venue:
            parts.append(f'@ {self.venue}')
        return ' '.join(parts)

    # Relationships
    scripts: list['Script'] = Relationship(back_populates='show')
    cue_lists: list['CueList'] = Relationship(back_populates='show')
    actors: list['Actor'] = Relationship(back_populates='show')
    configs: list['Config'] = Relationship(back_populates='show')
    layers: list['Layer'] = Relationship(back_populates='show')
    cue_logs: list['CueLog'] = Relationship(back_populates='show')


class Script(SQLModel, table=True):
    """A parsed script associated with a show."""

    __tablename__ = 'scripts'

    id: int | None = Field(default=None, primary_key=True)
    show_id: int = Field(foreign_key='shows.id')
    title: str
    format: str = 'fountain'  # fountain, pdf, text
    content: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)

    show: Optional[Show] = Relationship(back_populates='scripts')

    def __str__(self) -> str:
        return self.title or f'Script {self.id}'


class CueList(SQLModel, table=True):
    """A named cue list within a show (e.g. 'Main', 'Rehearsal')."""

    __tablename__ = 'cue_lists'

    id: int | None = Field(default=None, primary_key=True)
    show_id: int = Field(foreign_key='shows.id')
    name: str = 'Main'
    description: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)

    show: Optional[Show] = Relationship(back_populates='cue_lists')
    cues: list['Cue'] = Relationship(back_populates='cue_list')

    def __str__(self) -> str:
        return self.name or f'CueList {self.id}'


class Layer(SQLModel, table=True):
    """A named cue layer within a show (e.g. 'Lights'), with a backend type.

    ``type`` is validated against the layer type registry in ``showrunner.layers``.
    ``Cue.layer`` stores the layer name, not an id.
    """

    __tablename__ = 'layers'
    __table_args__ = (UniqueConstraint('show_id', 'name'),)

    id: int | None = Field(default=None, primary_key=True)
    show_id: int = Field(foreign_key='shows.id')
    name: str
    color: str = 'grey'  # Quasar colour name
    type: str = 'generic'
    sequence: int = Field(default=0)

    show: Optional[Show] = Relationship(back_populates='layers')


class Cue(SQLModel, table=True):
    """A single cue within a cue list.

    Cues are ordered by (number, point) and belong to a specific layer
    (see the ``layers`` table).
    """

    __tablename__ = 'cues'

    id: int | None = Field(default=None, primary_key=True)
    cue_list_id: int = Field(foreign_key='cue_lists.id')
    number: str | None = None  # main cue number (e.g. "q42")
    point: int = Field(default=0)
    name: str | None = None
    layer: str | None = None  # layer name; see the layers table
    cue_type: str | None = None  # Network, MIDI, Audio, Video, etc.
    notes: str | None = None
    color: str | None = None
    sequence: int = Field(default=0)  # explicit ordering within the list
    script_line: int | None = None  # line number in the script (None = unpositioned)
    script_char: int | None = (
        None  # character offset within the line (None = start of line)
    )
    created_at: datetime = Field(default_factory=_utcnow)

    cue_list: Optional[CueList] = Relationship(back_populates='cues')
    cue_logs: list['CueLog'] = Relationship(back_populates='cue')

    @property
    def number_label(self) -> str:
        """Cue number with point (e.g. ``5.1``); empty string when unnumbered."""
        label = '' if self.number is None else str(self.number)
        return f'{label}.{self.point}' if self.point else label

    def __str__(self) -> str:
        return ' '.join(filter(None, [self.number_label, self.name]))


class Actor(SQLModel, table=True):
    """An actor / performer in a show with optional mixer channel assignment."""

    __tablename__ = 'actors'

    id: int | None = Field(default=None, primary_key=True)
    show_id: int = Field(foreign_key='shows.id')
    name: str
    channel: int | None = None
    role: str | None = None
    active: bool = Field(default=True)

    show: Optional[Show] = Relationship(back_populates='actors')

    def __str__(self) -> str:
        label = self.name or f'Actor {self.id}'
        if self.role:
            label += f' ({self.role})'
        return label


class CueLog(SQLModel, table=True):
    """A timestamped log entry recorded when a cue fires during a performance."""

    __tablename__ = 'cue_logs'

    id: int | None = Field(default=None, primary_key=True)
    show_id: int = Field(foreign_key='shows.id')
    cue_id: int | None = Field(default=None, foreign_key='cues.id')
    triggered_at: datetime = Field(default_factory=_utcnow)
    duration_ms: int | None = None
    notes: str | None = None

    show: Optional[Show] = Relationship(back_populates='cue_logs')
    cue: Optional[Cue] = Relationship(back_populates='cue_logs')

    def __str__(self) -> str:
        return f'Log {self.id} @ {self.triggered_at}'


class Config(SQLModel, table=True):
    """Key-value configuration scoped to a show."""

    __tablename__ = 'config'

    id: int | None = Field(default=None, primary_key=True)
    show_id: int = Field(foreign_key='shows.id')
    key: str
    value: str | None = None

    show: Optional[Show] = Relationship(back_populates='configs')

    def __str__(self) -> str:
        return f'Config {self.key}={self.value}'


# Sort cues numerically (10 after 2); unnumbered first.
# ponytail: non-numeric numbers (e.g. "q42") cast to 0 and tie-break as text.
CUE_ORDER = (cast(Cue.number, Integer), Cue.number, Cue.point)


@event.listens_for(Session, 'before_flush')
def _remap_cues_on_script_edit(session, flush_context, instances) -> None:
    """When a script's text changes, move every positioned cue of its show with the text."""
    for obj in list(session.dirty):
        if not isinstance(obj, Script) or not inspect(obj).attrs.content.history.has_changes():
            continue
        old = session.execute(select(Script.content).where(Script.id == obj.id)).scalar()
        new = obj.content or ''
        if not old or old == new:
            continue
        mapping = map_script_lines(old, new)
        new_lines = new.split('\n')
        cues = session.execute(
            select(Cue)
            .join(CueList, Cue.cue_list_id == CueList.id)
            .where(CueList.show_id == obj.show_id, Cue.script_line.is_not(None))
        ).scalars()
        for cue in cues:
            line = min(max(cue.script_line, 0), len(mapping) - 1)
            cue.script_line = mapping[line]
            if cue.script_char is not None:
                cue.script_char = min(cue.script_char, len(new_lines[cue.script_line]))
