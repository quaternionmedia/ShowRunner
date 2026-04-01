"""ShowScripter - Script viewer and cue editor.

Provides a NiceGUI page at ``/script`` for viewing scripts and placing cues
onto specific lines within the script text.
"""

from nicegui import ui
from sqlmodel import select

import showrunner
from showrunner.database import ShowDatabase
from showrunner.models import Cue, CueList, Script, Show

LAYERS = ['Lights', 'Sound', 'Video', 'Audio', 'Stage']

LAYER_COLORS = {
    'Lights': 'orange',
    'Sound': 'blue',
    'Video': 'purple',
    'Audio': 'cyan',
    'Stage': 'green',
}

PAGE_SIZE = 100


def _build_page(db: ShowDatabase) -> None:
    """Register the /script NiceGUI page."""

    @ui.page('/script')
    def script_page():
        ui.dark_mode(True)

        # ---- state ----------------------------------------------------------
        with db.session() as s:
            shows = s.exec(select(Show).order_by(Show.name)).all()
            show_options = {sh.id: str(sh) for sh in shows}

        selected_show_id: dict = {'v': next(iter(show_options), None)}
        selected_script_id: dict = {'v': None}
        selected_layer: dict = {'v': LAYERS[0]}

        # pagination
        current_page: dict = {'v': 0}
        total_pages: dict = {'v': 1}

        # refs to dynamic containers
        script_select_ref: dict = {'el': None}
        script_content_ref: dict = {'el': None}
        pagination_ref: dict = {'el': None}
        unpositioned_ref: dict = {'el': None}

        # ---- helpers --------------------------------------------------------
        def _load_scripts():
            """Reload the script selector for the current show."""
            show_id = selected_show_id['v']
            if show_id is None:
                return {}
            with db.session() as s:
                scripts = s.exec(
                    select(Script)
                    .where(Script.show_id == show_id)
                    .order_by(Script.title)
                ).all()
                return {sc.id: str(sc) for sc in scripts}

        def _load_cues(cue_list_id: int | None) -> list[dict]:
            if cue_list_id is None:
                return []
            with db.session() as s:
                cues = s.exec(
                    select(Cue)
                    .where(Cue.cue_list_id == cue_list_id)
                    .order_by(Cue.number, Cue.point)
                ).all()
                return [
                    {
                        'id': c.id,
                        'number': c.number,
                        'point': c.point,
                        'name': c.name,
                        'layer': c.layer,
                        'script_line': c.script_line,
                    }
                    for c in cues
                ]

        def _get_or_create_cuelist(show_id: int) -> int:
            """Return the id of the first cue list for the show, creating one if needed."""
            with db.session() as s:
                cl = s.exec(select(CueList).where(CueList.show_id == show_id)).first()
                if cl:
                    return cl.id
                cl = CueList(show_id=show_id, name='Main')
                s.add(cl)
                s.commit()
                s.refresh(cl)
                return cl.id

        def _next_cue_number(cue_list_id: int) -> int:
            with db.session() as s:
                cues = s.exec(select(Cue).where(Cue.cue_list_id == cue_list_id)).all()
                if not cues:
                    return 1
                return max(c.number for c in cues) + 1

        # ---- render helpers -------------------------------------------------
        def render_script_content():
            """Render the current page of script lines with inline cue markers."""
            container = script_content_ref['el']
            if container is None:
                return
            container.clear()

            sid = selected_script_id['v']
            if sid is None:
                with container:
                    ui.label('Select a script to view.').classes('text-grey')
                render_pagination()
                return

            with db.session() as s:
                script = s.get(Script, sid)
                content = script.content if script else None

            if not content:
                with container:
                    ui.label('This script has no content.').classes('text-grey')
                render_pagination()
                return

            show_id = selected_show_id['v']
            cl_id = _get_or_create_cuelist(show_id)
            cues = _load_cues(cl_id)

            # Index positioned cues by line number
            cues_by_line: dict[int, list[dict]] = {}
            for c in cues:
                if c['script_line'] is not None:
                    cues_by_line.setdefault(c['script_line'], []).append(c)

            lines = content.split('\n')
            total_pages['v'] = max(1, (len(lines) + PAGE_SIZE - 1) // PAGE_SIZE)
            current_page['v'] = min(current_page['v'], total_pages['v'] - 1)

            page = current_page['v']
            start = page * PAGE_SIZE
            end = min(start + PAGE_SIZE, len(lines))

            with container:
                for i in range(start, end):
                    line = lines[i]
                    line_num = i + 1
                    with ui.row().classes(
                        'w-full items-start gap-0 hover:bg-gray-800 rounded group'
                    ):
                        ui.label(str(line_num)).classes(
                            'text-grey-6 text-xs w-10 text-right mr-2 mt-1 select-none'
                        )

                        # Cue markers for this line
                        if line_num in cues_by_line:
                            for c in cues_by_line[line_num]:
                                color = LAYER_COLORS.get(c['layer'], 'grey')
                                ui.badge(
                                    f'{c["layer"][0]}{c["number"]}',
                                    color=color,
                                ).classes('mr-1').tooltip(
                                    f'{c["layer"]} {c["number"]}: {c["name"] or ""}'
                                )

                        ui.label(line or '\u00a0').classes(
                            'font-mono text-sm whitespace-pre-wrap flex-1'
                        )

                        # "Add cue here" button, visible on hover
                        ui.button(
                            icon='add',
                            on_click=lambda _, ln=line_num: add_cue(ln),
                        ).props('flat dense round size=xs').classes(
                            'opacity-0 group-hover:opacity-100 ml-1'
                        ).tooltip(
                            'Add cue at this line'
                        )

            render_pagination()

        def render_pagination():
            """Render page navigation controls."""
            container = pagination_ref['el']
            if container is None:
                return
            container.clear()

            total = total_pages['v']
            page = current_page['v']

            with container:
                with ui.row().classes('items-center gap-2'):
                    ui.button(
                        icon='first_page',
                        on_click=lambda: go_to_page(0),
                    ).props('flat dense round').bind_enabled_from(
                        target_object=current_page,
                        target_name='v',
                        backward=lambda v: v > 0,
                    )
                    ui.button(
                        icon='chevron_left',
                        on_click=lambda: go_to_page(page - 1),
                    ).props('flat dense round').bind_enabled_from(
                        target_object=current_page,
                        target_name='v',
                        backward=lambda v: v > 0,
                    )
                    ui.label(f'Page {page + 1} / {total}').classes('text-sm mx-2')
                    ui.button(
                        icon='chevron_right',
                        on_click=lambda: go_to_page(page + 1),
                    ).props('flat dense round').bind_enabled_from(
                        target_object=current_page,
                        target_name='v',
                        backward=lambda v: v < total_pages['v'] - 1,
                    )
                    ui.button(
                        icon='last_page',
                        on_click=lambda: go_to_page(total - 1),
                    ).props('flat dense round').bind_enabled_from(
                        target_object=current_page,
                        target_name='v',
                        backward=lambda v: v < total_pages['v'] - 1,
                    )
                    ui.label('Go to line:').classes('text-sm ml-4')
                    ui.number(
                        value=page * PAGE_SIZE + 1,
                        min=1,
                        format='%d',
                        on_change=lambda e: (
                            go_to_line(int(e.value)) if e.value else None
                        ),
                    ).props('dense outlined').classes('w-24')

        def render_unpositioned():
            """Render the list of cues without a script position."""
            container = unpositioned_ref['el']
            if container is None:
                return
            container.clear()

            show_id = selected_show_id['v']
            if show_id is None:
                return

            cl_id = _get_or_create_cuelist(show_id)
            cues = _load_cues(cl_id)
            unpositioned = [c for c in cues if c['script_line'] is None]

            with container:
                if not unpositioned:
                    ui.label('No unpositioned cues.').classes('text-grey text-sm')
                    return
                for c in unpositioned:
                    color = LAYER_COLORS.get(c['layer'], 'grey')
                    with ui.row().classes('items-center w-full'):
                        ui.badge(
                            f'{c["layer"][0]}{c["number"]}',
                            color=color,
                        )
                        ui.label(c['name'] or '(untitled)').classes('text-sm flex-1')

        def refresh_all():
            render_script_content()
            render_unpositioned()

        def go_to_page(page: int):
            page = max(0, min(page, total_pages['v'] - 1))
            current_page['v'] = page
            render_script_content()

        def go_to_line(line: int):
            page = max(0, (line - 1) // PAGE_SIZE)
            go_to_page(page)

        # ---- actions --------------------------------------------------------
        def on_show_change(e):
            selected_show_id['v'] = e.value
            selected_script_id['v'] = None
            current_page['v'] = 0
            # Rebuild script selector
            opts = _load_scripts()
            sel = script_select_ref['el']
            if sel:
                sel.options = opts
                sel.value = next(iter(opts), None)
                sel.update()
            on_script_change_value(next(iter(opts), None))

        def on_script_change(e):
            on_script_change_value(e.value)

        def on_script_change_value(val):
            selected_script_id['v'] = val
            current_page['v'] = 0
            refresh_all()

        def on_layer_change(e):
            selected_layer['v'] = e.value

        def add_cue(line_num: int | None = None):
            show_id = selected_show_id['v']
            if show_id is None:
                ui.notify('Select a show first.', type='warning')
                return
            cl_id = _get_or_create_cuelist(show_id)
            num = _next_cue_number(cl_id)
            layer = selected_layer['v']

            with db.session() as s:
                cue = Cue(
                    cue_list_id=cl_id,
                    number=num,
                    name='',
                    layer=layer,
                    script_line=line_num,
                )
                s.add(cue)
                s.commit()

            ui.notify(
                f'Added {layer} cue {num}'
                + (f' at line {line_num}' if line_num else '')
            )
            refresh_all()

        # ---- layout ---------------------------------------------------------
        with ui.header().classes('items-center justify-between'):
            ui.label('ShowScripter').classes('text-h5 font-bold')
            with ui.row().classes('items-center gap-4'):
                ui.select(
                    options=show_options,
                    value=selected_show_id['v'],
                    label='Show',
                    on_change=on_show_change,
                ).classes('w-48')

                initial_scripts = _load_scripts()
                sel = ui.select(
                    options=initial_scripts,
                    value=next(iter(initial_scripts), None),
                    label='Script',
                    on_change=on_script_change,
                ).classes('w-48')
                script_select_ref['el'] = sel
                if initial_scripts:
                    selected_script_id['v'] = next(iter(initial_scripts))

                ui.select(
                    options=LAYERS,
                    value=selected_layer['v'],
                    label='Cue Layer',
                    on_change=on_layer_change,
                ).classes('w-36')

                ui.button(
                    'Add Cue (no position)', on_click=lambda: add_cue(None)
                ).props('flat')

        with ui.splitter(value=75).classes('w-full h-full') as splitter:
            with splitter.before:
                with ui.scroll_area().classes('w-full h-[calc(100vh-80px)]'):
                    script_content_ref['el'] = ui.column().classes('w-full p-4')
                    pagination_ref['el'] = ui.row().classes('w-full justify-center p-2')

            with splitter.after:
                with ui.scroll_area().classes('w-full h-[calc(100vh-80px)]'):
                    with ui.column().classes('w-full p-4'):
                        ui.label('Unpositioned Cues').classes('text-h6 mb-2')
                        unpositioned_ref['el'] = ui.column().classes('w-full gap-1')

        # Initial render
        refresh_all()


class ShowScripterPlugin:
    """Script viewer (PDF, Fountain, etc.) and OCR parser.

    Converts scripts into a structured format for cue management.
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            'name': 'ShowScripter',
            'description': 'Script viewer and cue editor',
            'version': '0.1.0',
        }

    @showrunner.hookimpl(trylast=True)
    def showrunner_startup(self, app):
        db = getattr(app, 'db', None)
        if db is None:
            return
        _build_page(db)

    @showrunner.hookimpl
    def showrunner_shutdown(self, app):
        pass

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        return None

    @showrunner.hookimpl
    def showrunner_get_commands(self):
        return []
