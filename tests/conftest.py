"""Shared pytest fixtures for the ShowRunner test suite.

Import these by name in any test file — pytest discovers them automatically.

Example::

    def test_something(db):
        with db.session() as s:
            s.add(Show(name="Hamlet"))
            s.commit()
"""

import pytest

from showrunner.database import ShowDatabase


@pytest.fixture()
def db(tmp_path):
    """Provide a fresh, isolated ShowDatabase for each test.

    The database file lives in pytest's ``tmp_path`` directory, which is
    unique per test, so tests never share state.
    """
    database = ShowDatabase(tmp_path / "test.db")
    database.create_schema()
    yield database
    database.close()
