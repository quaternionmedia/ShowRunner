"""ShowScripter - Script viewer and cue editor.

Provides a NiceGUI page at ``/script`` for viewing scripts and placing cues
onto specific lines within the script text.
"""

import re
from collections import deque
from html import escape as _escape
from typing import Callable

from nicegui import app as nicegui_app, ui
from sqlmodel import select

import showrunner
from showrunner.plugins.db import get_db
from showrunner.models import Cue, CueList, Script, Show
from showrunner.ui import _current_script_id, _current_show_id, header

LAYERS = ['Lights', 'Sound', 'Video', 'Audio', 'Stage']

LAYER_COLORS = {
    'Lights': 'orange',
    'Sound': 'blue',
    'Video': 'purple',
    'Audio': 'cyan',
    'Stage': 'green',
}

PAGE_SIZE = 100
PAGE_BREAK_RE = re.compile(r'\[\[Page\s+(.+?)\]\]')
DEFAULT_UNDO_LEVELS = 50


def _parse_pages(lines: list[str]) -> list[dict]:
    """Split *lines* into pages delimited by ``[[Page N]]`` markers.

    Returns a list of dicts: ``{'label': str | None, 'lines': [(index, text), ...]}``.
    Line indices are 0-based positions in the original *lines* list so that
    line numbering stays consistent regardless of pagination.

    Markers may appear inline (e.g. ``some text [[Page 5]] more text``).
    Text before the marker stays on the previous page; text after starts the
    new page.  The marker itself is stripped from the displayed content.

    Falls back to fixed ``PAGE_SIZE`` chunks when no markers are found.
    """
    pages: list[dict] = []
    current_label: str | None = None
    current_lines: list[tuple[int, str]] = []
    found_any_marker = False

    for i, line in enumerate(lines):
        m = PAGE_BREAK_RE.search(line)
        if m:
            found_any_marker = True
            before = line[: m.start()].rstrip()
            after = line[m.end() :].lstrip()

            # Text before the marker belongs to the current (previous) page
            if before:
                current_lines.append((i, before))

            # Flush accumulated lines as previous page
            if current_lines or current_label is not None:
                pages.append({'label': current_label, 'lines': current_lines})

            current_label = m.group(1)
            current_lines = []

            # Text after the marker starts the new page
            if after:
                current_lines.append((i, after))
        else:
            current_lines.append((i, line))

    # Flush last page
    if current_lines or current_label is not None:
        pages.append({'label': current_label, 'lines': current_lines})

    # Fallback: no markers found → chunk by PAGE_SIZE
    if not found_any_marker:
        pages = []
        for start in range(0, len(lines), PAGE_SIZE):
            chunk = [
                (i, lines[i]) for i in range(start, min(start + PAGE_SIZE, len(lines)))
            ]
            pages.append({'label': None, 'lines': chunk})

    if not pages:
        pages = [{'label': None, 'lines': []}]

    return pages


def _build_page(undo_levels: int = DEFAULT_UNDO_LEVELS) -> None:
    """Register the /script NiceGUI page."""

    @ui.page('/script')
    def script_page():
        ui.dark_mode(True)
        header()

        # ---- state ----------------------------------------------------------
        selected_show_id: dict = {'v': _current_show_id()}
        selected_script_id: dict = {'v': None}
        selected_layer: dict = {'v': LAYERS[0]}

        # pagination
        current_page: dict = {'v': 0}
        total_pages: dict = {'v': 1}
        parsed_pages: dict = {'v': []}

        # undo
        undo_stack: deque[dict] = deque(maxlen=undo_levels)

        # toggle for cue detail annotations
        show_details: dict = {'v': False}

        # refs to dynamic containers
        script_select_ref: dict = {'el': None}
        script_content_ref: dict = {'el': None}
        pagination_top_ref: dict = {'el': None}
        pagination_ref: dict = {'el': None}
        unpositioned_ref: dict = {'el': None}
        toolbar_ref: dict = {'el': None}

        # ---- helpers --------------------------------------------------------
        def _load_scripts():
            """Reload the script selector for the current show."""
            show_id = selected_show_id['v']
            if show_id is None:
                return {}
            with get_db().session() as s:
                scripts = s.exec(
                    select(Script)
                    .where(Script.show_id == show_id)
                    .order_by(Script.title)
                ).all()
                return {sc.id: str(sc) for sc in scripts}

        def _load_cues(cue_list_id: int | None) -> list[dict]:
            if cue_list_id is None:
                return []
            with get_db().session() as s:
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
            with get_db().session() as s:
                cl = s.exec(select(CueList).where(CueList.show_id == show_id)).first()
                if cl:
                    return cl.id
                cl = CueList(show_id=show_id, name='Main')
                s.add(cl)
                s.commit()
                s.refresh(cl)
                return cl.id

        def _next_cue_number(cue_list_id: int) -> int:
            with get_db().session() as s:
                cues = s.exec(select(Cue).where(Cue.cue_list_id == cue_list_id)).all()
                if not cues:
                    return 1
                return max(c.number for c in cues) + 1

        def _push_undo(description: str, reverse_fn: Callable[[], None]) -> None:
            """Push an undo entry onto the stack."""
            undo_stack.append({'description': description, 'reverse_fn': reverse_fn})

        def _perform_undo(index: int) -> None:
            """Execute and remove a single undo entry by index."""
            if 0 <= index < len(undo_stack):
                entry = undo_stack[index]
                del undo_stack[index]
                entry['reverse_fn']()
                ui.notify(f'Undone: {entry["description"]}')
                refresh_all()

        def _snapshot_cue(cue_id: int) -> dict | None:
            """Return a dict of all restorable fields for a cue, or None."""
            with get_db().session() as s:
                cue = s.get(Cue, cue_id)
                if cue is None:
                    return None
                return {
                    'cue_list_id': cue.cue_list_id,
                    'number': cue.number,
                    'point': cue.point,
                    'name': cue.name,
                    'layer': cue.layer,
                    'cue_type': cue.cue_type,
                    'notes': cue.notes,
                    'color': cue.color,
                    'sequence': cue.sequence,
                    'script_line': cue.script_line,
                    'script_char': cue.script_char,
                }

        def _update_cue(cue_id: int, record_undo: bool = True, **fields) -> None:
            """Update one or more fields on a cue."""
            if record_undo:
                old = _snapshot_cue(cue_id)
                if old is not None:
                    changed = {k: old[k] for k in fields if k in old}
                    desc = f'Edit cue {old["layer"] or ""} {old["number"]}'
                    _push_undo(
                        desc,
                        lambda cid=cue_id, prev=changed: _update_cue(
                            cid, False, **prev
                        ),
                    )

            with get_db().session() as s:
                cue = s.get(Cue, cue_id)
                if cue is None:
                    return
                for k, v in fields.items():
                    setattr(cue, k, v)
                s.add(cue)
                s.commit()

        def _delete_cue(cue_id: int, record_undo: bool = True) -> None:
            """Delete a cue by id."""
            if record_undo:
                snap = _snapshot_cue(cue_id)
                if snap is not None:
                    desc = f'Delete cue {snap["layer"] or ""} {snap["number"]}'
                    _push_undo(
                        desc,
                        lambda s=snap: _recreate_cue(s),
                    )

            with get_db().session() as s:
                cue = s.get(Cue, cue_id)
                if cue is not None:
                    s.delete(cue)
                    s.commit()

        def _recreate_cue(snap: dict) -> None:
            """Re-create a cue from a snapshot dict (used by undo of delete)."""
            with get_db().session() as s:
                cue = Cue(**snap)
                s.add(cue)
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
                        _update_cue(cid, True, **{f: val})

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

            with get_db().session() as s:
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
            pages = _parse_pages(lines)
            parsed_pages['v'] = pages
            total_pages['v'] = max(1, len(pages))
            current_page['v'] = min(current_page['v'], total_pages['v'] - 1)

            page = current_page['v']
            page_lines = pages[page]['lines']

            detail_active = show_details['v']

            with container:
                # When details are active, use a 2-column CSS grid
                grid_style = (
                    'display: grid; grid-template-columns: 1fr 40%; width: 100%;'
                    if detail_active
                    else 'display: grid; grid-template-columns: 1fr; width: 100%;'
                )
                with ui.element('div').style(grid_style):
                    for line_idx, line in page_lines:
                        line_num = line_idx + 1
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
                # Force the NiceGUI wrapper to render as <span> instead of <div>
                el._props['tag'] = 'span'

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

            # Use an inline-flow container so text and chips don't create
            # block-level breaks.  Flex containers blockify their children
            # which causes unwanted line breaks for wrapped text.
            with (
                ui.element('div')
                .style('display: inline; line-height: 1.5;')
                .classes('flex-1')
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
                    ui.html('&nbsp;')

        def _render_pagination_into(container) -> None:
            """Render page navigation controls into *container*."""
            total = total_pages['v']
            page = current_page['v']

            with container:
                with ui.row().classes('items-center gap-2'):
                    # Resolve current page label as int for label-aware navigation
                    current_label_int: int | None = None
                    if parsed_pages['v'] and parsed_pages['v'][page].get('label'):
                        try:
                            current_label_int = int(parsed_pages['v'][page]['label'])
                        except (ValueError, TypeError):
                            pass

                    ui.button(
                        icon='first_page',
                        on_click=lambda: go_to_page(0),
                    ).props('flat dense round').bind_enabled_from(
                        target_object=current_page,
                        target_name='v',
                        backward=lambda v: v > 0,
                    )

                    def _prev_page():
                        if current_label_int is not None:
                            _go_to_page_label(current_label_int - 1)
                        else:
                            go_to_page(page - 1)

                    ui.button(
                        icon='chevron_left',
                        on_click=_prev_page,
                    ).props('flat dense round').bind_enabled_from(
                        target_object=current_page,
                        target_name='v',
                        backward=lambda v: v > 0,
                    )
                    page_label = ''
                    if current_label_int is not None:
                        page_label = f'Page {current_label_int}'
                    else:
                        page_label = f'Page {page + 1}'
                    ui.label(f'{page_label}  ({page + 1} / {total})').classes(
                        'text-sm mx-2'
                    )

                    def _next_page():
                        if current_label_int is not None:
                            _go_to_page_label(current_label_int + 1)
                        else:
                            go_to_page(page + 1)

                    ui.button(
                        icon='chevron_right',
                        on_click=_next_page,
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
                    ui.label('Go to page:').classes('text-sm ml-4')
                    # Show the page label (e.g. "42" from [[Page 42]]) if available
                    current_label = (
                        parsed_pages['v'][page].get('label')
                        if parsed_pages['v']
                        else None
                    )
                    goto_input = (
                        ui.number(
                            value=int(current_label) if current_label else page + 1,
                            min=1,
                            format='%d',
                        )
                        .props('dense outlined')
                        .classes('w-24')
                    )

                    def _goto_commit(_, inp=goto_input):
                        if inp.value:
                            _go_to_page_label(inp.value)

                    goto_input.on('blur', _goto_commit)
                    goto_input.on('keydown.enter', _goto_commit)

        def render_pagination():
            """Render all pagination bars."""
            for ref in (pagination_top_ref, pagination_ref):
                container = ref['el']
                if container is None:
                    continue
                container.clear()
                _render_pagination_into(container)

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
            render_toolbar()
            render_script_content()
            render_unpositioned()

        def go_to_page(page: int):
            page = max(0, min(page, total_pages['v'] - 1))
            current_page['v'] = page
            render_script_content()

        def go_to_line(line: int):
            """Navigate to whichever page contains *line* (1-based)."""
            for idx, pg in enumerate(parsed_pages['v']):
                for line_idx, _ in pg['lines']:
                    if line_idx + 1 >= line:
                        go_to_page(idx)
                        return
            go_to_page(total_pages['v'] - 1)

        def _go_to_page_label(value):
            """Jump to a page by label, finding the nearest match if exact is missing.

            When the requested label doesn't exist, navigate to the nearest page
            whose numeric label is closest to *value*.  Direction is determined by
            comparing the target to the current page's label: if the target is
            higher, pick the next higher existing page; if lower, pick the next
            lower one.  Never wraps past the first or last page.
            """
            target = int(value)
            pages = parsed_pages['v']

            # Build a sorted list of (numeric_label, page_index) for labeled pages
            labeled: list[tuple[int, int]] = []
            for idx, pg in enumerate(pages):
                lbl = pg.get('label')
                if lbl is not None:
                    try:
                        labeled.append((int(lbl), idx))
                    except (ValueError, TypeError):
                        pass

            if not labeled:
                # No labeled pages — fall back to 1-based page index
                go_to_page(target - 1)
                return

            labeled.sort(key=lambda t: t[0])

            # Exact match
            for lbl, idx in labeled:
                if lbl == target:
                    go_to_page(idx)
                    return

            # Determine direction from the current page's label
            cur_label_val = None
            cur_pg = pages[current_page['v']] if pages else None
            if cur_pg and cur_pg.get('label') is not None:
                try:
                    cur_label_val = int(cur_pg['label'])
                except (ValueError, TypeError):
                    pass

            if cur_label_val is not None and target < cur_label_val:
                # Going backward — find the nearest label ≤ target
                best = None
                for lbl, idx in labeled:
                    if lbl <= target:
                        best = idx
                    else:
                        break
                if best is None:
                    best = labeled[0][1]  # clamp to first
                go_to_page(best)
            else:
                # Going forward — find the nearest label ≥ target
                best = None
                for lbl, idx in labeled:
                    if lbl >= target:
                        best = idx
                        break
                if best is None:
                    best = labeled[-1][1]  # clamp to last
                go_to_page(best)

        # ---- actions --------------------------------------------------------
        def on_show_change(e):
            selected_show_id['v'] = e.value
            nicegui_app.storage.general['current_show'] = e.value
            nicegui_app.storage.general['current_script'] = None
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
            nicegui_app.storage.general['current_script'] = e.value
            ui.navigate.reload()

        def on_script_change_value(val):
            selected_script_id['v'] = val
            nicegui_app.storage.general['current_script'] = val
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
                ui.separator().props('vertical')
                _render_undo_dropdown()

        def _render_undo_dropdown() -> None:
            """Render undo button with dropdown listing individual undo entries."""
            has_items = len(undo_stack) > 0
            with ui.dropdown_button(
                'Undo',
                icon='undo',
                split=bool(has_items),
                on_click=(
                    (lambda: _perform_undo(len(undo_stack) - 1)) if has_items else None
                ),
            ).props('flat dense' + (' disable' if not has_items else '')):
                if has_items:
                    for idx in range(len(undo_stack) - 1, -1, -1):
                        entry = undo_stack[idx]
                        ui.item(
                            entry['description'],
                            on_click=lambda _, i=idx: (_perform_undo(i),),
                        )

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

            with get_db().session() as s:
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
                s.refresh(cue)
                assert cue.id is not None
                new_id = cue.id

            desc = f'Add {layer} cue {num}'
            _push_undo(desc, lambda cid=new_id: _delete_cue(cid, False))

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
        with ui.row().classes(
            'w-full items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-700'
        ):
            ui.label('ShowScripter').classes('text-h5 font-bold')
            with ui.row().classes('items-center gap-4'):
                initial_scripts = _load_scripts()
                stored_script = _current_script_id()
                # Use stored script if it belongs to this show's scripts
                if stored_script in initial_scripts:
                    initial_value = stored_script
                else:
                    initial_value = next(iter(initial_scripts), None)
                sel = ui.select(
                    options=initial_scripts,
                    value=initial_value,
                    label='Script',
                    on_change=on_script_change,
                ).classes('w-48')
                script_select_ref['el'] = sel
                if initial_value is not None:
                    selected_script_id['v'] = initial_value
                    nicegui_app.storage.general['current_script'] = initial_value

        # Toolbar
        toolbar_ref['el'] = ui.row().classes(
            'w-full items-center gap-4 px-4 py-2 bg-gray-900 border-b border-gray-700'
        )

        with ui.splitter(value=75).classes('w-full h-full') as splitter:
            with splitter.before:
                with ui.scroll_area().classes('w-full h-[calc(100vh-80px)]'):
                    pagination_top_ref['el'] = ui.row().classes(
                        'w-full justify-center p-2'
                    )
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
        settings = getattr(app, 'config', None)
        levels = DEFAULT_UNDO_LEVELS
        if settings is not None:
            plugin_cfg = settings.plugins.settings.get('showscripter', {})
            levels = int(plugin_cfg.get('undo-levels', DEFAULT_UNDO_LEVELS))
        _build_page(undo_levels=levels)

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
        return {
            'label': 'Scripts',
            'path': '/script',
            'icon': 'description',
            'order': 10,
        }

    @showrunner.hookimpl
    def showrunner_get_status(self):
        return None
