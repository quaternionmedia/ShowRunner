"""ShowScripter - Script viewer and cue editor.

Provides a NiceGUI page at ``/script`` for viewing scripts and placing cues
onto specific lines within the script text.
"""

import json
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
from showrunner.groups import (
    container_for,
    create_group,
    delete_group,
    group_depths,
    group_tree,
    next_number,
    set_members,
    update_group,
)
from showrunner.layers import (
    add_layer,
    cue_number_label,
    delete_layer,
    get_layer_types,
    get_layers,
    hook_results,
    is_color,
    update_layer,
)
from showrunner.models import Cue, CueGroup, CueList, Layer, Script
from showrunner.plugins.db import get_db
from showrunner.renumber import renumber_cues
from showrunner.scriptformat import DEFAULT_LINE_HEIGHT, block_css, classify_lines, load_styles
from showrunner.ui import _current_script_id, _current_show_id, header

ALL_LISTS = 0  # cue-list filter value meaning "every cue list in the show"

PAGE_SIZE = 100
PAGE_BREAK_RE = re.compile(r'\[\[Page\s+(.+?)\]\]')
DEFAULT_UNDO_LEVELS = 50
GROUP_COLOURS = ['grey', 'red', 'orange', 'amber', 'yellow', 'lime', 'green', 'teal', 'cyan', 'blue', 'indigo', 'purple', 'pink']


def _group_colour(colour) -> str:
    """A colour the group picker offers; anything else (e.g. QLab's 'none') shows as grey."""
    return colour if colour in GROUP_COLOURS else 'grey'

# Draws group boxes around the chips of each group (window.srDrawGroups) and the rubber-band
# selection in group mode (window.srGroupMode). Chips carry data-cue-id; the host is #sr-script-host.
_GROUP_JS = """
<script>
window.srDrawGroups = (groups, tries = 0) => {
  const ov = document.getElementById('sr-group-overlay');
  if (!ov) { if (tries < 20) setTimeout(() => window.srDrawGroups(groups, tries + 1), 50); return; }
  window.srLast = groups;
  ov.innerHTML = '';
  const host = ov.parentElement, base = ov.getBoundingClientRect();
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('width', base.width);
  svg.setAttribute('height', base.height);
  svg.style.cssText = 'position:absolute;left:0;top:0;overflow:visible;';
  ov.appendChild(svg);
  const allLines = [...host.querySelectorAll('[data-line]')];
  for (const g of groups) {
    const chips = g.cue_ids.map(id => host.querySelector(`[data-cue-id="${id}"]`)).filter(Boolean);
    const memberLines = new Set(chips.map(el => el.closest('[data-line]')).filter(Boolean));
    if (!memberLines.size) continue;
    // One row per line from the group's first to its last member line. A row spans only the
    // member cues on that line, so other cues on the same line stay outside the box.
    const idx = allLines.map((el, i) => memberLines.has(el) ? i : -1).filter(i => i >= 0);
    const span = allLines.slice(Math.min(...idx), Math.max(...idx) + 1);
    const pad = 4 + g.nest * 4;
    const raw = span.map(el => {
      const r = el.getBoundingClientRect();
      const mine = chips.filter(c => el.contains(c)).map(c => c.getBoundingClientRect());
      return {
        l: (mine.length ? Math.min(...mine.map(m => m.left)) : r.left) - base.left,
        r: (mine.length ? Math.max(...mine.map(m => m.right)) : r.right) - base.left,
        t: Math.min(r.top, ...mine.map(m => m.top)) - base.top,
        b: r.bottom - base.top,
      };
    });
    const n = raw.length;
    // Row edges meet halfway between lines, so the rows form one continuous outline
    const edge = [raw[0].t - pad - 10];
    for (let i = 1; i < n; i++) edge.push((raw[i - 1].b + raw[i].t) / 2);
    edge.push(raw[n - 1].b + pad);
    const rows = raw.map((r, i) => ({l: r.l - pad, r: r.r + pad, t: edge[i], b: edge[i + 1]}));
    const pts = [];
    rows.forEach(r => pts.push([r.r, r.t], [r.r, r.b]));
    rows.slice().reverse().forEach(r => pts.push([r.l, r.b], [r.l, r.t]));
    const path = document.createElementNS(NS, 'path');
    path.setAttribute('d', 'M' + pts.map(p => p.join(',')).join(' L') + ' Z');
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', 'currentColor');
    path.setAttribute('stroke-width', '2');
    path.setAttribute('class', `text-${g.color}`);
    svg.appendChild(path);
    const label = document.createElement('div');
    label.textContent = g.name;
    label.style.cssText = `position:absolute;left:${rows[0].l + 8}px;top:${edge[0]}px;transform:translateY(-100%);padding:0 4px;background:#121212;font-size:0.7rem;white-space:nowrap;pointer-events:auto;cursor:pointer;z-index:2;color:inherit;`;
    label.className = `text-${g.color}`;
    label.addEventListener('click', () => ov.dispatchEvent(new CustomEvent('srgroup', {detail: g.id})));
    ov.appendChild(label);
  }
  if (!window.srResizeHooked) {
    window.srResizeHooked = true;
    window.addEventListener('resize', () => window.srLast && window.srDrawGroups(window.srLast));
  }
};
window.srGroupMode = (on, hostId) => {
  const host = document.getElementById(hostId);
  if (!host) return;
  host._srGroupOn = on;
  host.style.cursor = on ? 'crosshair' : '';
  if (host._srHooked) return;
  host._srHooked = true;
  host.addEventListener('mousedown', (e) => {
    if (!host._srGroupOn || e.button !== 0) return;
    e.preventDefault();
    const x0 = e.clientX, y0 = e.clientY;
    const sel = document.createElement('div');
    sel.style.cssText = 'position:fixed;border:1px dashed #fff;background:rgba(255,255,255,0.08);pointer-events:none;z-index:9999;';
    document.body.appendChild(sel);
    const move = (ev) => {
      sel.style.left = Math.min(x0, ev.clientX) + 'px'; sel.style.top = Math.min(y0, ev.clientY) + 'px';
      sel.style.width = Math.abs(ev.clientX - x0) + 'px'; sel.style.height = Math.abs(ev.clientY - y0) + 'px';
    };
    const up = (ev) => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      sel.remove();
      const L = Math.min(x0, ev.clientX), R = Math.max(x0, ev.clientX);
      const T = Math.min(y0, ev.clientY), B = Math.max(y0, ev.clientY);
      if (R - L < 4 && B - T < 4) return;
      const ids = [...host.querySelectorAll('[data-cue-id]')].filter((el) => {
        const r = el.getBoundingClientRect();
        return r.right >= L && r.left <= R && r.bottom >= T && r.top <= B;
      }).map((el) => Number(el.dataset.cueId));
      if (ids.length) host.dispatchEvent(new CustomEvent('srselect', {detail: [...new Set(ids)]}));
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  });
};
</script>
"""


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
                'notes': c.notes,
                'layer': c.layer,
                'script_line': c.script_line,
                'script_char': c.script_char,
                'group_id': c.group_id,
            }
            for c in cues
        ]

def _load_groups(cue_list_id: int | None, all_lists: bool = False) -> list[dict]:
    """Groups of the cue list in scope (every list in the show when *all_lists*), from group_tree."""
    if cue_list_id is None:
        return []
    with get_db().session() as s:
        cl = s.get(CueList, cue_list_id)
        if cl is None:
            return []
        ids = (
            s.exec(select(CueList.id).where(CueList.show_id == cl.show_id)).all()
            if all_lists
            else [cue_list_id]
        )
        return [g for lid in ids for g in group_tree(s, lid)]


def _group_dict(session, g: CueGroup) -> dict:
    """A group's state as plugin hooks see it: its own cue ids (not its descendants')."""
    return {
        'id': g.id,
        'name': g.name,
        'color': g.color,
        'parent_id': g.parent_id,
        'external_id': g.external_id,
        'layer': g.layer,
        'number': g.number,
        'cue_ids': list(session.exec(select(Cue.id).where(Cue.group_id == g.id))),
    }


def _group_snapshot(group_id: int) -> dict | None:
    with get_db().session() as s:
        g = s.get(CueGroup, group_id)
        return None if g is None else _group_dict(s, g)


def _cue_state(cue: Cue) -> dict:
    """The restorable fields of a cue, as the undo stack and plugin hooks see them."""
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
        'external_id': cue.external_id,
        'external_number': cue.external_number,
    }


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
        ui.add_body_html(_GROUP_JS)

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

        # new groups pre-fill the next free number on their layer (per-browser, like the others)
        auto_number: dict = {'v': bool(nicegui_app.storage.user.get('auto_number_groups', False))}

        # toggle for plugin preview chips
        show_previews: dict = {
            'v': bool(nicegui_app.storage.user.get('show_previews', cfg.get('show-previews', True)))
        }

        # toggle for click-to-add-cue mode
        cue_mode: dict = {'v': False}
        group_mode: dict = {'v': False}  # rubber-band selection draws a new group

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
                return _cue_state(cue)

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

        def _notify_group(action: str, group: dict | None, before: dict | None = None) -> None:
            """Tell plugins a group changed (see showrunner_group_changed)."""
            if group is None:
                return
            for msg in hook_results(_sr_ui._pm, 'showrunner_group_changed', action=action, group=group, before=before):
                if msg:
                    ui.notify(f'Plugin did not update group: {msg}', type='warning')

        def _single_list(cue_ids: list[int]) -> int | None:
            """The cue list all *cue_ids* belong to, or None (with a notice) when they span lists."""
            with get_db().session() as s:
                lists = set(s.exec(select(Cue.cue_list_id).where(Cue.id.in_(cue_ids))))
            if len(lists) != 1:
                ui.notify('Select cues from one cue list.', type='warning')
                return None
            return lists.pop()

        def _create_group(cl_id: int, cue_ids: list[int], name: str, color: str, layer=None, number=None) -> None:
            """New group around *cue_ids*, nested in the smallest existing group that already holds them all."""
            with get_db().session() as s:
                parent = container_for(s, cl_id, cue_ids)
                gid = create_group(s, cl_id, cue_ids, name=name, color=color, parent_id=parent,
                                   layer=layer, number=number).id
                s.commit()
            _notify_group('create', _group_snapshot(gid))
            refresh_all()

        def _update_group(group_id: int, **fields) -> None:
            """Change a group's name, colour, layer or number (ValueError if the number is taken)."""
            before = _group_snapshot(group_id)
            with get_db().session() as s:
                update_group(s, group_id, **fields)
                s.commit()
            _notify_group('update', _group_snapshot(group_id), before)
            refresh_all()

        def _delete_group(group_id: int) -> None:
            """Remove a group. Its cues stay in the list, in the enclosing group if there is one."""
            before = _group_snapshot(group_id)
            with get_db().session() as s:
                delete_group(s, group_id)
                s.commit()
            _notify_group('delete', before)
            refresh_all()

        def _set_group_members(gid: int, cue_ids: list[int]) -> None:
            """Make *cue_ids* the group's own cues; the ones it drops move to the enclosing group."""
            before = _group_snapshot(gid)
            with get_db().session() as s:
                set_members(s, gid, cue_ids)
                s.commit()
            _notify_group('members', _group_snapshot(gid), before)
            refresh_all()

        def _remove_from_group(cue_id: int) -> None:
            with get_db().session() as s:
                cue = s.get(Cue, cue_id)
                gid = cue.group_id if cue else None
            before = _group_snapshot(gid) if gid is not None else None
            if before is None:
                return
            _set_group_members(gid, [c for c in before['cue_ids'] if c != cue_id])

        def open_group_dialog(group: dict | None, cue_ids: list[int] | None = None, cl_id: int | None = None) -> None:
            """Name and colour for a new group (*cue_ids* and *cl_id*) or an existing one (*group*)."""
            dlg = ui.dialog()
            with dlg, ui.card().classes('p-3 gap-2').style('min-width: 300px'):
                ui.label('Edit group' if group else 'New group').classes('text-subtitle2 font-bold')
                name_in = ui.input(label='Name', value=(group or {}).get('name', '')).classes('w-full')
                colour_in = ui.select(
                    GROUP_COLOURS, value=_group_colour((group or {}).get('color')), label='Colour'
                ).classes('w-full')
                # A new group takes the layer its cues share, numbered like a cue on that layer
                # A new group takes the active layer; with auto-number on, its number is pre-filled
                layer_default = (group or {}).get('layer')
                if group is None:
                    layer_default = selected_layer['v'] if selected_layer['v'] in layers_by_name['v'] else None

                def _suggest(layer_name: str | None) -> str:
                    if group is not None or cl_id is None or not cue_ids or not layer_name:
                        return ''
                    if not auto_number['v']:
                        return ''
                    with get_db().session() as s:
                        return next_number(s, cl_id, layer_name, cue_ids)

                with ui.row().classes('w-full items-end gap-2'):
                    layer_in = ui.select(list(layers_by_name['v']), value=layer_default, label='Layer',
                                         clearable=True).classes('flex-1')
                    number_in = ui.input(label='Number', value=(group or {}).get('number') or _suggest(layer_default)).classes('flex-1')
                    if group is None:
                        layer_in.on_value_change(
                            lambda e: number_in.set_value(_suggest(e.value)) if auto_number['v'] else None
                        )
                # Unticking a cue unlinks it from this group on Save
                member_checks: dict[int, ui.checkbox] = {}
                if group and group['cue_ids']:
                    ui.label('Cues in this group').classes('text-caption text-grey mt-2')
                    with get_db().session() as s:
                        members = s.exec(
                            select(Cue).where(Cue.id.in_(group['cue_ids']))
                            .order_by(Cue.script_line, Cue.script_char)
                        ).all()
                        rows = [(c.id, c.layer or '', _cue_number_label({'number': c.number, 'point': c.point,
                                                                       'layer': c.layer}), c.name or '')
                                for c in members]
                    for cid, layer, number, name in rows:
                        text = ' '.join(part for part in (layer, number, name) if part)
                        member_checks[cid] = ui.checkbox(text, value=True)

                def _save(_):
                    name, colour = name_in.value.strip(), colour_in.value
                    layer, number = layer_in.value or None, (number_in.value or '').strip() or None
                    try:
                        if group:
                            keep = [cid for cid, box in member_checks.items() if box.value]
                            _update_group(group['id'], name=name, color=colour, layer=layer, number=number)
                            if len(keep) != len(member_checks):
                                _set_group_members(group['id'], keep)
                        else:
                            _create_group(cl_id, cue_ids, name, colour, layer, number)
                    except ValueError as exc:
                        ui.notify(str(exc), type='negative')
                        return
                    dlg.close()

                with ui.row().classes('w-full justify-between items-center mt-2'):
                    if group:
                        ui.button('Delete group', color='red', on_click=lambda _: (_delete_group(group['id']), dlg.close())).props('flat dense').tooltip('Cues stay in the list')
                    else:
                        ui.label('')
                    with ui.row():
                        ui.button('Cancel', on_click=dlg.close).props('flat')
                        ui.button('Save' if group else 'Create', on_click=_save)
            dlg.open()

        def _on_group_select(e) -> None:
            ids = [int(i) for i in (e.args or {}).get('ids', [])]
            if not ids:
                return
            cl_id = _single_list(ids)
            if cl_id is not None:
                open_group_dialog(None, ids, cl_id)

        def _open_group_by_id(group_id) -> None:
            snap = _group_snapshot(int(group_id))
            if snap is not None:
                open_group_dialog(snap)

        def toggle_group_mode() -> None:
            group_mode['v'] = not group_mode['v']
            ui.run_javascript(f'window.srGroupMode({json.dumps(group_mode["v"])}, "sr-script-host")')
            render_toolbar()

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
            wrapper._props['data-cue-id'] = str(cue_id)
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

                    if c.get('group_id') is not None:
                        ui.button(
                            'Remove from group',
                            icon='link_off',
                            on_click=lambda _, cid=c['id']: (_remove_from_group(cid), menu.close(), refresh_all()),
                        ).props('flat dense')

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
                # Textarea so long notes wrap inside the detail column
                notes_input = (
                    ui.textarea(
                        value=c['notes'] or '',
                        placeholder='notes',
                    )
                    .props('dense borderless autogrow')
                    .classes('flex-1 min-w-0')
                    .style('color: grey; font-style: italic; white-space: pre-wrap;')
                )
                # Save on blur for all fields; enter saves the single-line ones
                for inp, field in [(num_input, 'number'), (name_input, 'name'), (notes_input, 'notes')]:

                    def _save(_, cid=c['id'], f=field, el=inp):
                        val = el.value or None if f in ('number', 'notes') else el.value
                        _update_cue(cid, True, **{f: val})

                    inp.on('blur', _save)
                    if field != 'notes':
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
            groups = _load_groups(cl_id, all_lists)

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

                # Group boxes: JS measures the chips of each group's cues on this page
                on_page = {c['id'] for line_idx, _ in page_lines for c in cues_by_line.get(line_idx + 1, [])}
                depths = group_depths(groups)
                deepest = max(depths.values(), default=0)
                boxes = [
                    {
                        'id': g['id'],
                        'name': ' '.join(filter(None, [
                            _cue_number_label({'number': g['number'], 'point': 0, 'layer': g['layer']})
                            if g['number'] else '',
                            g['name'] or '(unnamed)',
                        ])),
                        'color': _group_colour(g['color']),
                        'nest': deepest - depths[g['id']],  # outer boxes sit outside inner ones
                        'cue_ids': [i for i in g['member_ids'] if i in on_page],
                    }
                    for g in sorted(groups, key=lambda g: depths[g['id']])
                ]
                boxes = [b for b in boxes if b['cue_ids']]
                overlay = ui.element('div').props('id=sr-group-overlay').style(
                    'position: absolute; inset: 0; pointer-events: none;'
                )
                overlay.on(
                    'srgroup',
                    lambda e: _open_group_by_id(e.args['id']),
                    js_handler='(e) => emit({id: e.detail})',
                )
                ui.timer(0.05, lambda: ui.run_javascript(f'window.srDrawGroups({json.dumps(boxes)})'), once=True)

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
            ) as line_el:
                line_el._props['data-line'] = str(line_num)  # group boxes measure this
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
                    new_prefix = ui.input('Prefix', placeholder='from name').classes('w-32')

                    def _add():
                        try:
                            with get_db().session() as s:
                                add_layer(s, show_id, new_name.value, new_type.value,
                                          new_color.value, _sr_ui._pm,
                                          prefix=new_prefix.value.strip() or None)
                        except ValueError as exc:
                            ui.notify(str(exc), type='negative')
                            return
                        new_name.value = ''
                        _changed()

                    ui.button('Add', on_click=_add)

            def _save(lid, name, type_, color, prefix):
                try:
                    with get_db().session() as s:
                        update_layer(s, s.get(Layer, lid), _sr_ui._pm,
                                     name=name, type=type_, color=color, prefix=prefix.strip())
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
                            prefix_in = ui.input('Prefix', value=r['prefix']).classes('w-24')
                            ui.button(
                                icon='save',
                                on_click=lambda _, lid=r['id'], n=name_in, t=type_in, c=color_in,
                                    p=prefix_in: _save(lid, n.value, t.value, c.value, p.value),
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

        def _renumber_list_ids(show_id: int) -> list[int]:
            """Cue lists a renumber applies to: the list being viewed, or all of them when showing all."""
            cl_id, all_lists = _cue_scope(show_id)
            if not all_lists:
                return [cl_id]
            with get_db().session() as s:
                return list(s.exec(select(CueList.id).where(CueList.show_id == show_id)).all())

        def _run_renumber(
            layer: str,
            cue_range: tuple[int, int],
            start: int | None,
            increment: int,
            include_unnumbered: bool,
            apply: bool,
        ) -> list[tuple[str, int, str, str, dict]]:
            """Renumber cues and groups on *layer* in the show's scope.

            Returns ``(kind, id, old_label, new_label, state_before)`` per changed item; kind is
            'cue' or 'group'. Everything runs in one session, so an error in any list changes
            nothing; the session is only committed when *apply* is true.
            """
            show_id = selected_show_id['v']
            list_ids = _renumber_list_ids(show_id)
            changes = []
            with get_db().session() as s:
                for cl_id in list_ids:
                    before = {
                        ('cue', c.id): _cue_state(c)
                        for c in s.exec(select(Cue).where(Cue.cue_list_id == cl_id, Cue.layer == layer)).all()
                    }
                    before.update({
                        ('group', g.id): _group_dict(s, g)
                        for g in s.exec(select(CueGroup).where(CueGroup.cue_list_id == cl_id, CueGroup.layer == layer)).all()
                    })
                    for item, old, new in renumber_cues(
                        s,
                        cl_id,
                        layer,
                        cue_range,
                        start=start,
                        increment=increment,
                        include_unnumbered=include_unnumbered,
                    ):
                        kind = 'group' if isinstance(item, CueGroup) else 'cue'
                        changes.append((kind, item.id, old, new, before[(kind, item.id)]))
                if apply:
                    s.commit()
            return changes

        def open_renumber_dialog():
            """Dialog to renumber a range of cues on one layer, with a preview before applying."""
            show_id = selected_show_id['v']
            if show_id is None:
                ui.notify('Select a show first.', type='warning')
                return
            layer_names = list(layers_by_name['v'])
            if not layer_names:
                ui.notify('Add a layer first (Layers button).', type='warning')
                return

            def _read(apply: bool):
                """Run the renumber with the dialog's inputs; ValueError goes to the caller."""
                if from_in.value is None or to_in.value is None:
                    raise ValueError('Enter the first and last cue number of the range.')
                start = None if start_in.value is None else int(start_in.value)
                return _run_renumber(
                    layer_in.value,
                    (int(from_in.value), int(to_in.value)),
                    start,
                    int(inc_in.value or 1),
                    unnumbered_in.value,
                    apply,
                )

            def _preview():
                preview_box.clear()
                try:
                    changes = _read(apply=False)
                except ValueError as exc:
                    with preview_box:
                        ui.label(str(exc)).classes('text-negative')
                    return
                with preview_box:
                    if not changes:
                        ui.label('No cues in that range.')
                        return
                    ui.label(f'{len(changes)} cues will change:')
                    with ui.scroll_area().style('height: 240px'):
                        for _, _, old, new, _before in changes:
                            ui.label(f'{old or "(unnumbered)"} → {new}')

            def _apply():
                try:
                    changes = _read(apply=True)
                except ValueError as exc:
                    ui.notify(str(exc), type='negative')
                    return
                dlg.close()
                if not changes:
                    ui.notify('No cues in that range.', type='warning')
                    return
                layer = layer_in.value
                for kind, iid, _old, _new, before in changes:
                    if kind == 'cue':
                        _notify_cue('update', iid, before)
                    else:
                        _notify_group('update', _group_snapshot(iid), before)

                def _restore(items=[(kind, iid, before) for kind, iid, _, _, before in changes]):
                    for kind, iid, before in items:
                        if kind == 'cue':
                            _update_cue(iid, False, number=before['number'])
                        else:
                            _update_group(iid, number=before['number'])

                _push_undo(f'Renumber {layer} ({len(changes)} cues)', _restore)
                ui.notify(f'Renumbered {len(changes)} cues')
                refresh_all()

            dlg = ui.dialog()
            with dlg, ui.card().style('min-width: 480px'):
                ui.label('Renumber cues').classes('text-h6')
                layer_in = ui.select(layer_names, value=selected_layer['v'], label='Layer').classes('w-full')
                with ui.row().classes('w-full items-end gap-2'):
                    from_in = ui.number('From', precision=0).classes('flex-1')
                    to_in = ui.number('To', precision=0).classes('flex-1')
                with ui.row().classes('w-full items-end gap-2'):
                    start_in = ui.number('Start at (blank = first in range)', precision=0).classes('flex-1')
                    inc_in = ui.number('Increment', value=1, min=1, precision=0).classes('flex-1')
                unnumbered_in = ui.checkbox('Include unnumbered cues').tooltip(
                    'Number every unnumbered cue on this layer that has a script position, '
                    'in script order. Cues with no script position are skipped.'
                )
                ui.separator()
                preview_box = ui.column().classes('w-full gap-1')
                with ui.row().classes('w-full justify-end gap-2'):
                    ui.button('Cancel', on_click=dlg.close).props('flat')
                    ui.button('Preview', on_click=_preview).props('outline')
                    ui.button('Apply', on_click=_apply).props('unelevated')
            dlg.open()

        def set_layer(layer: str):
            selected_layer['v'] = layer
            render_toolbar()

        def toggle_details():
            show_details['v'] = not show_details['v']
            nicegui_app.storage.user['show_details'] = show_details['v']
            render_toolbar()
            render_script_content()

        def toggle_auto_number():
            auto_number['v'] = not auto_number['v']
            nicegui_app.storage.user['auto_number_groups'] = auto_number['v']
            render_toolbar()

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
                ui.button('Renumber', icon='format_list_numbered', on_click=open_renumber_dialog).props('flat dense')
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
                    'Group',
                    icon='crop_free',
                    on_click=toggle_group_mode,
                    color='white' if group_mode['v'] else None,
                ).props('dense unelevated' if group_mode['v'] else 'dense outline').tooltip(
                    'Drag a box over cues to group them' if not group_mode['v'] else 'Group mode on: drag a box over cues'
                )
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
                ui.button(
                    'Auto-number groups',
                    icon='format_list_numbered',
                    on_click=toggle_auto_number,
                    color='white' if auto_number['v'] else None,
                ).props('dense unelevated' if auto_number['v'] else 'dense outline')
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
                        script_content_ref['el'] = (
                            ui.column().classes('w-full p-4').props('id=sr-script-host').style('position: relative;')
                        )
                        script_content_ref['el'].on(
                            'srselect', _on_group_select, js_handler='(e) => emit({ids: e.detail})'
                        )
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
