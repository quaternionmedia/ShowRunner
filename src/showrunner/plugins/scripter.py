"""ShowScripter - Script viewer and cue editor.

Provides a NiceGUI page at ``/script`` for viewing scripts and placing cues
onto specific lines within the script text.
"""

import re
from collections import deque
from html import escape as _escape
from typing import Callable

from loguru import logger
from nicegui import app as nicegui_app
from nicegui import ui
from sqlmodel import select

import showrunner
from showrunner import ui as _sr_ui
from showrunner.layers import cue_number_label
from showrunner.layers import (
    add_layer,
    delete_layer,
    get_layer_types,
    get_layers,
    hook_results,
    is_color,
    update_layer,
)
from showrunner.models import Cue, CueList, Layer, Script
from showrunner.plugins.db import get_db
from showrunner.scriptformat import DEFAULT_LINE_HEIGHT, block_css, classify_lines, load_styles
from showrunner.ui import _current_script_id, _current_show_id, header

ALL_LISTS = 0  # cue-list filter value meaning "every cue list in the show"

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


def _cue_number_label(cue: dict) -> str:
    """The cue number as plugins format it (see showrunner_format_cue_number)."""
    return cue_number_label(cue.get('number'), cue.get('point'), cue.get('layer'), _sr_ui._pm)


def _get_previews(cues: list[dict]) -> dict[int, list[dict]]:
    """Collect preview chips per cue id from all plugins.

    A plugin that raises is logged and skipped; one that answers in the wrong
    shape loses what cannot be drawn -- an answer that is not a dict, chips that
    are not a list, a chip without text -- and every other plugin's chips stay.
    """
    pm = _sr_ui._pm
    if pm is None or not cues:
        return {}
    previews: dict[int, list[dict]] = {}
    for result in hook_results(pm, 'showrunner_script_previews', cues=cues):
        if result is None:
            continue
        if not isinstance(result, dict):
            logger.warning(f'showrunner_script_previews returned {type(result).__name__}; ignored')
            continue
        for cue_id, chips in result.items():
            drawable = [
                chip for chip in (chips if isinstance(chips, list) else [])
                if isinstance(chip, dict) and isinstance(chip.get('text'), str)
            ]
            if len(drawable) != (len(chips) if isinstance(chips, list) else 1):
                logger.warning(f'showrunner_script_previews: dropped malformed chips for cue {cue_id}')
            if drawable:
                previews.setdefault(cue_id, []).extend(drawable)  # several plugins may preview one cue
    return previews


def _carried_cue(cues: list[dict], previews: dict, first_line: int) -> dict | None:
    """Last cue with a preview positioned before *first_line* (1-based), by script position."""
    before = [
        c for c in cues
        if c['id'] in previews and c['script_line'] is not None and c['script_line'] < first_line
    ]
    return max(before, key=lambda c: (c['script_line'], c['script_char'] or 0), default=None)


def _load_cues(cue_list_id: int | None, all_lists: bool = False) -> list[dict]:
    """Cues of one cue list, or of every list in its show when *all_lists*."""
    if cue_list_id is None:
        return []
    with get_db().session() as s:
        cl = s.get(CueList, cue_list_id)
        if cl is None:  # list deleted since it was chosen
            return []
        in_scope = (
            Cue.cue_list_id.in_(select(CueList.id).where(CueList.show_id == cl.show_id))
            if all_lists
            else Cue.cue_list_id == cue_list_id
        )
        cues = s.exec(select(Cue).where(in_scope).order_by(Cue.number, Cue.point)).all()
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


def _build_page(undo_levels: int = DEFAULT_UNDO_LEVELS, plugin_cfg: dict | None = None) -> None:
    """Register the /script NiceGUI page.

    *plugin_cfg* is the ``[plugins.showscripter]`` table: ``show-previews``,
    ``line-height`` and ``format.<block>`` style overrides.
    """
    cfg = plugin_cfg or {}
    styles = load_styles(cfg.get('format'))
    line_height = str(cfg.get('line-height', DEFAULT_LINE_HEIGHT))
    if not re.fullmatch(r'[\d.]+(rem|em|px)?', line_height):
        line_height = DEFAULT_LINE_HEIGHT

    @ui.page('/script')
    def script_page():
        ui.dark_mode(True)
        header()

        # ---- state ----------------------------------------------------------
        selected_show_id: dict = {'v': _current_show_id()}
        selected_script_id: dict = {'v': None}
        selected_layer: dict = {'v': None}
        layers_by_name: dict = {'v': {}}  # name -> layer row; reloaded by _refresh_layers
        list_filter: dict = {'v': None}  # None = the script's cue list, ALL_LISTS = every list

        # pagination
        current_page: dict = {'v': 0}
        total_pages: dict = {'v': 1}
        parsed_pages: dict = {'v': []}

        # undo
        undo_stack: deque[dict] = deque(maxlen=undo_levels)

        # toggle for cue detail annotations
        # per-browser (server-side user storage), restored on page load
        show_details: dict = {'v': bool(nicegui_app.storage.user.get('show_details', False))}

        # toggle for plugin preview chips
        show_previews: dict = {
            'v': bool(nicegui_app.storage.user.get('show_previews', cfg.get('show-previews', True)))
        }

        # toggle for click-to-add-cue mode
        cue_mode: dict = {'v': False}

        # refs to dynamic containers
        script_select_ref: dict = {'el': None}
        script_content_ref: dict = {'el': None}
        pagination_top_ref: dict = {'el': None}
        pagination_bottom_ref: dict = {'el': None}
        scroll_ref: dict = {'el': None}
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

        def _refresh_layers() -> None:
            """Reload the show's layers and keep the selected layer one that exists."""
            show_id = selected_show_id['v']
            with get_db().session() as s:
                rows = get_layers(s, show_id, _sr_ui._pm) if show_id is not None else []
            layers_by_name['v'] = {r['name']: r for r in rows}
            if selected_layer['v'] not in layers_by_name['v']:
                selected_layer['v'] = next(iter(layers_by_name['v']), None)

        def _cue_scope(show_id: int) -> tuple[int, bool]:
            """(cue_list_id, all_lists) for the cue-list filter."""
            choice = list_filter['v']
            if choice == ALL_LISTS:
                return _get_or_create_cuelist(show_id), True
            if choice is None:
                return _get_or_create_cuelist(show_id), False
            with get_db().session() as s:
                cl = s.get(CueList, choice)
            if cl is None or cl.show_id != show_id:  # a restored filter may be stale
                list_filter['v'] = None
                return _get_or_create_cuelist(show_id), False
            return choice, False

        def _cue_list_options(show_id: int) -> dict:
            with get_db().session() as s:
                lists = s.exec(select(CueList).where(CueList.show_id == show_id)).all()
            return {ALL_LISTS: 'All lists', **{cl.id: cl.name for cl in lists}}

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

        def _notify_cue(action: str, cue_id: int, old: dict | None = None) -> None:
            """Tell plugins a cue was created, updated or deleted (see showrunner_cue_changed)."""
            if action == 'delete':
                cue, before = old, None
            else:
                state = _snapshot_cue(cue_id)
                cue = state and {'id': cue_id, **state}
                before = old and {'id': cue_id, **old} if action == 'update' else None
            if cue is None:
                return
            for msg in hook_results(_sr_ui._pm, 'showrunner_cue_changed', action=action, cue=cue, before=before):
                if msg:
                    ui.notify(f'TheatreMix not updated: {msg}', type='warning')

        def _update_cue(cue_id: int, record_undo: bool = True, **fields) -> None:
            """Update one or more fields on a cue."""
            old = _snapshot_cue(cue_id)
            if record_undo and old is not None:
                changed = {k: old[k] for k in fields if k in old}
                desc = f'Edit cue {old["layer"] or ""} {_cue_number_label(old)}'
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
            if old is not None:
                _notify_cue('update', cue_id, old)

        def _delete_cue(cue_id: int, record_undo: bool = True) -> None:
            """Delete a cue by id."""
            snap = _snapshot_cue(cue_id)
            if record_undo and snap is not None:
                desc = f'Delete cue {snap["layer"] or ""} {_cue_number_label(snap)}'
                _push_undo(
                    desc,
                    lambda s=snap: _recreate_cue(s),
                )

            with get_db().session() as s:
                cue = s.get(Cue, cue_id)
                if cue is not None:
                    s.delete(cue)
                    s.commit()
            if snap is not None:
                _notify_cue('delete', cue_id, snap)

        def _recreate_cue(snap: dict) -> None:
            """Re-create a cue from a snapshot dict (used by undo of delete)."""
            with get_db().session() as s:
                cue = Cue(**snap)
                s.add(cue)
                s.commit()
                s.refresh(cue)
                new_id = cue.id
            _notify_cue('create', new_id)

        # ---- render helpers -------------------------------------------------
        def _layer_colour(name: str | None) -> str:
            row = layers_by_name['v'].get(name)
            return row['color'] if row else 'grey'

        def _layer_options(current: str | None) -> list[str]:
            names = list(layers_by_name['v'])
            if current and current not in names:  # cue on a layer that was removed
                names.append(current)
            return names

        def render_cue_chip(c: dict, compact: bool = False) -> None:
            """Render a draggable, editable cue badge."""
            color = _layer_colour(c['layer'])
            number = _cue_number_label(c)
            label_text = f'{(c["layer"] or "")[:1]} {number}'
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
                badge.tooltip(f'{c["layer"]} {number}: {c["name"] or ""}')
                if compact:
                    badge.style('font-size: 0.65rem; padding: 1px 4px;')

            with ui.menu().props('anchor="bottom left" self="top left"') as menu:
                with ui.card().classes('p-3 gap-2').style('min-width: 260px'):
                    ui.label('Edit Cue').classes('text-subtitle2 font-bold')

                    name_input = ui.input(
                        label='Name',
                        value=c['name'] or '',
                    ).classes('w-full')

                    number_input = ui.input(
                        label='Number',
                        value=c['number'] or '',
                    ).classes('w-full')

                    layer_select = ui.select(
                        options=_layer_options(c['layer']),
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
                                    number=number_input.value or None,
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
            color = _layer_colour(c['layer'])
            with ui.row().classes('items-center w-full').style('flex-shrink: 0'):
                # Dotted connector line — stretches to fill gap
                ui.element('div').style(
                    f'flex: 1; min-width: 20px; border-top: 2px dotted {color}; opacity: 0.4;'
                ).classes('self-center')
                # Editable fields
                num_input = (
                    ui.input(
                        value=c['number'] or '',
                        placeholder='#',
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
                        val = el.value or None if f == 'number' else el.value
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
            cl_id, all_lists = _cue_scope(show_id)
            cues = _load_cues(cl_id, all_lists)

            # Index positioned cues by (line, char)
            cues_by_line: dict[int, list[dict]] = {}
            for c in cues:
                if c['script_line'] is not None:
                    cues_by_line.setdefault(c['script_line'], []).append(c)
            # Sort cues within each line by char position
            for line_cues in cues_by_line.values():
                line_cues.sort(key=lambda c: c['script_char'] or 0)

            previews = _get_previews(cues) if show_previews['v'] else {}

            lines = content.split('\n')
            kinds = classify_lines(lines)
            pages = _parse_pages(lines)
            parsed_pages['v'] = pages
            total_pages['v'] = max(1, len(pages))
            current_page['v'] = min(current_page['v'], total_pages['v'] - 1)
            # per-browser (server-side user storage), per-script: a refresh reopens the page being read
            nicegui_app.storage.user.setdefault('script_views', {})[str(sid)] = {
                'page': current_page['v'],
                'list': list_filter['v'],
            }

            page = current_page['v']
            page_lines = pages[page]['lines']

            detail_active = show_details['v']

            def render_preview(chips: list[dict], cue: dict, carried: bool = False) -> None:
                """A boxed chip group with a pointer up to its source cue."""
                colour = _layer_colour(cue['layer'])
                colour = colour if is_color(colour) else 'grey'
                number = _cue_number_label(cue)
                # grid-column spans both columns so it works with the detail column on or off
                with ui.element('div').style('grid-column: 1 / -1; margin: 0.4rem 0 0.2rem 3rem;'):
                    with ui.element('div').classes(f'relative text-{colour}').style(
                        'display: inline-block; max-width: 80%; padding: 4px 8px; '
                        'border: 1px solid currentColor; border-radius: 6px; background: #1b1b1b;'
                    ):
                        ui.element('div').style(
                            'position: absolute; top: -6px; left: 1rem; width: 9px; height: 9px; '
                            'background: #1b1b1b; border-left: 1px solid currentColor; '
                            'border-top: 1px solid currentColor; transform: rotate(45deg);'
                        )
                        with ui.row().classes('items-center gap-1'):
                            ui.badge(f'{(cue["layer"] or "")[:1]} {number}', color=colour).props(
                                'dense'
                            )
                            if carried:
                                ui.label(f'from line {cue["script_line"]}').classes(
                                    'text-xs text-grey'
                                )
                            for chip in chips:
                                bg_colour = chip.get('color')
                                if not is_color(bg_colour):  # reaches a Quasar props string
                                    bg_colour = 'grey-9'
                                badge = ui.badge(chip['text']).props(
                                    f'color={bg_colour} text-color=white'
                                )
                                if chip.get('tooltip'):
                                    badge.tooltip(chip['tooltip'])

            with container:
                # When details are active, use a 2-column CSS grid
                grid_style = (
                    'display: grid; grid-template-columns: 1fr 40%; width: 100%;'
                    if detail_active
                    else 'display: grid; grid-template-columns: 1fr; width: 100%;'
                )
                with ui.element('div').style(grid_style):
                    carried = (
                        _carried_cue(cues, previews, page_lines[0][0] + 1)
                        if page_lines and page > 0
                        else None
                    )
                    if carried:
                        render_preview(previews[carried['id']], carried, carried=True)
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
                                    const offsetAttr = (span && (span.closest('[data-offset]') || {}).dataset) || {};
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
                            _render_line_with_cues(line, line_num, line_cues, kinds[line_idx])

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

                        for c in line_cues:
                            if c['id'] in previews:
                                render_preview(previews[c['id']], c)

            render_pagination()

        def _render_line_with_cues(
            line: str, line_num: int, line_cues: list[dict], kind: str = 'action'
        ) -> None:
            """Render a script line styled as its block *kind*, cues floating above the text.

            Cue chips sit in zero-width anchors at their character offsets, so they never
            move the words. Clicking a text segment places a new cue at that character.
            """

            def _make_clickable_span(text: str, ln: int, segment_offset: int) -> None:
                """Create a clickable text span that reports character offset."""
                cursor = 'crosshair' if cue_mode['v'] else 'text'
                # Use ui.element('span') so NiceGUI's standard event dispatch works.
                # ui.html() does not inject `emit()` into js_handler contexts, so
                # click events on it would silently never reach Python.
                el = (
                    ui.element('span')
                    .style(f'white-space: pre-wrap; cursor: {cursor};')
                )
                el._props['data-offset'] = str(segment_offset)
                el._props['innerHTML'] = _escape(text) or '\u00a0'

                def _on_click(e, ln=ln, so=segment_offset):
                    if not cue_mode['v']:
                        return
                    char_offset = so
                    if isinstance(e.args, dict):
                        char_offset = so + e.args.get('char_offset', 0)
                    add_cue(ln, char_offset)

                el.on(
                    'click',
                    handler=_on_click,
                    js_handler='''(e) => {
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

            by_char: dict[int, list[dict]] = {}
            for c in line_cues:
                pos = min(c['script_char'] or 0, len(line))
                by_char.setdefault(pos, []).append(c)

            with ui.element('div').style(
                f'position: relative; flex: 1; min-width: 0; line-height: {line_height}; '
                f'font-family: monospace; font-size: 0.875rem; {block_css(styles[kind])}'
            ):
                cursor = 0
                for pos in sorted(by_char):
                    if pos > cursor:
                        _make_clickable_span(line[cursor:pos], line_num, cursor)
                        cursor = pos
                    with ui.element('span').style(
                        'position: relative; display: inline-block; width: 0; height: 0;'
                    ):
                        with ui.element('span').style(
                            'position: absolute; left: 0; bottom: 0.6em; white-space: nowrap; '
                            'z-index: 2; line-height: 1; font-style: normal; font-weight: normal; '
                            'text-transform: none;'
                        ):
                            for c in by_char[pos]:
                                render_cue_chip(c, compact=True)
                _make_clickable_span(line[cursor:] or '\u00a0', line_num, cursor)

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
                    raw_label = (
                        parsed_pages['v'][page].get('label') if parsed_pages['v'] else None
                    )  # may be non-numeric, e.g. a roman numeral
                    page_label = f'Page {raw_label or page + 1}'
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
                    goto_input = (
                        ui.number(
                            value=current_label_int if current_label_int is not None else page + 1,
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
            for ref in (pagination_top_ref, pagination_bottom_ref):
                container = ref['el']
                if container is not None:
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

            cl_id, all_lists = _cue_scope(show_id)
            cues = _load_cues(cl_id, all_lists)
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
            _refresh_layers()
            render_toolbar()
            render_script_content()
            render_unpositioned()

        def go_to_page(page: int):
            page = max(0, min(page, total_pages['v'] - 1))
            current_page['v'] = page
            render_script_content()
            if scroll_ref['el'] is not None:
                scroll_ref['el'].scroll_to(percent=0)

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
            list_filter['v'] = None
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

        def on_list_filter(val):
            list_filter['v'] = val
            current_page['v'] = 0
            refresh_all()

        def open_layers_dialog():
            """Dialog to list, add, edit and delete the show's layers."""
            show_id = selected_show_id['v']
            if show_id is None:
                ui.notify('Select a show first.', type='warning')
                return
            types = get_layer_types(_sr_ui._pm)
            type_labels = {t: v['label'] for t, v in types.items()}

            dlg = ui.dialog()
            with dlg, ui.card().style('min-width: 640px'):
                ui.label('Layers').classes('text-h6')
                rows_box = ui.column().classes('w-full gap-2')
                ui.separator()
                with ui.row().classes('w-full items-end gap-2'):
                    new_name = ui.input('New layer name').classes('flex-1')
                    new_type = ui.select(type_labels, value='generic', label='Type').classes('w-40')
                    new_color = ui.input('Colour', value='grey').classes('w-32')

                    def _add():
                        try:
                            with get_db().session() as s:
                                add_layer(s, show_id, new_name.value, new_type.value,
                                          new_color.value, _sr_ui._pm)
                        except ValueError as exc:
                            ui.notify(str(exc), type='negative')
                            return
                        new_name.value = ''
                        _changed()

                    ui.button('Add', on_click=_add)

            def _save(lid, name, type_, color):
                try:
                    with get_db().session() as s:
                        update_layer(s, s.get(Layer, lid), _sr_ui._pm,
                                     name=name, type=type_, color=color)
                except ValueError as exc:
                    ui.notify(str(exc), type='negative')
                    return
                ui.notify('Layer saved')
                _changed()

            def _delete(lid):
                try:
                    with get_db().session() as s:
                        delete_layer(s, s.get(Layer, lid))
                except ValueError as exc:
                    ui.notify(str(exc), type='negative')
                    return
                ui.notify('Layer deleted')
                _changed()

            def _draw():
                rows_box.clear()
                with rows_box:
                    with get_db().session() as s:
                        rows = get_layers(s, show_id, _sr_ui._pm)
                    for r in rows:
                        opts = dict(type_labels)
                        opts.setdefault(r['type'], f"{r['type']} (plugin not loaded)")
                        with ui.row().classes('w-full items-end gap-2'):
                            name_in = ui.input('Name', value=r['name']).classes('flex-1')
                            type_in = ui.select(opts, value=r['type'], label='Type').classes('w-40')
                            color_in = ui.input('Colour', value=r['color']).classes('w-32')
                            ui.button(
                                icon='save',
                                on_click=lambda _, lid=r['id'], n=name_in, t=type_in, c=color_in:
                                    _save(lid, n.value, t.value, c.value),
                            ).props('flat dense').tooltip('Save')
                            ui.button(
                                icon='delete', color='red',
                                on_click=lambda _, lid=r['id']: _delete(lid),
                            ).props('flat dense').tooltip('Delete layer')

            def _changed():
                _refresh_layers()
                refresh_all()
                _draw()

            _draw()
            dlg.open()

        def set_layer(layer: str):
            selected_layer['v'] = layer
            render_toolbar()

        def toggle_details():
            show_details['v'] = not show_details['v']
            nicegui_app.storage.user['show_details'] = show_details['v']
            render_toolbar()
            render_script_content()

        def toggle_previews():
            show_previews['v'] = not show_previews['v']
            nicegui_app.storage.user['show_previews'] = show_previews['v']
            render_toolbar()
            render_script_content()

        def toggle_cue_mode():
            cue_mode['v'] = not cue_mode['v']
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
                for name, info in layers_by_name['v'].items():
                    is_active = selected_layer['v'] == name
                    ui.button(
                        name,
                        on_click=lambda _, ly=name: set_layer(ly),
                        color=info['color'],
                    ).props('dense unelevated' if is_active else 'dense outline')
                ui.button('Layers', icon='tune', on_click=open_layers_dialog).props('flat dense')
                show_id = selected_show_id['v']
                if show_id is not None:
                    scope_id, all_lists = _cue_scope(show_id)
                    current = ALL_LISTS if all_lists else scope_id
                    ui.select(
                        _cue_list_options(show_id),
                        value=current,
                        label='Cue list',
                        on_change=lambda e: on_list_filter(e.value),
                    ).props('dense').classes('w-44')
                ui.separator().props('vertical')
                is_cue_mode = cue_mode['v']
                (
                    ui.button(
                        'Q',
                        on_click=toggle_cue_mode,
                        color='yellow' if is_cue_mode else None,
                    )
                    .props('dense unelevated' if is_cue_mode else 'dense outline')
                    .tooltip(
                        'Click on script to add cue'
                        if is_cue_mode
                        else 'Enable click-to-add cue mode'
                    )
                )
                ui.separator().props('vertical')
                ui.button(
                    'Cue Details',
                    icon='visibility' if show_details['v'] else 'visibility_off',
                    on_click=toggle_details,
                    color='white' if show_details['v'] else None,
                ).props('dense unelevated' if show_details['v'] else 'dense outline')
                ui.button(
                    'Previews',
                    icon='visibility' if show_previews['v'] else 'visibility_off',
                    on_click=toggle_previews,
                    color='white' if show_previews['v'] else None,
                ).props('dense unelevated' if show_previews['v'] else 'dense outline')
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
            layer = selected_layer['v']
            if layer is None:
                ui.notify('Add a layer first (Layers button).', type='warning')
                return
            cl_id, _ = _cue_scope(show_id)  # the list being viewed, so the new cue is visible

            try:
                with get_db().session() as s:
                    cue = Cue(
                        cue_list_id=cl_id,
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
            except Exception as exc:
                ui.notify(f'Failed to create cue: {exc}', type='negative')
                return

            _notify_cue('create', new_id)
            desc = f'Add {layer} cue'
            _push_undo(desc, lambda cid=new_id: _delete_cue(cid, False))

            pos_desc = ''
            if line_num is not None:
                pos_desc = f' at line {line_num}'
                if char_pos:
                    pos_desc += f', char {char_pos}'
            ui.notify(f'Added {layer} cue{pos_desc}')
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
                    saved = nicegui_app.storage.user.get('script_views', {}).get(str(initial_value))
                    if isinstance(saved, dict):
                        page, flt = saved.get('page'), saved.get('list')
                        current_page['v'] = page if isinstance(page, int) and page > 0 else 0
                        list_filter['v'] = flt if isinstance(flt, int) else None

        # Toolbar
        toolbar_ref['el'] = ui.row().classes(
            'w-full items-center gap-4 px-4 py-2 bg-gray-900 border-b border-gray-700'
        )

        with ui.splitter(value=75).classes('w-full h-full') as splitter:
            with splitter.before:
                with ui.column().classes('w-full gap-0'):
                    # pinned above the scroll area so it stays put when the page changes
                    pagination_top_ref['el'] = ui.row().classes(
                        'w-full justify-center p-2 border-b border-gray-700'
                    )
                    with ui.scroll_area().classes('w-full h-[calc(100vh-180px)]') as scroll:
                        scroll_ref['el'] = scroll
                        script_content_ref['el'] = ui.column().classes('w-full p-4')
                    pagination_bottom_ref['el'] = ui.row().classes(
                        'w-full justify-center p-2 border-t border-gray-700'
                    )

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
        plugin_cfg: dict = {}
        if settings is not None:
            plugin_cfg = settings.plugins.settings.get('showscripter', {})
            levels = int(plugin_cfg.get('undo-levels', DEFAULT_UNDO_LEVELS))
        _build_page(undo_levels=levels, plugin_cfg=plugin_cfg)

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
