"""Tests del START diario y el kill switch del trading real (usuario 2026-08-09)."""

from __future__ import annotations

from datetime import date

from options_advisor.storage import db
from options_advisor.storage import repository as repo


def _conn():
    return db.connect(":memory:")


def test_not_armed_by_default():
    conn = _conn()
    assert repo.is_live_armed(conn, date(2026, 8, 10)) is False


def test_arm_today_only_valid_today():
    conn = _conn()
    repo.arm_live_today(conn, date(2026, 8, 10))
    assert repo.is_live_armed(conn, date(2026, 8, 10)) is True
    # Al día siguiente ya NO está armado: hay que dar START de nuevo.
    assert repo.is_live_armed(conn, date(2026, 8, 11)) is False


def test_disarm():
    conn = _conn()
    repo.arm_live_today(conn, date(2026, 8, 10))
    repo.disarm_live(conn)
    assert repo.is_live_armed(conn, date(2026, 8, 10)) is False


def test_kill_switch():
    conn = _conn()
    assert repo.is_live_kill_switch(conn) is False
    repo.set_live_kill_switch(conn, True)
    assert repo.is_live_kill_switch(conn) is True
    repo.set_live_kill_switch(conn, False)
    assert repo.is_live_kill_switch(conn) is False
