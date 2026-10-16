"""Cue layers: per-show named layers with a colour and a backend type.

Layers live in the ``layers`` table (``models.Layer``); ``Cue.layer`` holds the
layer name. The type registry combines built-in types with plugin-registered
ones via ``showrunner_get_layer_types``. Functions that need plugins take ``pm``
(a pluggy PluginManager, or ``None`` for built-ins only).
"""

import re
from typing import Iterator

from loguru import logger
from sqlmodel import Session, select

from .models import Cue, CueList, Layer

# Seeded into a show that has no layers yet: (name, colour, type).
DEFAULT_LAYERS = [
    ("Lights", "orange", "lights"),
    ("Sound", "blue", "sound"),
    ("Video", "purple", "video"),
    ("Audio", "cyan", "audio"),
    ("Stage", "green", "stage"),
]

BUILTIN_LAYER_TYPES = {
    "generic": {
        "label": "Generic",
        "description": "No specific behaviour",
        "color": "grey",
    },
    "lights": {"label": "Lights", "description": "Lighting cues", "color": "orange"},
    "sound": {"label": "Sound", "description": "Sound cues", "color": "blue"},
    "video": {"label": "Video", "description": "Video cues", "color": "purple"},
    "audio": {"label": "Audio", "description": "Audio cues", "color": "cyan"},
    "stage": {"label": "Stage", "description": "Stage cues", "color": "green"},
}

# RGB for PDF export, keyed by Quasar colour name. Unknown names fall back to grey.
COLOR_RGB = {
    "orange": (255, 165, 0),
    "blue": (50, 100, 255),
    "purple": (160, 32, 240),
    "cyan": (0, 200, 200),
    "green": (0, 180, 0),
    "pink": (233, 30, 99),
    "grey": (128, 128, 128),
}

_COLOR_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def is_color(value) -> bool:
    """True for a plain Quasar colour name (e.g. ``orange``, ``deep-purple``)."""
    return isinstance(value, str) and bool(_COLOR_RE.match(value))


def hook_results(pm, hook_name: str, **kwargs) -> Iterator:
    """Yield each plugin's return value for *hook_name*; a failing plugin is logged and skipped."""
    if pm is None:
        return
    for impl in getattr(pm.hook, hook_name).get_hookimpls():
        try:
            yield impl.function(**{k: kwargs[k] for k in impl.argnames})
        except Exception:
            logger.exception(f"{hook_name} failed in plugin {impl.plugin_name}")


def _as_list(result) -> list[dict]:
    if not result:
        return []
    return [result] if isinstance(result, dict) else list(result)


def get_layer_types(pm=None) -> dict[str, dict]:
    """Return ``{type: {type, label, description, color}}`` for built-in and plugin types.

    Plugins are visited in registration order (pluggy's ``get_hookimpls``); the first
    registration of a type wins, later duplicates are logged and ignored.
    """
    types = {k: {"type": k, **v} for k, v in BUILTIN_LAYER_TYPES.items()}
    for result in hook_results(pm, "showrunner_get_layer_types"):
        for entry in _as_list(result):
            key = entry.get("type")
            if not key:
                continue
            if key in types:
                logger.warning(
                    f"layer type {key!r} already registered; ignoring duplicate"
                )
                continue
            types[key] = {"label": key, "description": "", "color": "grey", **entry}
    return types


def validate_type(type_: str, pm=None) -> None:
    """Raise ``ValueError`` unless *type_* is in the registry."""
    if type_ not in get_layer_types(pm):
        raise ValueError(f"unknown layer type {type_!r}")


def _layer_dict(layer: Layer, types: dict) -> dict:
    return {
        "id": layer.id,
        "name": layer.name,
        "color": layer.color,
        "type": layer.type,
        "sequence": layer.sequence,
        # False when the type's plugin is gone; the layer is kept and treated as generic.
        "available": layer.type in types,
    }


def get_layers(session: Session, show_id: int, pm=None) -> list[dict]:
    """Layers of a show, ordered by ``sequence``."""
    types = get_layer_types(pm)
    rows = session.exec(
        select(Layer).where(Layer.show_id == show_id).order_by(Layer.sequence, Layer.id)
    ).all()
    return [_layer_dict(r, types) for r in rows]


def layer_color(session: Session, show_id: int, name: str | None) -> str:
    """Colour name for a layer name in a show; ``grey`` when unknown."""
    layer = session.exec(
        select(Layer).where(Layer.show_id == show_id, Layer.name == name)
    ).first()
    return layer.color if layer else "grey"


def ensure_layers(session: Session, show_id: int, pm=None) -> None:
    """Seed a show with the defaults if it has no layers, then add plugin layers not yet present.

    Never changes an existing row. A plugin layer with an unregistered type or an
    invalid colour is logged and skipped.
    """
    rows = session.exec(select(Layer).where(Layer.show_id == show_id)).all()
    names = {r.name for r in rows}
    seq = max((r.sequence for r in rows), default=-1) + 1
    if not rows:
        for name, color, type_ in DEFAULT_LAYERS:
            session.add(
                Layer(show_id=show_id, name=name, color=color, type=type_, sequence=seq)
            )
            names.add(name)
            seq += 1

    types = get_layer_types(pm)
    for result in hook_results(pm, "showrunner_get_layers"):
        for entry in _as_list(result):
            name = entry.get("name")
            if not name or name in names:
                continue
            if entry.get("type") not in types or not is_color(
                entry.get("color", "grey")
            ):
                logger.warning(
                    f"skipping plugin layer {entry!r}: unknown type or colour"
                )
                continue
            session.add(
                Layer(
                    show_id=show_id,
                    name=name,
                    color=entry.get("color", "grey"),
                    type=entry["type"],
                    sequence=seq,
                )
            )
            names.add(name)
            seq += 1
    session.commit()


def _cues_in_show(show_id: int):
    return select(Cue).where(
        Cue.cue_list_id.in_(select(CueList.id).where(CueList.show_id == show_id))
    )


def add_layer(
    session: Session, show_id: int, name: str, type_: str, color: str, pm=None
) -> Layer:
    """Create a layer at the end of the show's order.

    Raises ``ValueError`` on bad input or a duplicate name.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("layer name is required")
    validate_type(type_, pm)
    if not is_color(color):
        raise ValueError(f"invalid colour {color!r}")
    if session.exec(
        select(Layer).where(Layer.show_id == show_id, Layer.name == name)
    ).first():
        raise ValueError(f"layer {name!r} already exists")
    last = session.exec(select(Layer.sequence).where(Layer.show_id == show_id)).all()
    layer = Layer(
        show_id=show_id,
        name=name,
        type=type_,
        color=color,
        sequence=max(last, default=-1) + 1,
    )
    session.add(layer)
    session.commit()
    session.refresh(layer)
    return layer


def update_layer(session: Session, layer: Layer, pm=None, **changes) -> Layer:
    """Change ``name``, ``type``, ``color`` or ``sequence``. A rename also renames cues using it.

    Raises ``ValueError`` on bad input or a duplicate name; nothing is changed in that case.
    """
    if "type" in changes:
        validate_type(changes["type"], pm)
    if "color" in changes and not is_color(changes["color"]):
        raise ValueError(f'invalid colour {changes["color"]!r}')
    new_name = changes.get("name")
    if new_name is not None:
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("layer name is required")
        clash = session.exec(
            select(Layer).where(
                Layer.show_id == layer.show_id,
                Layer.name == new_name,
                Layer.id != layer.id,
            )
        ).first()
        if clash:
            raise ValueError(f"layer {new_name!r} already exists")
        old_name = layer.name
        for cue in session.exec(
            _cues_in_show(layer.show_id).where(Cue.layer == old_name)
        ).all():
            cue.layer = new_name
        layer.name = new_name
    for field in ("type", "color", "sequence"):
        if field in changes:
            setattr(layer, field, changes[field])
    session.add(layer)
    session.commit()
    session.refresh(layer)
    return layer


def delete_layer(session: Session, layer: Layer) -> None:
    """Delete a layer. Raises ``ValueError`` while cues still use it."""
    used = session.exec(
        _cues_in_show(layer.show_id).where(Cue.layer == layer.name)
    ).all()
    if used:
        raise ValueError(f"layer {layer.name!r} is used by {len(used)} cue(s)")
    session.delete(layer)
    session.commit()
