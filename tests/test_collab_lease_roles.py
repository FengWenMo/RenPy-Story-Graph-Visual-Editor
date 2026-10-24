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
from storygraph.story.line_identity import parse as cs_parse


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
    def test_illegal_fork_ids_are_migrated_to_parseable_ids(self, tmp_path):
        """
        清单 §三：历史库里的 `ch01.0002~2` 全项目解析器都不认。

        打开库时把它重新发号（`parse()` 必须认得），payload 里的 `LineId`
        也要同步；`label` 字段保持原样。
        """
        import json

        db, _seed_sheet = _seed(tmp_path)
        with cs.connect(db, write=True) as conn:
            payload = conn.execute(
                "SELECT payload FROM beats WHERE line_id='ch01.0002'").fetchone()
            data = json.loads(payload['payload'])
            data['LineId'] = 'ch01.0002~2'
            conn.execute(
                "UPDATE beats SET line_id='ch01.0002~2', payload=? "
                "WHERE line_id='ch01.0002'",
                (json.dumps(data, ensure_ascii=False),))

        # 下一次 connect 触发值迁移
        with cs.connect(db) as conn:
            rows = conn.execute(
                'SELECT line_id, label, payload FROM beats ORDER BY seq').fetchall()
        ids = [r['line_id'] for r in rows]
        assert all(cs_parse(lid) is not None for lid in ids), ids
        assert len(set(ids)) == len(ids)
        assert not any('~' in lid for lid in ids), ids
        migrated = [r for r in rows if r['label'] == 'ch01' and
                    json.loads(r['payload']).get('LineId') == r['line_id']]
        assert len(migrated) == 2, rows
        assert all(cs_parse(r['line_id']) is not None for r in migrated)

    def test_keep_both_new_id_uses_the_allocator_not_a_tilde_suffix(self,
                                                                    tmp_path):
        """「两句都留」的新行 ID 必须是 `label.NNNN`，不是 `~2`。"""
        db, _seed_sheet = _seed(tmp_path)
        with cs.connect(db, write=True) as conn:
            new = cs._unique_line_id(conn, 'ch01.0001')
            ids = {r['line_id'] for r in
                   conn.execute('SELECT line_id FROM beats')}
        assert new not in ids
        assert cs_parse(new) == ('ch01', 3), new


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

    def test_timestamps_are_timezone_aware(self, tmp_path):
        """
        清单 §十九：写库一律 UTC + 时区后缀（不再写"无时区本地时间"）。
        """
        db, _ = _seed(tmp_path)
        res = cs.lock(db, 'ch01.0001', 'alice', ttl=120)
        assert res['expires_at'].endswith('+00:00'), res['expires_at']
        parsed = cs._parse_ts(cs._now())
        assert parsed is not None and parsed.tzinfo is not None

    def test_cross_timezone_lease_does_not_expire_early(self):
        """
        北京（UTC+8）写、东京（UTC+9）读：差值不能再被当成本地时间差。

        修复前：`locked_until` 是 naive 的 +08 本地时间，东京用 +09 本地时间
        比较 → 凭空早 1 小时判过期。修复后统一在 aware UTC 域比较。
        """
        from datetime import datetime, timedelta, timezone

        bj = timezone(timedelta(hours=8))
        tokyo = timezone(timedelta(hours=9))
        now_utc = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        until_bj = (now_utc + timedelta(minutes=15)).astimezone(bj).isoformat()
        row = {'locked_by': 'alice',
               'locked_at': now_utc.astimezone(bj).isoformat(),
               'locked_until': until_bj}

        state = cs.lock_state(row, now=now_utc.astimezone(tokyo))
        assert state['locked'] is True
        assert state['expired'] is False
        assert 890 <= state['expires_in'] <= 910, state

    def test_naive_and_aware_records_do_not_raise(self, tmp_path):
        """混进一条带时区的记录（或一条老的无时区记录）不许直接抛异常。"""
        from datetime import datetime, timedelta, timezone

        future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        past_local = (datetime.now() - timedelta(minutes=10)).isoformat()
        for until in (future, past_local):
            state = cs.lock_state({'locked_by': 'alice',
                                   'locked_at': until,
                                   'locked_until': until})
            assert isinstance(state['expired'], bool), state

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

    ⚠ "没登记"是另一回事（2026-10-21 收口）：`role_of` 现在要求调用方
    **明写** `default=…`——本地单人那条口子（未登记 = editor）只存在于
    调用点，不再是一个隐含的 `or ROLE_EDITOR`。
    """

    def test_unknown_role_returns_viewer(self, tmp_path):
        db, _ = _seed(tmp_path)
        with cs.connect(db, write=True) as conn:
            conn.execute(
                'INSERT INTO users(id, display_name, role, created_at) '
                'VALUES(?,?,?,?)', ('eve', 'eve', 'admin', cs._now()))
        with cs.connect(db) as conn:
            assert cs.role_of(conn, 'eve', default=cs.ROLE_EDITOR) \
                == cs.ROLE_VIEWER, '认不出的角色被当成了 editor（fail-open）'

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

    def test_unregistered_user_gets_the_callers_default(self, tmp_path):
        """
        没登记的人 → **调用方明写的那个 default**（本地单人那条口子）。

        从前是"空串 + 各处 `or ROLE_EDITOR`"：忘补一处就静默变只读。
        现在不写 `default=` 直接 TypeError，写了才生效。
        """
        db, _ = _seed(tmp_path)
        with cs.connect(db) as conn:
            assert cs.role_of(conn, 'nobody-yet',
                              default=cs.ROLE_EDITOR) == cs.ROLE_EDITOR
            assert cs.role_of(conn, 'nobody-yet',
                              default=cs.ROLE_VIEWER) == cs.ROLE_VIEWER
            # 默认值也认不出 → 最小权限（不因为调用方写错就放行）
            assert cs.role_of(conn, 'nobody-yet',
                              default='admin') == cs.ROLE_VIEWER

    def test_the_default_argument_is_mandatory(self, tmp_path):
        """**类型上**绕不过去：漏了 `default` 是 TypeError，不是安静的错误答案。"""
        db, _ = _seed(tmp_path)
        with cs.connect(db) as conn:
            with pytest.raises(TypeError):
                cs.role_of(conn, 'nobody-yet')


class TestOwnerClaim:
    """
    "无 owner 时可认领"：迁移必需，但必须**只用一次**、并且说出来。

    这不是安全边界（工具没有认证，`--user` 默认 anonymous，谁都能传名字）。
    能做的有两件事：审计留痕 + 让调用方明说"这是本库第一次认领"。
    """

    def _no_owner_db(self, tmp_path, users=('alice', 'bob')):
        """
        一个**既没有 owner、又有成员**的库（schema 2 老库迁移后的样子）。

        ⚠ `init_db(owner='')` 会自动把**第一个成员**提成 owner（新库总得
        有人能管），所以这里必须自己插成员 —— 否则测不到认领那条路。
        """
        db = tmp_path / 'c.db'
        cs.init_db(db)
        with cs.connect(db, write=True) as conn:
            for u in users:
                cs.ensure_user(conn, u)
            assert cs._has_owner(conn) is False
        return db

    def test_first_claim_is_flagged_once(self, tmp_path):
        db = self._no_owner_db(tmp_path)
        with cs.connect(db) as conn:
            assert cs._has_owner(conn) is False
        first = cs.set_role(db, 'alice', 'owner', actor='alice')
        assert first['ok'] and first['claimed'] is True, first
        # 第二次：已经有 owner 了，bob 不是 owner → 被拦
        second = cs.set_role(db, 'bob', 'owner', actor='bob')
        assert second['ok'] is False, second
        assert '所有者' in second['reason']

    def test_the_claim_is_written_to_the_audit(self, tmp_path):
        """认领要留痕：审计里有 `set_role`，detail 里 `claimed: true`。"""
        db = self._no_owner_db(tmp_path, users=('alice',))
        cs.set_role(db, 'alice', 'owner', actor='alice')
        with cs.connect(db) as conn:
            rows = [dict(r) for r in conn.execute(
                'SELECT actor, action, detail FROM audit ORDER BY ts').fetchall()]
        claims = [r for r in rows if r['action'] == 'set_role'
                  and '"claimed": true' in str(r['detail'])]
        assert claims, rows
        assert claims[0]['actor'] == 'alice'

    def test_owner_state_reports_the_truth(self, tmp_path):
        """"本库尚无所有者"必须是查得出来的事实，不是等出事才发现。"""
        db = self._no_owner_db(tmp_path)
        st = cs.owner_state(db)
        assert st['has_owner'] is False and st['members'] == 2, st
        cs.set_role(db, 'alice', 'owner', actor='alice')
        st = cs.owner_state(db)
        assert st['has_owner'] is True and st['owner'] == 'alice', st

    def test_empty_role_in_the_db_shows_as_viewer(self, tmp_path):
        """库里 `role=''` 是脏值：清单显示"只读"，与拦截口径一致。"""
        db = tmp_path / 'c.db'
        cs.init_db(db, users=['alice', 'bob'], owner='alice')
        with cs.connect(db, write=True) as conn:
            conn.execute("UPDATE users SET role='' WHERE id='bob'")
        roles = {m['id']: m['role'] for m in cs.members(db)}
        assert roles['bob'] == cs.ROLE_VIEWER, roles
