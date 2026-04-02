"""SQLModel models for the ShowRunner database schema.

Defines the core data models for shows, scripts, cue lists, cues, actors,
performance logs, and configuration. All models use SQLModel for ORM operations
with SQLite by default.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


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


class Cue(SQLModel, table=True):
    """A single cue within a cue list.

    Cues are ordered by (number, point) and belong to a specific layer
    (Lights, Sound, Video, Audio, Stage).
    """

    __tablename__ = 'cues'

    id: int | None = Field(default=None, primary_key=True)
    cue_list_id: int = Field(foreign_key='cue_lists.id')
    number: int = Field(default=0)
    point: int = Field(default=0)
    name: str | None = None
    layer: str | None = None  # Lights, Sound, Video, Audio, Stage
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

    def __str__(self) -> str:
        label = f'{self.number}'
        if self.point:
            label += f'.{self.point}'
        if self.name:
            label += f' {self.name}'
        return label


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
