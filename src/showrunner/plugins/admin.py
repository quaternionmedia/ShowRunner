"""ShowAdmin plugin for Showrunner.

Provides a web-based admin interface for managing database models
using SQLAdmin. This plugin is optional — install the ``admin``
dependency group to enable it::

    uv sync --group admin
"""

try:
    from markupsafe import Markup
    from sqladmin import Admin, ModelView
    from wtforms import TextAreaField

    HAS_SQLADMIN = True
except ImportError:  # pragma: no cover
    HAS_SQLADMIN = False

import showrunner
from showrunner.models import Actor, Config, Cue, CueList, CueLog, Script, Show

# ---------------------------------------------------------------------------
# ModelAdmin views (only defined when sqladmin is available)
# ---------------------------------------------------------------------------

if HAS_SQLADMIN:

    class ShowAdmin(ModelView, model=Show):
        column_list = [Show.id, Show.name, Show.venue, Show.created_at]
        column_searchable_list = [Show.name, Show.venue]
        column_sortable_list = [Show.id, Show.name, Show.created_at]
        name = 'Show'
        name_plural = 'Shows'
        icon = 'fa-solid fa-masks-theater'

    class ScriptAdmin(ModelView, model=Script):
        column_list = [
            Script.id,
            Script.show_id,
            Script.title,
            Script.format,
            Script.created_at,
        ]
        column_searchable_list = [Script.title]
        column_sortable_list = [Script.id, Script.title, Script.created_at]
        form_overrides = {
            'content': TextAreaField,
        }

        form_args = {
            'content': {'render_kw': {'rows': 20}},
        }
        column_formatters_detail = {
            'content': lambda m, n: Markup(
                '<pre style="white-space:pre-wrap;max-height: 90vh;">{}</pre>'.format(
                    Markup.escape(m.content or '')
                )
            ),
        }
        name = 'Script'
        name_plural = 'Scripts'
        icon = 'fa-solid fa-scroll'

    class CueListAdmin(ModelView, model=CueList):
        column_list = [
            CueList.id,
            CueList.show_id,
            CueList.name,
            CueList.description,
            CueList.created_at,
        ]
        column_searchable_list = [CueList.name]
        column_sortable_list = [CueList.id, CueList.name, CueList.created_at]
        name = 'Cue List'
        name_plural = 'Cue Lists'
        icon = 'fa-solid fa-list-ol'

    class CueAdmin(ModelView, model=Cue):
        column_list = [
            Cue.id,
            Cue.cue_list_id,
            Cue.number,
            Cue.point,
            Cue.name,
            Cue.layer,
            Cue.cue_type,
            Cue.color,
            Cue.sequence,
        ]
        column_searchable_list = [Cue.name, Cue.layer]
        column_sortable_list = [Cue.id, Cue.number, Cue.point, Cue.layer, Cue.sequence]
        column_default_sort = [(Cue.number, False), (Cue.point, False)]
        name = 'Cue'
        name_plural = 'Cues'
        icon = 'fa-solid fa-lightbulb'

    class ActorAdmin(ModelView, model=Actor):
        column_list = [
            Actor.id,
            Actor.show_id,
            Actor.name,
            Actor.channel,
            Actor.role,
            Actor.active,
        ]
        column_searchable_list = [Actor.name, Actor.role]
        column_sortable_list = [Actor.id, Actor.name, Actor.channel]
        name = 'Actor'
        name_plural = 'Actors'
        icon = 'fa-solid fa-user'

    class CueLogAdmin(ModelView, model=CueLog):
        column_list = [
            CueLog.id,
            CueLog.show_id,
            CueLog.cue_id,
            CueLog.triggered_at,
            CueLog.duration_ms,
            CueLog.notes,
        ]
        column_sortable_list = [CueLog.id, CueLog.triggered_at]
        column_default_sort = (CueLog.triggered_at, True)
        name = 'Cue Log'
        name_plural = 'Cue Logs'
        icon = 'fa-solid fa-clock-rotate-left'

    class ConfigAdmin(ModelView, model=Config):
        column_list = [Config.id, Config.show_id, Config.key, Config.value]
        column_searchable_list = [Config.key]
        column_sortable_list = [Config.id, Config.key]
        name = 'Config'
        name_plural = 'Config'
        icon = 'fa-solid fa-gear'

    _MODEL_VIEWS = [
        ShowAdmin,
        ScriptAdmin,
        CueListAdmin,
        CueAdmin,
        ActorAdmin,
        CueLogAdmin,
        ConfigAdmin,
    ]


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------


class ShowAdminPlugin:
    """Web-based admin interface for ShowRunner database models.

    Requires the ``admin`` dependency group (``sqladmin``).
    When sqladmin is not installed the plugin registers but does nothing.
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            'name': 'ShowAdmin',
            'description': 'SQLAdmin web interface for database models',
            'version': '0.1.0',
        }

    @showrunner.hookimpl(trylast=True)
    def showrunner_startup(self, app):
        if not HAS_SQLADMIN:
            return

        db = getattr(app, 'db', None)
        if db is None:
            return

        admin = Admin(app.api, db.engine, title='ShowRunner Admin')
        for view in _MODEL_VIEWS:
            admin.add_view(view)
        self._admin = admin

    @showrunner.hookimpl
    def showrunner_shutdown(self, app):
        pass

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        return None

    @showrunner.hookimpl
    def showrunner_get_commands(self):
        return []

    @showrunner.hookimpl
    def showrunner_get_nav(self):
        return None

    @showrunner.hookimpl
    def showrunner_get_status(self):
        return None
