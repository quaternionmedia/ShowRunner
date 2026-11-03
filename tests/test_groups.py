"""Cue groups: nesting, membership, delete reparenting, and cue delete."""

import pytest
from sqlmodel import select

from showrunner.groups import (
    container_for,
    create_group,
    delete_group,
    group_tree,
    next_number,
    set_members,
    update_group,
)
from showrunner.models import Cue, CueGroup, CueList, Show


def _setup(db, n=4):
    with db.session() as s:
        show = Show(name="S")
        s.add(show)
        s.flush()
        cl = CueList(show_id=show.id, name="Main")
        s.add(cl)
        s.flush()
        cues = [Cue(cue_list_id=cl.id, number=str(i + 1), layer="Lights") for i in range(n)]
        s.add_all(cues)
        s.commit()
        return cl.id, [c.id for c in cues]


def test_create_group_and_tree(db):
    cl, ids = _setup(db)
    with db.session() as s:
        g = create_group(s, cl, ids[:2], name="Act 1", color="red")
        s.commit()
        gid = g.id
    with db.session() as s:
        [node] = group_tree(s, cl)
    assert node["id"] == gid and node["name"] == "Act 1" and node["color"] == "red"
    assert node["cue_ids"] == ids[:2] and node["member_ids"] == ids[:2]


def test_nested_group_counts_in_parent_members(db):
    cl, ids = _setup(db)
    with db.session() as s:
        outer = create_group(s, cl, ids[:3], name="outer")
        s.commit()
        outer_id = outer.id
    with db.session() as s:
        assert container_for(s, cl, ids[:2]) == outer_id
        inner = create_group(s, cl, ids[:2], name="inner", parent_id=outer_id)
        s.commit()
        inner_id = inner.id
    with db.session() as s:
        tree = {g["id"]: g for g in group_tree(s, cl)}
    assert tree[inner_id]["cue_ids"] == ids[:2]
    assert tree[outer_id]["cue_ids"] == [ids[2]]
    assert sorted(tree[outer_id]["member_ids"]) == sorted(ids[:3])


def test_set_members_drops_move_to_parent(db):
    cl, ids = _setup(db)
    with db.session() as s:
        outer = create_group(s, cl, ids)
        s.flush()
        inner = create_group(s, cl, ids[:2], parent_id=outer.id)
        s.commit()
        outer_id, inner_id = outer.id, inner.id
    with db.session() as s:
        set_members(s, inner_id, [ids[0]])
        s.commit()
    with db.session() as s:
        tree = {g["id"]: g for g in group_tree(s, cl)}
    assert tree[inner_id]["cue_ids"] == [ids[0]]
    assert ids[1] in tree[outer_id]["cue_ids"]


def test_delete_group_reparents_cues_and_children(db):
    cl, ids = _setup(db)
    with db.session() as s:
        outer = create_group(s, cl, ids)
        s.flush()
        mid = create_group(s, cl, ids[:3], parent_id=outer.id)
        s.flush()
        inner = create_group(s, cl, ids[:1], parent_id=mid.id)
        s.commit()
        outer_id, mid_id, inner_id = outer.id, mid.id, inner.id
    with db.session() as s:
        delete_group(s, mid_id)
        s.commit()
    with db.session() as s:
        tree = {g["id"]: g for g in group_tree(s, cl)}
    assert mid_id not in tree
    assert tree[inner_id]["parent_id"] == outer_id
    assert sorted(tree[outer_id]["cue_ids"]) == sorted(
        ids[1:]
    )  # mid's own cues and outer's own cue
    assert tree[inner_id]["cue_ids"] == [ids[0]]


def test_delete_top_level_group_ungroups_cues(db):
    cl, ids = _setup(db)
    with db.session() as s:
        g = create_group(s, cl, ids[:2])
        s.commit()
        gid = g.id
    with db.session() as s:
        delete_group(s, gid)
        s.commit()
    with db.session() as s:
        assert all(c.group_id is None for c in s.exec(select(Cue)).all())
        assert group_tree(s, cl) == []


def test_deleting_cue_removes_it_from_group(db):
    cl, ids = _setup(db)
    with db.session() as s:
        g = create_group(s, cl, ids[:2], name="g")
        s.commit()
        gid = g.id
    with db.session() as s:
        s.delete(s.get(Cue, ids[0]))
        s.commit()
    with db.session() as s:
        [node] = group_tree(s, cl)
    assert node["id"] == gid and node["cue_ids"] == [ids[1]]


def test_update_group_name_and_colour(db):
    cl, ids = _setup(db)
    with db.session() as s:
        g = create_group(s, cl, ids[:1])
        s.commit()
        gid = g.id
    with db.session() as s:
        update_group(s, gid, name="Renamed", color="blue")
        s.commit()
    with db.session() as s:
        [node] = group_tree(s, cl)
    assert (node["name"], node["color"]) == ("Renamed", "blue")


def test_group_number_is_unique_per_layer_and_renumbers_with_cues(db):
    """A group's number shares the layer's number space with cues, and renumber moves it too."""
    from showrunner.renumber import renumber_cues

    cl, ids = _setup(db, n=3)  # cues numbered 1, 2, 3 on Lights
    with db.session() as s:
        with pytest.raises(ValueError, match='already used'):
            create_group(s, cl, ids[:1], layer="Lights", number="2")
        s.rollback()
        g = create_group(s, cl, ids[:1], layer="Lights", number="10")
        g_id = g.id
        s.commit()
    with db.session() as s:
        changes = renumber_cues(s, cl, "Lights", (10, 10), start=20)
        s.commit()
    assert [(isinstance(item, CueGroup), old, new)
            for item, old, new in changes] == [(True, "10", "20")]
    with db.session() as s:
        assert s.get(CueGroup, g_id).number == "20"


def test_next_number_follows_the_previous_item_of_the_layer_in_script(db):
    with db.session() as s:
        show = Show(name="S")
        s.add(show)
        s.flush()
        cl = CueList(show_id=show.id, name="Main")
        s.add(cl)
        s.flush()
        # Lights 1 @line 1, Lights 2 @line 3, Sound 9 @line 2 (other layer), new cue @line 4
        s.add(Cue(cue_list_id=cl.id, number="1", layer="Lights", script_line=1))
        s.add(Cue(cue_list_id=cl.id, number="9", layer="Sound", script_line=2))
        s.add(Cue(cue_list_id=cl.id, number="2", layer="Lights", script_line=3))
        new = Cue(cue_list_id=cl.id, number=None, layer="Lights", script_line=4)
        s.add(new)
        s.flush()
        new_id = new.id
        cl_id = cl.id
        s.commit()
    with db.session() as s:
        assert next_number(s, cl_id, "Lights", [new_id]) == "3"
        assert next_number(s, cl_id, "Sound", [new_id]) == "10"


def test_next_number_skips_taken_numbers_and_defaults_to_one(db):
    with db.session() as s:
        show = Show(name="S")
        s.add(show)
        s.flush()
        cl = CueList(show_id=show.id, name="Main")
        s.add(cl)
        s.flush()
        first = Cue(cue_list_id=cl.id, number=None, layer="Lights", script_line=1)
        s.add(Cue(cue_list_id=cl.id, number="2", layer="Lights", script_line=5))  # later, taken
        s.add(first)
        s.flush()
        first_id = first.id
        cl_id = cl.id
        s.commit()
    with db.session() as s:
        assert next_number(s, cl_id, "Lights", [first_id]) == "1"
        create_group(s, cl_id, [first_id], layer="Lights", number="1")
        s.commit()
    with db.session() as s:
        other = s.exec(select(Cue).where(Cue.number == "2")).one()
        assert next_number(s, cl_id, "Lights", [other.id]) == "3"  # 1 and 2 are taken
