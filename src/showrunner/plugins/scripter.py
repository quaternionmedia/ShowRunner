"""ShowScripter - Script viewer and cue editor.

Provides a NiceGUI page at ``/script`` for viewing scripts and placing cues
onto specific lines within the script text.
"""

from html import escape as _escape

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

        # toggle for cue detail annotations
        show_details: dict = {'v': False}

        # refs to dynamic containers
        script_select_ref: dict = {'el': None}
        script_content_ref: dict = {'el': None}
        pagination_ref: dict = {'el': None}
        unpositioned_ref: dict = {'el': None}
        toolbar_ref: dict = {'el': None}

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
                        'script_char': c.script_char,
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

        def _update_cue(cue_id: int, **fields) -> None:
            """Update one or more fields on a cue."""
            with db.session() as s:
                cue = s.get(Cue, cue_id)
                if cue is None:
                    return
                for k, v in fields.items():
                    setattr(cue, k, v)
                s.add(cue)
                s.commit()

        def _delete_cue(cue_id: int) -> None:
            """Delete a cue by id."""
            with db.session() as s:
                cue = s.get(Cue, cue_id)
                if cue is not None:
                    s.delete(cue)
                    s.commit()

        # ---- render helpers -------------------------------------------------
        def render_cue_chip(c: dict) -> None:
            """Render a draggable, editable cue badge."""
            color = LAYER_COLORS.get(c['layer'], 'grey')
            label_text = f'{c["layer"][0]}{c["number"]}'
            cue_id = c['id']

            # Draggable wrapper: native HTML element with draggable attribute.
            # NiceGUI's ui.element renders as a Vue generic element where
            # props become HTML attributes on plain divs.
            wrapper = ui.element('span').classes(
                'inline-flex cursor-grab select-none mr-1'
            )
            wrapper._props['draggable'] = 'true'
            wrapper.on(
                'dragstart',
                js_handler=f'(e) => {{ e.dataTransfer.setData("text/plain", "{cue_id}"); e.dataTransfer.effectAllowed = "move"; }}',
            )

            with wrapper:
                badge = ui.badge(
                    label_text,
                    color=color,
                )
                badge.tooltip(f'{c["layer"]} {c["number"]}: {c["name"] or ""}')

            with ui.menu().props('anchor="bottom left" self="top left"') as menu:
                with ui.card().classes('p-3 gap-2').style('min-width: 260px'):
                    ui.label('Edit Cue').classes('text-subtitle2 font-bold')

                    name_input = ui.input(
                        label='Name',
                        value=c['name'] or '',
                    ).classes('w-full')

                    number_input = ui.number(
                        label='Number',
                        value=c['number'],
                        min=0,
                        format='%d',
                    ).classes('w-full')

                    layer_select = ui.select(
                        options=LAYERS,
                        value=c['layer'],
                        label='Layer',
                    ).classes('w-full')

                    with ui.row().classes('w-full justify-between items-center mt-2'):
                        ui.button(
                            icon='delete',
                            color='red',
                            on_click=lambda _, cid=c['id']: (
                                _delete_cue(cid),
                                menu.close(),
                                ui.notify('Cue deleted'),
                                refresh_all(),
                            ),
                        ).props('flat dense round').tooltip('Delete cue')

                        ui.button(
                            'Save',
                            on_click=lambda _, cid=c['id']: (
                                _update_cue(
                                    cid,
                                    name=name_input.value,
                                    number=(
                                        int(number_input.value)
                                        if number_input.value
                                        else c['number']
                                    ),
                                    layer=layer_select.value,
                                ),
                                menu.close(),
                                ui.notify('Cue updated'),
                                refresh_all(),
                            ),
                        ).props('flat dense')

            badge.on('click', menu.open)

        def render_cue_detail(c: dict) -> None:
            """Render an inline editable cue detail with a dotted connector line."""
            color = LAYER_COLORS.get(c['layer'], 'grey')
            with ui.row().classes('items-center w-full').style('flex-shrink: 0'):
                # Dotted connector line — stretches to fill gap
                ui.element('div').style(
                    f'flex: 1; min-width: 20px; border-top: 2px dotted {color}; opacity: 0.4;'
                ).classes('self-center')
                # Editable fields
                num_input = (
                    ui.number(
                        value=c['number'],
                        min=0,
                        format='%d',
                    )
                    .props('dense borderless')
                    .classes('w-16')
                    .style(f'color: {color}; font-weight: bold;')
                )
                name_input = (
                    ui.input(
                        value=c['name'] or '',
                        placeholder='name',
                    )
                    .props('dense borderless')
                    .classes('w-32')
                    .style(f'color: {color};')
                )
                # Save on blur / enter for both fields
                for inp, field in [(num_input, 'number'), (name_input, 'name')]:

                    def _save(_, cid=c['id'], f=field, el=inp):
                        val = int(el.value) if f == 'number' and el.value else el.value
                        _update_cue(cid, **{f: val})

                    inp.on('blur', _save)
                    inp.on('keydown.enter', _save)

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

            # Index positioned cues by (line, char)
            cues_by_line: dict[int, list[dict]] = {}
            for c in cues:
                if c['script_line'] is not None:
                    cues_by_line.setdefault(c['script_line'], []).append(c)
            # Sort cues within each line by char position
            for line_cues in cues_by_line.values():
                line_cues.sort(key=lambda c: c['script_char'] or 0)

            lines = content.split('\n')
            total_pages['v'] = max(1, (len(lines) + PAGE_SIZE - 1) // PAGE_SIZE)
            current_page['v'] = min(current_page['v'], total_pages['v'] - 1)

            page = current_page['v']
            start = page * PAGE_SIZE
            end = min(start + PAGE_SIZE, len(lines))

            detail_active = show_details['v']

            with container:
                # When details are active, use a 2-column CSS grid
                grid_style = (
                    'display: grid; grid-template-columns: 1fr 40%; width: 100%;'
                    if detail_active
                    else 'display: grid; grid-template-columns: 1fr; width: 100%;'
                )
                with ui.element('div').style(grid_style):
                    for i in range(start, end):
                        line = lines[i]
                        line_num = i + 1
                        line_cues = cues_by_line.get(line_num, [])

                        # -- Column 1: script line (drop target) --
                        with (
                            ui.row()
                            .classes('items-start gap-0 rounded group')
                            .style(
                                'flex-wrap: nowrap; transition: background 0.15s;'
                            ) as line_row
                        ):
                            # Drop target: dragover/dragleave for visual feedback
                            line_row.on(
                                'dragover',
                                js_handler='(e) => { e.preventDefault(); e.currentTarget.style.background = "rgba(255,255,255,0.08)"; }',
                            )
                            line_row.on(
                                'dragleave',
                                js_handler='(e) => { e.currentTarget.style.background = ""; }',
                            )
                            # Drop: JS extracts cue id + char offset, emits to Python
                            line_row.on(
                                'drop',
                                handler=lambda e, ln=line_num: _handle_drop(e, ln),
                                js_handler='''(e) => {
                                    e.preventDefault();
                                    e.currentTarget.style.background = "";
                                    const cueId = e.dataTransfer.getData("text/plain");
                                    if (!cueId) return;
                                    let charOffset = 0;
                                    const span = document.elementFromPoint(e.clientX, e.clientY);
                                    const offsetAttr = span ? (span.closest('[data-offset]') || {}).dataset : {};
                                    const segOffset = parseInt(offsetAttr.offset || '0', 10);
                                    if (document.caretPositionFromPoint) {
                                        const pos = document.caretPositionFromPoint(e.clientX, e.clientY);
                                        if (pos) charOffset = segOffset + pos.offset;
                                    } else if (document.caretRangeFromPoint) {
                                        const range = document.caretRangeFromPoint(e.clientX, e.clientY);
                                        if (range) charOffset = segOffset + range.startOffset;
                                    }
                                    emit({cue_id: cueId, char_offset: charOffset});
                                }''',
                            )

                            # Line number
                            ui.label(str(line_num)).classes(
                                'text-grey-6 text-xs w-10 text-right mr-2 mt-1 select-none'
                            )

                            # Build line content with inline cue chips at char positions
                            _render_line_with_cues(line, line_num, line_cues)

                        # -- Column 2: cue detail annotations --
                        if detail_active:
                            if line_cues:
                                with (
                                    ui.column()
                                    .classes('gap-0 w-full')
                                    .style(
                                        'border-left: 1px solid rgba(255,255,255,0.1); padding-left: 4px;'
                                    )
                                ):
                                    for c in line_cues:
                                        render_cue_detail(c)
                            else:
                                ui.element('div').style(
                                    'border-left: 1px solid rgba(255,255,255,0.1);'
                                )

            render_pagination()

        def _render_line_with_cues(
            line: str, line_num: int, line_cues: list[dict]
        ) -> None:
            """Render a script line with cue chips interleaved at character positions.

            The line text is split at each cue's ``script_char`` offset so chips
            appear inline within the text.  Clicking a text segment places a new
            cue at the exact character the user clicked.
            """

            def _make_clickable_span(text: str, ln: int, segment_offset: int) -> None:
                """Create a clickable text span that reports character offset."""
                el = ui.html(
                    f'<span style="white-space:pre-wrap; cursor:text"'
                    f' data-offset="{segment_offset}"'
                    f'>{_escape(text)}</span>'
                ).classes('font-mono text-sm')

                def _on_click(e, ln=ln, so=segment_offset):
                    char_offset = 0
                    if isinstance(e.args, dict):
                        char_offset = e.args.get('char_offset', 0)
                    add_cue(ln, so + char_offset)

                el.on(
                    'click',
                    handler=_on_click,
                    js_handler='''(e) => {
                        const span = e.target.closest('span') || e.target;
                        let offset = 0;
                        if (document.caretPositionFromPoint) {
                            const pos = document.caretPositionFromPoint(e.clientX, e.clientY);
                            if (pos) offset = pos.offset;
                        } else if (document.caretRangeFromPoint) {
                            const range = document.caretRangeFromPoint(e.clientX, e.clientY);
                            if (range) offset = range.startOffset;
                        }
                        emit({char_offset: offset});
                    }''',
                )

            if not line_cues:
                _make_clickable_span(line or '\u00a0', line_num, 0)
                return

            with (
                ui.row()
                .classes('items-center gap-0 flex-1')
                .style('flex-wrap: wrap; align-items: baseline;')
            ):
                cursor = 0
                for c in line_cues:
                    char_pos = c['script_char'] if c['script_char'] is not None else 0

                    if char_pos > cursor:
                        _make_clickable_span(line[cursor:char_pos], line_num, cursor)
                        cursor = char_pos

                    render_cue_chip(c)

                if cursor < len(line):
                    _make_clickable_span(line[cursor:], line_num, cursor)
                else:
                    ui.label('\u00a0').classes('flex-1')

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
            """Render the list of cues without a script position (drop target)."""
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
                # The whole unpositioned area is a drop target
                drop_zone = (
                    ui.column()
                    .classes(
                        'w-full gap-1 min-h-[80px] rounded border border-dashed border-gray-600 p-2'
                    )
                    .style('transition: background 0.15s;')
                )
                drop_zone.on(
                    'dragover',
                    js_handler='(e) => { e.preventDefault(); e.currentTarget.style.background = "rgba(255,255,255,0.08)"; }',
                )
                drop_zone.on(
                    'dragleave',
                    js_handler='(e) => { e.currentTarget.style.background = ""; }',
                )
                drop_zone.on(
                    'drop',
                    handler=lambda e: _handle_drop(e, None),
                    js_handler='''(e) => {
                        e.preventDefault();
                        e.currentTarget.style.background = "";
                        const cueId = e.dataTransfer.getData("text/plain");
                        if (cueId) emit({cue_id: cueId});
                    }''',
                )
                with drop_zone:
                    if not unpositioned:
                        ui.label('Drop cues here to unposition them.').classes(
                            'text-grey text-sm'
                        )
                    else:
                        for c in unpositioned:
                            with ui.row().classes('items-center w-full'):
                                render_cue_chip(c)
                                ui.label(c['name'] or '(untitled)').classes(
                                    'text-sm flex-1'
                                )

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

        def set_layer(layer: str):
            selected_layer['v'] = layer
            render_toolbar()

        def toggle_details():
            show_details['v'] = not show_details['v']
            render_toolbar()
            render_script_content()

        def render_toolbar():
            """Render the layer toolbar buttons with active state."""
            container = toolbar_ref['el']
            if container is None:
                return
            container.clear()
            with container:
                ui.label('Current Layer:').classes('text-sm font-bold')
                for layer in LAYERS:
                    color = LAYER_COLORS[layer]
                    is_active = selected_layer['v'] == layer
                    ui.button(
                        layer,
                        on_click=lambda _, ly=layer: set_layer(ly),
                        color=color,
                    ).props('dense unelevated' if is_active else 'dense outline')
                ui.separator().props('vertical')
                ui.button(
                    'Cue Details',
                    icon='visibility' if show_details['v'] else 'visibility_off',
                    on_click=toggle_details,
                    color='white' if show_details['v'] else None,
                ).props('dense unelevated' if show_details['v'] else 'dense outline')
                ui.separator().props('vertical')
                ui.button(
                    'Add Cue (no position)',
                    icon='add',
                    on_click=lambda: add_cue(None, None),
                ).props('flat dense')

        def add_cue(
            line_num: int | None = None,
            char_pos: int | None = None,
        ):
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
                    script_char=char_pos if line_num is not None else None,
                )
                s.add(cue)
                s.commit()

            pos_desc = ''
            if line_num is not None:
                pos_desc = f' at line {line_num}'
                if char_pos:
                    pos_desc += f', char {char_pos}'
            ui.notify(f'Added {layer} cue {num}{pos_desc}')
            refresh_all()

        def _handle_drop(
            e,
            line_num: int | None,
        ):
            """Handle a cue being dropped onto a script line or the unpositioned area."""
            try:
                # e.args comes from emit({cue_id: ..., char_offset: ...}) in the js_handler
                args = e.args if isinstance(e.args, dict) else {}
                cue_id_str = args.get('cue_id', '')
                if not cue_id_str:
                    return
                cue_id = int(cue_id_str)
            except (ValueError, TypeError, AttributeError):
                return

            char_pos = None
            if line_num is not None:
                char_pos = int(args.get('char_offset', 0))

            _update_cue(cue_id, script_line=line_num, script_char=char_pos)
            if line_num is not None:
                msg = f'Moved cue to line {line_num}'
                if char_pos:
                    msg += f', char {char_pos}'
                ui.notify(msg)
            else:
                ui.notify('Cue unpositioned')
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

        # Toolbar
        toolbar_ref['el'] = ui.row().classes(
            'w-full items-center gap-4 px-4 py-2 bg-gray-900 border-b border-gray-700'
        )

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
        render_toolbar()
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
