"""
判据 · 协作三件小事（2026-10-19）
=================================

用户排期第 3 项：

> 协作三件小事——WAL + busy_timeout、锁租约、成员权限。1.5 天量级，
> 把"会丢数据"降到"会冲突"。

这里钉住四层：

  ① **连接层**：WAL、busy_timeout、写连接 `BEGIN IMMEDIATE`；
  ② **schema 3 迁移**：老库打开后自动补 `locked_until` / `role`，
     老数据一个不丢；
  ③ **锁租约**：别人的锁过期后可以接手（留审计），没过期照旧拒绝；
  ④ **成员权限**：viewer 只读、editor 能改、owner 能改角色；
     老库没有 owner 时允许第一次认领（否则迁移后权限死锁）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from storygraph.collab import collab_store as cs
from storygraph.story.beat_model import Beat, assign_identity


def _beats(n=2):
    beats = [Beat(seq=i + 1, label='ch01', speaker='e', text=f'第{i + 1}句')
             for i in range(n)]
    assign_identity(beats)
    return beats


def _seed(tmp_path, users=('alice', 'bob'), owner='alice'):
    db = tmp_path / 'collab.db'
    cs.init_db(db, users=users, owner=owner)
    seed = tmp_path / 'seed.xlsx'
    from storygraph.story.excel_export import export_beats_to_excel
    export_beats_to_excel(_beats(), seed)
    res = cs.push(db, seed, actor=owner or 'alice', force=True)
    assert res.get('added'), res
    return db, seed


class TestConnectionLayer:
    def test_wal_and_busy_timeout_are_on(self, tmp_path):
        db = tmp_path / 'c.db'
        cs.init_db(db, users=['a'])
        with cs.connect(db) as conn:
            assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
            assert int(conn.execute('PRAGMA busy_timeout').fetchone()[0]) == \
                cs.BUSY_TIMEOUT_MS

    def test_write_connection_takes_immediate_lock(self, tmp_path):
        db = tmp_path / 'c.db'
        cs.init_db(db, users=['a'])
        with cs.connect(db, write=True) as conn:
            assert conn.in_transaction is True

    def test_schema_version_is_3(self, tmp_path):
        db = tmp_path / 'c.db'
        cs.init_db(db, users=['a'])
        with cs.connect(db) as conn:
            got = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        assert got['value'] == '3'
        assert cs.SCHEMA_VERSION == 3


class TestMigration:
    def _old_db(self, tmp_path):
        """手搓一个 schema 2 老库（没有 locked_until / role）。"""
        db = tmp_path / 'old.db'
        conn = sqlite3.connect(db)
        conn.executescript('''
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE users (id TEXT PRIMARY KEY,
                            display_name TEXT NOT NULL DEFAULT '',
                            created_at TEXT NOT NULL);
        CREATE TABLE beats (line_id TEXT PRIMARY KEY,
                            label TEXT NOT NULL DEFAULT '',
                            seq INTEGER NOT NULL DEFAULT 0,
                            sheet TEXT NOT NULL DEFAULT '',
                            payload TEXT NOT NULL DEFAULT '{}',
                            content_hash TEXT NOT NULL DEFAULT '',
                            status TEXT NOT NULL DEFAULT 'draft',
                            revision INTEGER NOT NULL DEFAULT 1,
                            locked_by TEXT, locked_at TEXT,
                            updated_by TEXT NOT NULL DEFAULT '',
                            updated_at TEXT NOT NULL DEFAULT '',
                            deleted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE assets (asset_type TEXT NOT NULL, asset_id TEXT NOT NULL,
                             payload TEXT NOT NULL DEFAULT '{}',
                             updated_by TEXT NOT NULL DEFAULT '',
                             updated_at TEXT NOT NULL DEFAULT '',
                             PRIMARY KEY (asset_type, asset_id));
        CREATE TABLE file_blocks (label TEXT PRIMARY KEY,
                                  text TEXT NOT NULL DEFAULT '',
                                  updated_by TEXT NOT NULL DEFAULT '',
                                  updated_at TEXT NOT NULL DEFAULT '');
        CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT,
                            ts TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '',
                            action TEXT NOT NULL, line_id TEXT NOT NULL DEFAULT '',
                            detail TEXT NOT NULL DEFAULT '');
        INSERT INTO meta(key, value) VALUES('schema_version', '2');
        INSERT INTO users(id, display_name, created_at)
            VALUES('old', '老人', '2026-10-01T00:00:00');
        INSERT INTO beats(line_id, label, seq, payload, updated_at)
            VALUES('a.0001', 'a', 1, '{}', '2026-10-01T00:00:00');
        ''')
        conn.commit()
        conn.close()
        return db

    def test_open_migrates_columns_and_keeps_rows(self, tmp_path):
        db = self._old_db(tmp_path)
        with cs.connect(db) as conn:
            cols = {r[1] for r in conn.execute('PRAGMA table_info(beats)')}
            ucols = {r[1] for r in conn.execute('PRAGMA table_info(users)')}
            row = conn.execute(
                "SELECT payload FROM beats WHERE line_id='a.0001'").fetchone()
            role = conn.execute(
                "SELECT role FROM users WHERE id='old'").fetchone()['role']
        assert 'locked_until' in cols
        assert 'role' in ucols
        assert json.loads(row['payload']) == {}
        assert role == 'editor', '老成员默认 editor（与既有行为兼容）'

    def test_no_owner_can_claim_the_first_owner(self, tmp_path):
        """老库没有任何 owner → 第一次 set_role 允许认领，否则权限死锁。"""
        db = self._old_db(tmp_path)
        res = cs.set_role(db, 'old', 'owner', actor='old')
        assert res['ok'], res
        assert cs.set_role(db, 'old', 'viewer', actor='old')['ok']


class TestLockLease:
    def test_lock_returns_a_lease(self, tmp_path):
        db, _ = _seed(tmp_path)
        res = cs.lock(db, 'ch01.0001', 'alice', ttl=120)
        assert res['ok'], res
        assert res['lease_seconds'] == 120
        rows = cs.locks(db)
        assert rows[0]['expires_in'] > 0
        assert not rows[0]['expired']

    def test_expired_lock_can_be_taken_over_with_audit(self, tmp_path):
        db, _ = _seed(tmp_path)
        assert cs.lock(db, 'ch01.0001', 'alice')['ok']
        # 把租约人为改成过去
        past = (datetime.now() - timedelta(minutes=1)).isoformat(
            timespec='seconds')
        with cs.connect(db, write=True) as conn:
            conn.execute(
                'UPDATE beats SET locked_until=? WHERE line_id=?',
                (past, 'ch01.0001'))
        res = cs.lock(db, 'ch01.0001', 'bob')
        assert res['ok'], res
        assert cs.locks(db)[0]['locked_by'] == 'bob'
        with cs.connect(db) as conn:
            acts = [r['action'] for r in conn.execute(
                'SELECT action FROM audit ORDER BY id DESC LIMIT 8')]
        assert 'lock_takeover_expired' in acts, acts

    def test_unexpired_lock_still_blocks(self, tmp_path):
        db, _ = _seed(tmp_path)
        assert cs.lock(db, 'ch01.0001', 'alice')['ok']
        res = cs.lock(db, 'ch01.0001', 'bob')
        assert not res['ok']
        assert 'alice' in res['reason']

    def test_renew_extends_only_for_the_owner(self, tmp_path):
        db, _ = _seed(tmp_path)
        assert cs.lock(db, 'ch01.0001', 'alice', ttl=120)['ok']
        first = cs.locks(db)[0]['expires_in']
        assert cs.renew_lock(db, 'ch01.0001', 'bob')['ok'] is False
        assert cs.renew_lock(db, 'ch01.0001', 'alice', ttl=600)['ok']
        assert cs.locks(db)[0]['expires_in'] > first

    def test_push_after_lock_expires(self, tmp_path):
        db, _seed_sheet = _seed(tmp_path)
        sheet = tmp_path / 'bob.xlsx'
        cs.pull(db, sheet, actor='bob')
        assert cs.lock(db, 'ch01.0001', 'alice')['ok']
        past = (datetime.now() - timedelta(minutes=1)).isoformat(
            timespec='seconds')
        with cs.connect(db, write=True) as conn:
            conn.execute('UPDATE beats SET locked_until=? WHERE line_id=?',
                         (past, 'ch01.0001'))
        import openpyxl
        from storygraph.story.script_table import BEAT_FIELDS
        wb = openpyxl.load_workbook(sheet)
        wb['Beats'].cell(row=2, column=BEAT_FIELDS.index('Text') + 1,
                         value='bob 越过了过期锁')
        wb.save(sheet)
        res = cs.push(db, sheet, actor='bob')
        assert 'ch01.0001' in res['updated'], res
        assert not res.get('rejected'), res


class TestRoles:
    def test_first_member_is_owner_and_default_is_editor(self, tmp_path):
        db, _ = _seed(tmp_path)
        roles = {m['id']: m['role'] for m in cs.members(db)}
        assert roles == {'alice': 'owner', 'bob': 'editor'}, roles

    def test_viewer_cannot_push_or_lock_but_can_read(self, tmp_path):
        db, sheet = _seed(tmp_path)
        assert cs.set_role(db, 'bob', 'viewer', actor='alice')['ok']
        assert cs.lock(db, 'ch01.0001', 'bob')['ok'] is False
        assert cs.set_status(db, 'ch01.0001', 'approved', 'bob')['ok'] is False
        res = cs.push(db, sheet, actor='bob')
        assert res.get('permission_denied'), res
        # 读仍然可以
        assert cs.status_summary(db)['lines'] == 2
        assert cs.pull(db, tmp_path / 'bob.xlsx', actor='bob')['rows'] == 2

    def test_only_owner_can_change_roles(self, tmp_path):
        db, _ = _seed(tmp_path)
        assert cs.set_role(db, 'bob', 'owner', actor='bob')['ok'] is False
        assert cs.set_role(db, 'bob', 'viewer', actor='alice')['ok']
        assert cs.set_role(db, 'bob', 'editor', actor='alice')['ok']

    def test_role_shows_up_in_status_summary(self, tmp_path):
        db, _ = _seed(tmp_path)
        cs.set_role(db, 'bob', 'viewer', actor='alice')
        users = {u['id']: u for u in cs.status_summary(db)['users']}
        assert users['alice']['role'] == 'owner'
        assert users['bob']['role'] == 'viewer'
        assert users['bob']['role_label'] == '只读'

    def test_force_is_allowed_for_editor(self, tmp_path):
        """既有契约：editor 的 `--force` 逃生舱不被权限收紧误伤。"""
        db, sheet = _seed(tmp_path)
        assert cs.set_role(db, 'bob', 'editor', actor='alice')['ok']
        beats, _ = cs.export_ir_from_db(db)
        beats[0].text = 'bob 强制覆盖'
        from storygraph.story.excel_export import export_beats_to_excel
        out = tmp_path / 'force.xlsx'
        export_beats_to_excel(beats, out)
        res = cs.push(db, out, actor='bob', force=True)
        assert res['updated'], res


class TestUnknownRoleFailsClosed:
    """
    认不出的角色按**最小权限**处理（2026-10-20 修，P3）。

    从前 `role_of` 是 `return role if role in ROLES else ROLE_EDITOR`：
    一个被手改成 `role='admin'`（或迁移写坏）的成员会被**当成 editor 放行**
    改内容。权限判断的方向必须是"认不出就少给"。

    ⚠ 空串（**没登记**的成员 → 调用方兜底 editor）是另一回事：那是"本地单人
    第一次打开"的老语义，和"库里有这条人、但角色是乱码"完全不同 ——
    两者从前都落在 editor 上，这才是漏洞的形状。
    """

    def test_unknown_role_returns_viewer(self, tmp_path):
        db, _ = _seed(tmp_path)
        with cs.connect(db, write=True) as conn:
            conn.execute(
                'INSERT INTO users(id, display_name, role, created_at) '
                'VALUES(?,?,?,?)', ('eve', 'eve', 'admin', cs._now()))
        with cs.connect(db) as conn:
            assert cs.role_of(conn, 'eve') == cs.ROLE_VIEWER, (
                '认不出的角色被当成了 editor（fail-open）')

    def test_unknown_role_cannot_manage_members(self, tmp_path):
        db, _ = _seed(tmp_path)
        with cs.connect(db, write=True) as conn:
            conn.execute(
                'INSERT INTO users(id, display_name, role, created_at) '
                'VALUES(?,?,?,?)', ('eve', 'eve', 'root', cs._now()))
        res = cs.set_role(db, 'bob', 'viewer', actor='eve')
        assert res['ok'] is False, res
        assert '所有者' in res['reason']

    def test_members_list_matches_enforcement(self, tmp_path):
        """清单说"编辑"、实际按只读拦，是最难查的那种不一致。"""
        db, _ = _seed(tmp_path)
        with cs.connect(db, write=True) as conn:
            conn.execute(
                'INSERT INTO users(id, display_name, role, created_at) '
                'VALUES(?,?,?,?)', ('eve', 'eve', 'superuser', cs._now()))
        roles = {m['id']: m['role'] for m in cs.members(db)}
        assert roles['eve'] == cs.ROLE_VIEWER

    def test_unregistered_user_still_gets_no_role(self, tmp_path):
        """没登记的人仍然是空串（本地单人那条口子不许被这条改动误伤）。"""
        db, _ = _seed(tmp_path)
        with cs.connect(db) as conn:
            assert cs.role_of(conn, 'nobody-yet') == ''
