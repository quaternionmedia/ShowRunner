"""Cue groups: boxes around cues in one cue list, nestable through ``parent_id``.

Each cue belongs to at most one group (its innermost). A group's box covers its
own cues and those of its child groups. Functions flush but do not commit; the
caller owns the transaction.
"""

from sqlmodel import Session, select

from showrunner.models import Cue, CueGroup


def group_tree(session: Session, cue_list_id: int) -> list[dict]:
    """Every group in the list: ``cue_ids`` are its own cues, ``member_ids`` include descendants."""
    groups = session.exec(select(CueGroup).where(CueGroup.cue_list_id == cue_list_id)).all()
    direct: dict[int, list[int]] = {}
    for cue_id, group_id in session.exec(
        select(Cue.id, Cue.group_id).where(
            Cue.cue_list_id == cue_list_id, Cue.group_id.is_not(None)
        )
    ):
        direct.setdefault(group_id, []).append(cue_id)
    children: dict[int | None, list[int]] = {}
    for g in groups:
        children.setdefault(g.parent_id, []).append(g.id)

    def members(gid: int) -> list[int]:
        return direct.get(gid, []) + [m for child in children.get(gid, []) for m in members(child)]

    return [
        {
            "id": g.id,
            "name": g.name,
            "color": g.color,
            "parent_id": g.parent_id,
            "external_id": g.external_id,
            "cue_ids": direct.get(g.id, []),
            "member_ids": members(g.id),
        }
        for g in groups
    ]


def container_for(session: Session, cue_list_id: int, cue_ids: list[int]) -> int | None:
    """Smallest group already holding all of *cue_ids*: a new group for them nests inside it."""
    wanted = set(cue_ids)
    best = None
    for g in group_tree(session, cue_list_id):
        if wanted <= set(g["member_ids"]) and (best is None or len(g["member_ids"]) < best[1]):
            best = (g["id"], len(g["member_ids"]))
    return best[0] if best else None


def _cues_in_list(session: Session, cue_list_id: int, cue_ids: list[int]) -> list[Cue]:
    return list(
        session.exec(select(Cue).where(Cue.cue_list_id == cue_list_id, Cue.id.in_(cue_ids)))
    )


def create_group(
    session: Session,
    cue_list_id: int,
    cue_ids: list[int],
    name: str = "",
    color: str = "grey",
    parent_id: int | None = None,
) -> CueGroup:
    """New group holding *cue_ids* (taken from any group they were in before)."""
    group = CueGroup(cue_list_id=cue_list_id, name=name, color=color, parent_id=parent_id)
    session.add(group)
    session.flush()
    for cue in _cues_in_list(session, cue_list_id, cue_ids):
        cue.group_id = group.id
        session.add(cue)
    session.flush()
    return group


def update_group(
    session: Session, group_id: int, name: str | None = None, color: str | None = None
) -> CueGroup | None:
    """Rename and/or recolour a group."""
    group = session.get(CueGroup, group_id)
    if group is None:
        return None
    if name is not None:
        group.name = name
    if color is not None:
        group.color = color
    session.add(group)
    session.flush()
    return group


def set_members(session: Session, group_id: int, cue_ids: list[int]) -> None:
    """Make *cue_ids* the group's own cues. Cues dropped from it move up to its parent group."""
    group = session.get(CueGroup, group_id)
    if group is None:
        return
    keep = set(cue_ids)
    for cue in session.exec(select(Cue).where(Cue.group_id == group.id)):
        if cue.id not in keep:
            cue.group_id = group.parent_id
            session.add(cue)
    for cue in _cues_in_list(session, group.cue_list_id, list(keep)):
        cue.group_id = group.id
        session.add(cue)
    session.flush()


def delete_group(session: Session, group_id: int) -> None:
    """Remove a group. Its cues and child groups move to its parent (top level: ungrouped)."""
    group = session.get(CueGroup, group_id)
    if group is None:
        return
    for cue in session.exec(select(Cue).where(Cue.group_id == group.id)):
        cue.group_id = group.parent_id
        session.add(cue)
    for child in session.exec(select(CueGroup).where(CueGroup.parent_id == group.id)):
        child.parent_id = group.parent_id
        session.add(child)
    session.flush()
    session.delete(group)
    session.flush()


def group_depths(groups: list[dict]) -> dict[int, int]:
    """Nesting depth per group id (0 = top level), from the ``parent_id`` chain of *groups*."""
    parent = {g['id']: g['parent_id'] for g in groups}
    depths = {}
    for gid in parent:
        depth, p = 0, parent[gid]
        while p in parent and depth <= len(parent):
            depth, p = depth + 1, parent[p]
        depths[gid] = depth
    return depths
