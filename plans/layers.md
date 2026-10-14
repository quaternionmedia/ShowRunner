# Plan: user-customizable layers + fixes from PR #41 review

## Context

PR #41 (`layers` branch) added a hard-coded `Mix` layer (this work removes it from core; see below). Layers are a fixed list duplicated in
five places, so adding one means touching all of them (review item #5 shows we missed some).
Goal: layers become user data (**name, color, backend type**) stored in a new `layers` table,
creatable by users (UI + CLI) and by plugins (hook). The other review issues are fixed on the
same branch (PR #41 branch `layers`).

Decisions (from user):
- Layer = name + color + backend `type`. `type` is a text field validated against a **type registry** that plugins extend; a plugin may attach custom behaviour to a type it registers.
- Stored in a new `layers` table (per show).
- Created by users **and** plugins.
- `Cue.layer` stays a name string (no cue migration); PDF colours come from DB layers.
- Script viewer gets a cue-list filter.

## Where layers live today (all hard-coded)

- `src/showrunner/plugins/scripter.py:22-31` — `LAYERS`, `LAYER_COLORS` (toolbar :932, cue editor select :331, badges :292/:367, add-cue :999)
- `src/showrunner/plugins/printer.py:117-120,397-399` — colour from `pdf_layout.toml [cues.colors]`, grey fallback; default `'Lights'`; label = first letter
- `src/showrunner/pdf_layout.toml:133-138` — RGB per layer
- `src/showrunner/cli.py:663-664` — `--layer` help text
- `src/showrunner/models.py:83,94` — comments only; `Cue.layer: str | None`
- `src/showrunner/plugins/admin.py:85-91` — Cue columns (add a Layer view)
- docs: `docs/cookbook/cues.md`, `docs/cookbook/pdf-export.md`, `docs/ref/models.md`

## Approach

### Data model
`Layer(SQLModel, table=True)` in `src/showrunner/models.py`:
`id, show_id (FK shows.id), name, color (Quasar colour name, default 'grey'), type (str, default 'generic'), sequence (int)`; unique `(show_id, name)`. Add `Show.layers` relationship.
Alembic revision (after `2a50c1c7c24c`): create `layers`; seed per show the five defaults (Lights orange/Sound blue/Video purple/Audio cyan/Stage green, `type` = lowercase name) plus any distinct `cues.layer` value already used in that show that isn't a default, e.g. a `Mix` cue created on the `layers` branch (colour grey, `type='generic'`; the user or a plugin can retype it).

### Single source: `src/showrunner/layers.py` (new, small)
- `DEFAULT_LAYERS` (the five above); `BUILTIN_LAYER_TYPES` (`generic`, `lights`, `sound`, `video`, `audio`, `stage`). `mix` is deliberately **not** built in: it comes from a plugin.
- **Type registry**: `get_layer_types() -> dict[str, dict]` merges `BUILTIN_LAYER_TYPES` with `pm.hook.showrunner_get_layer_types()` results, keyed by `type`; each entry `{type, label, description, color}` (`color` = default for new layers of that type). Duplicate keys: first registration wins, later ones logged at WARNING. `validate_type(type)` raises `ValueError` for unknown types; used by the UI, CLI and API on create/update.
- `get_layers(show_id) -> list[dict]` (ordered by `sequence`), `layer_color(show_id, name)`.
- `ensure_layers(session, show_id, extra=())` — insert missing layers (defaults on a show with none; plugin-provided ones by name, never overwrite user edits).
- Called when a show is created (`plugins/db.py:60 create_show`, `cli.py` show-add) and on `showrunner_startup` for existing shows.

### Plugin hooks (`hookspecs.py`)
- `showrunner_get_layer_types(self) -> dict | list[dict] | None` — registers backend types: `{type, label, description, color}`. A plugin that owns a type then acts on cues whose layer has that type, using `get_layers(show_id)` to map layer name → type.
- **Canonical example (docs): a TheatreMix-style plugin extending layer types.** It implements `showrunner_get_layer_types` returning `{type: 'mix', label: 'Mix', description: 'TheatreMix cues', color: 'pink'}` and `showrunner_get_layers` returning `{name: 'Mix', color: 'pink', type: 'mix'}`, and handles cues whose layer type is `mix`. Used as the worked example in `docs/cookbook/cues.md` and `docs/cookbook/plugins-create.md`; also exercised by a test with a fake plugin. Core ships no `mix` anywhere.
- `showrunner_get_layers(self) -> dict | list[dict] | None` — each dict `{name, color, type}`; `type` must be registered. `ensure_layers` merges across plugins and skips (logs) entries with an unknown type.
- Existing layers whose type is no longer registered (plugin disabled/removed) are kept untouched and shown flagged `(unavailable)` in the UI; they behave as `generic` for colours/printing.

### Consumers
- **scripter.py**: delete `LAYERS`/`LAYER_COLORS`; load show layers when building the page; toolbar, cue-editor select, badges read them. Small "Layers" dialog: add / rename / recolour / retype / reorder / delete (delete blocked if cues use it). Type is a select fed by `get_layer_types()`; picking a type pre-fills its default colour. Renaming updates `Cue.layer` in the same transaction.
- **printer.py**: colour from `get_layers`; keep `[cues.colors]` in `pdf_layout.toml` only as an optional override (back-compat), drop the five built-in entries; fallback grey. Label = first letter (unchanged).
- **cli.py**: `sr layers list|add|set|remove <show_id>`, `sr layers types` (list registry); `cues add --layer` validated against the show's layers; help text no longer lists names.
- **db.py** routes: `GET /layer-types`, `GET /shows/{id}/layers`, `POST`, `PUT`, `DELETE` (thin, same style as existing routes; unknown type → 422).
- **admin.py**: `Layer` model view.

## Fixes for the other review items

1. `_load_cues`: if `s.get(CueList, id)` is `None` → return `[]`.
2. `_get_previews`: iterate `pm.hook.showrunner_script_previews.get_hookimpls()` and call each in its own try/except so one failing plugin only loses its own chips.
3. Duplicate cue ids across plugins: extend chip lists instead of overwrite; document in hookspec + cookbook.
4. Cue-list filter: select above the viewer (`All lists` default + each list); `_load_cues(show_id, cue_list_id=None)`; no-arg behaviour stays show-wide. Add-cue targets the selected list (first list if "All").
5. (this plan — layers table).
6. Chip `color`: accept only `[a-z0-9-]+`, else `white`.
7. Tests (new `tests/test_layers.py`, extend `tests/test_script_previews.py`): layer helpers + seeding idempotence, plugin-provided layer doesn't overwrite user edit, migration seeds from existing cue layers, registry merge + first-wins duplicate + unknown type rejected, plugin-provided type usable in a layer, orphaned type kept and treated as generic, rename cascades, delete blocked when in use, printer colour for custom layer, missing cue list, per-plugin error isolation, duplicate-id merge, colour whitelist, list filter query.

## Steps

- [x] `Layer` model + `Show.layers` + alembic revision (+ migration test)
- [x] `src/showrunner/layers.py` (defaults, `get_layers`, `ensure_layers`) + seed on show create / startup
- [x] `showrunner_get_layer_types` + `showrunner_get_layers` hookspecs; type registry + `validate_type` in `layers.py`
- [x] scripter: use DB layers, Layers dialog, cue-list filter, fixes #1–#3, #6
- [x] printer: DB colours, TOML override; trim `pdf_layout.toml`
- [x] CLI `sr layers …`, `--layer` validation; db.py routes; admin view
- [x] Remove `Mix` from core (`scripter.py` LAYERS/colours). Tests (#7) and docs (`cues.md` with the Mix-plugin example, `pdf-export.md`, `ref/models.md`, `plugin-architecture.md`, CONTRIBUTING hook table)
- [ ] Push to `layers` (updates PR #41); update PR body

## Verification

- `uv run pytest`; `uv run ruff check` on changed files (no new findings)
- `sr migration upgrade` on a copy of `show.db` (has real cues) — layers seeded, cues untouched
- Manual: `sr start`; in `/script` add a layer, recolour, rename (cues follow), delete in-use (blocked), filter by cue list; export PDF and confirm custom layer colour/label

## Open / noted

- Type registry is by name only (label, description, default colour). No per-type config schema or cue-handling callback yet; plugins act on their types through existing hooks.
- Not covered: per-layer permissions, per-layer MIDI/OSC routing.
