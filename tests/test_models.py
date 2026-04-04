"""Tests for ShowRunner model __str__ methods and additional model behaviour."""

from showrunner.models import Actor, Config, Cue, CueList, CueLog, Script, Show

# ---------------------------------------------------------------------------
# Show.__str__
# ---------------------------------------------------------------------------


def test_show_str_name_only():
    show = Show(name='Hamlet')
    assert str(show) == 'Hamlet'


def test_show_str_name_and_venue():
    show = Show(name='Hamlet', venue='Globe Theatre')
    assert str(show) == 'Hamlet @ Globe Theatre'


def test_show_str_no_venue_no_at_sign():
    show = Show(name='Wicked')
    assert '@' not in str(show)


# ---------------------------------------------------------------------------
# Script.__str__
# ---------------------------------------------------------------------------


def test_script_str_with_title():
    script = Script(show_id=1, title='Act One')
    assert str(script) == 'Act One'


def test_script_str_empty_title_falls_back():
    # id is None before being persisted
    script = Script(show_id=1, title='')
    assert str(script) == 'Script None'


# ---------------------------------------------------------------------------
# CueList.__str__
# ---------------------------------------------------------------------------


def test_cuelist_str_with_name():
    cl = CueList(show_id=1, name='Main')
    assert str(cl) == 'Main'


def test_cuelist_str_empty_name_falls_back():
    cl = CueList(show_id=1, name='')
    assert str(cl) == 'CueList None'


# ---------------------------------------------------------------------------
# Cue.__str__
# ---------------------------------------------------------------------------


def test_cue_str_number_only():
    cue = Cue(cue_list_id=1, number=10, point=0)
    assert str(cue) == '10'


def test_cue_str_number_and_point():
    cue = Cue(cue_list_id=1, number=10, point=5)
    assert str(cue) == '10.5'


def test_cue_str_number_point_and_name():
    cue = Cue(cue_list_id=1, number=10, point=5, name='Lights Up')
    assert str(cue) == '10.5 Lights Up'


def test_cue_str_zero_point_omitted():
    """point=0 is falsy — should not appear in the label."""
    cue = Cue(cue_list_id=1, number=5, point=0, name='Go')
    assert str(cue) == '5 Go'


def test_cue_str_name_only_no_point():
    cue = Cue(cue_list_id=1, number=1, name='Curtain')
    assert str(cue) == '1 Curtain'


# ---------------------------------------------------------------------------
# Actor.__str__
# ---------------------------------------------------------------------------


def test_actor_str_name_only():
    actor = Actor(show_id=1, name='Jane')
    assert str(actor) == 'Jane'


def test_actor_str_name_and_role():
    actor = Actor(show_id=1, name='Jane', role='Lead')
    assert str(actor) == 'Jane (Lead)'


def test_actor_str_no_role_no_parens():
    actor = Actor(show_id=1, name='Bob')
    assert '(' not in str(actor)


# ---------------------------------------------------------------------------
# CueLog.__str__
# ---------------------------------------------------------------------------


def test_cuelog_str_contains_log():
    log = CueLog(show_id=1)
    assert 'Log' in str(log)


def test_cuelog_str_contains_at():
    log = CueLog(show_id=1)
    assert '@' in str(log)


# ---------------------------------------------------------------------------
# Config.__str__
# ---------------------------------------------------------------------------


def test_config_str():
    cfg = Config(show_id=1, key='theme', value='dark')
    assert str(cfg) == 'Config theme=dark'


def test_config_str_null_value():
    cfg = Config(show_id=1, key='empty')
    assert str(cfg) == 'Config empty=None'


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------


def test_create_and_retrieve_config(db):
    with db.session() as s:
        show = Show(name='Config Test')
        s.add(show)
        s.commit()
        s.refresh(show)

        cfg = Config(show_id=show.id, key='stage_ip', value='192.168.1.1')
        s.add(cfg)
        s.commit()
        s.refresh(cfg)
        cfg_id = cfg.id
        show_id = show.id

    with db.session() as s:
        result = s.get(Config, cfg_id)
        assert result.key == 'stage_ip'
        assert result.value == '192.168.1.1'
        assert result.show_id == show_id


def test_config_value_can_be_null(db):
    with db.session() as s:
        show = Show(name='Null Val Test')
        s.add(show)
        s.commit()
        s.refresh(show)

        cfg = Config(show_id=show.id, key='empty_flag')
        s.add(cfg)
        s.commit()
        s.refresh(cfg)
        cfg_id = cfg.id

    with db.session() as s:
        result = s.get(Config, cfg_id)
        assert result.value is None


def test_config_str_repr(db):
    with db.session() as s:
        show = Show(name='Repr Test')
        s.add(show)
        s.commit()
        s.refresh(show)

        cfg = Config(show_id=show.id, key='brightness', value='75')
        s.add(cfg)
        s.commit()
        s.refresh(cfg)

    assert str(cfg) == 'Config brightness=75'


# ---------------------------------------------------------------------------
# Actor active flag
# ---------------------------------------------------------------------------


def test_actor_active_defaults_to_true(db):
    with db.session() as s:
        show = Show(name='Actor Flag Test')
        s.add(show)
        s.commit()
        s.refresh(show)

        actor = Actor(show_id=show.id, name='Dave')
        s.add(actor)
        s.commit()
        s.refresh(actor)
        actor_id = actor.id

    with db.session() as s:
        result = s.get(Actor, actor_id)
        assert result.active is True


def test_actor_can_be_set_inactive(db):
    with db.session() as s:
        show = Show(name='Inactive Actor Test')
        s.add(show)
        s.commit()
        s.refresh(show)

        actor = Actor(show_id=show.id, name='Dave', active=False)
        s.add(actor)
        s.commit()
        s.refresh(actor)
        actor_id = actor.id

    with db.session() as s:
        result = s.get(Actor, actor_id)
        assert result.active is False


# ---------------------------------------------------------------------------
# CueLog duration_ms
# ---------------------------------------------------------------------------


def test_cuelog_duration_ms_stored(db):
    with db.session() as s:
        show = Show(name='Duration Test')
        s.add(show)
        s.commit()
        s.refresh(show)

        log = CueLog(show_id=show.id, duration_ms=1500)
        s.add(log)
        s.commit()
        s.refresh(log)
        log_id = log.id

    with db.session() as s:
        result = s.get(CueLog, log_id)
        assert result.duration_ms == 1500


def test_cuelog_duration_ms_defaults_to_none(db):
    with db.session() as s:
        show = Show(name='No Duration Test')
        s.add(show)
        s.commit()
        s.refresh(show)

        log = CueLog(show_id=show.id)
        s.add(log)
        s.commit()
        s.refresh(log)
        log_id = log.id

    with db.session() as s:
        result = s.get(CueLog, log_id)
        assert result.duration_ms is None
