"""Local sync state and action resolution. No database.

get_action is the whole INSERT/UPDATE/DELETE/IGNORE decision, ported
from get_action.sql. Its quirks are load-bearing: they decide whether
a museum's media gets duplicated or dropped.
"""

from __future__ import annotations

import pytest

from courier_tms.media.state import SyncState

TABLE_OBJECTS = 108
TABLE_CONSTITUENTS = 23


@pytest.fixture
def state(tmp_path) -> SyncState:
    s = SyncState(str(tmp_path / "state.sqlite"))
    yield s
    s.close()


def at(state: SyncState, record_id: int, when: str) -> None:
    """Pin a log row's timestamp so ordering assertions are exact."""
    state.conn.execute(
        "UPDATE record_log SET created_date = ? WHERE record_id = ?", (when, record_id)
    )
    state.conn.commit()


def last_record_id(state: SyncState) -> int:
    return state.conn.execute("SELECT MAX(record_id) FROM record_log").fetchone()[0]


# ── the four actions ─────────────────────────────────────────────────


def test_unseen_asset_on_a_live_feed_is_an_insert(state):
    assert state.get_action("a1", 1, TABLE_OBJECTS, deleted=False) == ("INSERT", None, None)


def test_unseen_asset_on_a_deleted_feed_is_ignored(state):
    """Nothing was ever written for this pair, so there is nothing to
    delete — and inventing a DELETE would target someone else's row."""
    assert state.get_action("a1", 1, TABLE_OBJECTS, deleted=True) == ("IGNORE", None, None)


def test_previously_inserted_asset_on_a_live_feed_is_an_update(state):
    state.log_record(1, 500, 1, "a1", TABLE_OBJECTS, "INSERT")
    action, media_master_id, last_update = state.get_action("a1", 1, TABLE_OBJECTS, deleted=False)
    assert action == "UPDATE"
    assert media_master_id == 500
    assert last_update is not None


def test_previously_inserted_asset_on_a_deleted_feed_is_a_delete(state):
    state.log_record(1, 500, 1, "a1", TABLE_OBJECTS, "INSERT")
    assert state.get_action("a1", 1, TABLE_OBJECTS, deleted=True) == ("DELETE", 500, None)


# ── scoping ──────────────────────────────────────────────────────────


def test_a_log_row_for_another_object_does_not_count(state):
    state.log_record(1, 500, 1, "a1", TABLE_OBJECTS, "INSERT")
    assert state.get_action("a1", 2, TABLE_OBJECTS, deleted=False)[0] == "INSERT"


def test_a_log_row_for_another_asset_does_not_count(state):
    state.log_record(1, 500, 1, "a1", TABLE_OBJECTS, "INSERT")
    assert state.get_action("a2", 1, TABLE_OBJECTS, deleted=False)[0] == "INSERT"


def test_a_log_row_for_another_table_does_not_count(state):
    """The xref tables are polymorphic; the same asset can hang off an
    object and a constituent independently."""
    state.log_record(1, 500, 1, "a1", TABLE_CONSTITUENTS, "INSERT")
    assert state.get_action("a1", 1, TABLE_OBJECTS, deleted=False)[0] == "INSERT"


# ── quirks carried over from get_action.sql ──────────────────────────


def test_only_insert_rows_resolve_the_media_master_id(state):
    """UPDATE rows carry a media_master_id too, but the original only
    ever read it off INSERT rows."""
    state.log_record(1, 500, 1, "a1", TABLE_OBJECTS, "INSERT")
    state.log_record(2, 999, 1, "a1", TABLE_OBJECTS, "UPDATE")
    assert state.get_action("a1", 1, TABLE_OBJECTS, deleted=False)[1] == 500


def test_the_most_recent_insert_wins_when_an_asset_was_reinserted(state):
    state.log_record(1, 500, 1, "a1", TABLE_OBJECTS, "INSERT")
    state.log_record(2, 600, 1, "a1", TABLE_OBJECTS, "INSERT")
    assert state.get_action("a1", 1, TABLE_OBJECTS, deleted=False)[1] == 600


def test_a_logged_delete_does_not_retire_the_pair(state):
    """Faithful to the original: logged DELETE rows are not consulted,
    so a pair that was deleted and reappears resolves to UPDATE against
    the old MediaMasterID rather than to a fresh INSERT."""
    state.log_record(1, 500, 1, "a1", TABLE_OBJECTS, "INSERT")
    state.log_record(1, 500, 1, "a1", TABLE_OBJECTS, "DELETE")
    action, media_master_id, _ = state.get_action("a1", 1, TABLE_OBJECTS, deleted=False)
    assert (action, media_master_id) == ("UPDATE", 500)


def test_last_update_ignores_delete_rows(state):
    state.log_record(1, 500, 1, "a1", TABLE_OBJECTS, "INSERT")
    at(state, last_record_id(state), "2026-01-01T00:00:00+00:00")
    state.log_record(1, 500, 1, "a1", TABLE_OBJECTS, "DELETE")
    at(state, last_record_id(state), "2099-01-01T00:00:00+00:00")

    _, _, last_update = state.get_action("a1", 1, TABLE_OBJECTS, deleted=False)
    assert last_update == "2026-01-01T00:00:00+00:00"


def test_last_update_is_the_newest_non_delete_write(state):
    state.log_record(1, 500, 1, "a1", TABLE_OBJECTS, "INSERT")
    at(state, last_record_id(state), "2026-01-01T00:00:00+00:00")
    state.log_record(2, 500, 1, "a1", TABLE_OBJECTS, "UPDATE")
    at(state, last_record_id(state), "2026-06-01T00:00:00+00:00")

    _, _, last_update = state.get_action("a1", 1, TABLE_OBJECTS, deleted=False)
    assert last_update == "2026-06-01T00:00:00+00:00"


# ── process and error logs ───────────────────────────────────────────


def test_process_log_records_a_run(state):
    process_id = state.start_process("PROCESS_MEDIA")
    state.finish_process(process_id, total=3, succeeded=2, failed=1, info="note")
    row = state.conn.execute(
        "SELECT process_name, records_total, records_succeeded, records_failed, "
        "additional_info, end_date FROM process_log WHERE process_id = ?", (process_id,)
    ).fetchone()
    assert row[:5] == ("PROCESS_MEDIA", 3, 2, 1, "note")
    assert row[5] is not None


def test_error_log_keeps_the_failing_pair(state):
    process_id = state.start_process("PROCESS_MEDIA")
    state.log_error(process_id, "boom", 42, "a1")
    row = state.conn.execute(
        "SELECT tms_record_id, asset_id, error_msg FROM error_log"
    ).fetchone()
    assert row == (42, "a1", "boom")


def test_state_survives_reopening(tmp_path):
    """The state file is what makes a second run idempotent; if it did
    not persist, every run would re-insert the whole feed."""
    path = str(tmp_path / "state.sqlite")
    first = SyncState(path)
    first.log_record(1, 500, 1, "a1", TABLE_OBJECTS, "INSERT")
    first.close()

    second = SyncState(path)
    try:
        assert second.get_action("a1", 1, TABLE_OBJECTS, deleted=False)[1] == 500
    finally:
        second.close()
