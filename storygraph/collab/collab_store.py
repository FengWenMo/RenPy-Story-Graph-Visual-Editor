"""
collab_store.py
===============
团队协作后端：SQLite 是**协作记录**，Excel 是编辑前端。

## 真源（2026-10-17 定，回应调研的"三个真源打架"）

    `.rpy`   = **内容真源**（作者写的、游戏读的那份）
    SQLite   = 协作记录：同一份列契约的**可查询副本** + 锁 / 审批 / 审计
    Excel    = 编辑前端（文案/策划唯一愿意用的界面，本地化厂商也收 xlsx）

所以 SQLite **不是第二真源**：它存的 payload 必须能从 `.rpy`/Excel 无损
重建，也必须能无损写回。判据是 `tests/test_collab_roundtrip.py` 里
"每个 `FieldSpec.collab` 字段过一遍库都要原样回来"。

## 这个模块解决的问题

  · 每次 `push` 都按 LineId 做 upsert，并检查"你拉取时的版本"是否已过期
    → 别人改了同一行，你不会静默覆盖，而是收到冲突清单。
    **没有「协作信息」表 = 没有基线 → 默认拒绝覆盖**（2026-10-17 修：
    从前 `base_rev and ...` 把"没有基线"当成"不用检查"，普通导出表
    可以盲覆盖远端改动）。
  · 行级锁：`lock` 之后别人 push 这一行会被拒（同一行两个人同时改是
    叙事工具里最贵的错——改了 A 的意思，B 没看到）。
  · 状态机 + 审计：draft → in_review → approved，每次变更留痕。
  · 资产表同样入库（assets），这样 Unity 导出 / 校验都能从同一个库取。

设计原则：本模块不依赖 tkinter，可被 CLI、编辑器、CI 共用。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.story.beat_fields import BEAT_FIELDS
from storygraph.story.beat_model import Beat, assign_identity
from storygraph.core.cell_limits import CELL_TEXT_LIMIT
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

#: 库结构版本。
#:
#: 2（2026-10-18）：`FileBlocks` 从**行 payload** 搬进独立表 `file_blocks`。
#: 为什么：一行的 payload 里塞 64KB 的顶层文本，Excel 的 `BasePayload`
#: 单元格（上限 32767）抄不下 → JSON 在 char 592 断掉 → 第二次协作起
#: 字段级合并全部退化。文件级数据本来就不属于任何一拍。
#:
#: 3（2026-10-19，协作三件小事）：
#:   · `beats.locked_until` —— 锁**租约**的到期时间（不再"一锁到永久"）；
#:   · `users.role` —— 成员权限（viewer / editor / owner）；
#:   · 连接层启用 WAL + `busy_timeout`，写事务用 `BEGIN IMMEDIATE`。
SCHEMA_VERSION = 3

#: 等锁多久（秒）。SQLite 在多进程并发写时默认 5 秒就抛
#: `database is locked`；团队协作的写事务都是毫秒级，30 秒足够且不会让人白等。
DB_TIMEOUT_SECONDS = 30.0
#: `PRAGMA busy_timeout` 的毫秒数（与 `DB_TIMEOUT_SECONDS` 同一档）。
BUSY_TIMEOUT_MS = 30_000

#: 锁租约默认时长（秒）。15 分钟：够改一段台词/一页表，又不至于
#: 一个人下班没解锁就让整章永远写不进去。
LOCK_LEASE_SECONDS = 15 * 60

#: 成员角色。**默认 editor**（与既有行为兼容：老库里的成员照样能 push）。
ROLE_OWNER = 'owner'
ROLE_EDITOR = 'editor'
ROLE_VIEWER = 'viewer'
ROLES = (ROLE_OWNER, ROLE_EDITOR, ROLE_VIEWER)
ROLE_LABELS = {ROLE_OWNER: '所有者', ROLE_EDITOR: '编辑',
               ROLE_VIEWER: '只读'}

#: 角色 → 允许的动作。只做**最小可用**的三档：
#:   · viewer 只读（pull / status 照样能看）；
#:   · editor 能改（push / lock / 状态 / 资产）；
#:   · owner 额外能强制覆盖与改成员角色。
#: `approve` 目前与 `status` 同权（editor 也能把状态置 approved）——
#: 这是 2026-10-19 之前的既有行为，先不收紧，只把"只读"这一档立起来。
_ROLE_ACTIONS = {
    ROLE_OWNER: frozenset({'read', 'push', 'lock', 'unlock', 'renew',
                           'status', 'approve', 'assets', 'force',
                           'manage'}),
    ROLE_EDITOR: frozenset({'read', 'push', 'lock', 'unlock', 'renew',
                            'status', 'approve', 'assets', 'force'}),
    ROLE_VIEWER: frozenset({'read'}),
}

STATUS_DRAFT = 'draft'
STATUS_REVIEW = 'in_review'
STATUS_APPROVED = 'approved'
STATUSES = (STATUS_DRAFT, STATUS_REVIEW, STATUS_APPROVED)

# push/pull 时携带协作状态的工作表名（不污染 Beats 表的列）
COLLAB_SHEET = '协作信息'
#: 协作信息表的列。
#:
#: `BasePayload` 是**拉取那一刻这一行的完整内容**（2026-10-19，C1 三方合并的 base）。
#: 为什么装进 Excel 而不是数据库：每个人拉取的时间不同，"你那时的基线"只有
#: **你手上这份表**知道；装进文件里，复制/改名/另存都不会丢。老版本 Excel 另存
#: 丢掉隐藏表 → 读不到 base → 降级成两方比较（并明确告知），见 `push`。
COLLAB_FIELDS = ['LineId', 'Revision', 'LockedBy', 'Status', 'UpdatedBy',
                 'UpdatedAt', 'BasePayload']
#: 上面那一列的名字（读表时按它取）
BASE_FIELD = 'BasePayload'
#: 协作信息表左下角的"拉取时间"标记（与行数据同表，避免多一张 sheet）
PULLED_AT_KEY = 'PulledAt'

#: 三方合并**不比**的列：身份（LineId）和派生值（ContentHash 写库时重算）
_MERGE_SKIP = ('LineId', 'ContentHash')

_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    role         TEXT NOT NULL DEFAULT 'editor',
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS beats (
    line_id      TEXT PRIMARY KEY,
    label        TEXT NOT NULL DEFAULT '',
    seq          INTEGER NOT NULL DEFAULT 0,
    sheet        TEXT NOT NULL DEFAULT '',
    payload      TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'draft',
    revision     INTEGER NOT NULL DEFAULT 1,
    locked_by    TEXT,
    locked_at    TEXT,
    locked_until TEXT,
    updated_by   TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL DEFAULT '',
    deleted      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_beats_label ON beats(label);
CREATE TABLE IF NOT EXISTS assets (
    asset_type   TEXT NOT NULL,
    asset_id     TEXT NOT NULL,
    payload      TEXT NOT NULL DEFAULT '{}',
    updated_by   TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (asset_type, asset_id)
);
CREATE TABLE IF NOT EXISTS file_blocks (
    label      TEXT PRIMARY KEY,
    text       TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    actor    TEXT NOT NULL DEFAULT '',
    action   TEXT NOT NULL,
    line_id  TEXT NOT NULL DEFAULT '',
    detail   TEXT NOT NULL DEFAULT ''
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


# ==================================================================
#  连接与建库
# ==================================================================

def _configure_connection(conn) -> None:
    """
    连接级 PRAGMA。**WAL + busy_timeout 是"会丢数据 → 会冲突"的第一道闸**：
      · WAL 让读写不互相堵（读事务不再挡住写）；
      · busy_timeout 让并发写等待而不是立刻抛 `database is locked`；
      · `synchronous=NORMAL` 是 WAL 下的常规取舍（掉电最多丢最后一笔提交，
        不至于把库写坏）；
      · `foreign_keys=ON` 目前没有外键，但打开它避免以后加表时静默不生效。
    ⚠ WAL 在网络盘 / 只读介质上可能失败：失败**不让整个连接挂掉**，
    照旧用默认 journal（这时 busy_timeout 仍然生效）。
    """
    try:
        conn.execute(f'PRAGMA busy_timeout={int(BUSY_TIMEOUT_MS)}')
    except sqlite3.Error as e:                  # noqa: BLE001 - 老 SQLite 也不该炸
        swallow('collab_store.configure.busy_timeout', e)
    try:
        conn.execute('PRAGMA journal_mode=WAL')
    except sqlite3.Error as e:                  # noqa: BLE001 - 网络盘/只读
        swallow('collab_store.configure.wal', e)
    try:
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA foreign_keys=ON')
    except sqlite3.Error as e:                  # noqa: BLE001
        swallow('collab_store.configure.pragmas', e)


def _table_columns(conn, table: str) -> set:
    """表的列名集合（`PRAGMA table_info`；表名只来自本模块的常量）。"""
    try:
        return {str(r[1]) for r in conn.execute(f'PRAGMA table_info({table})')}
    except sqlite3.Error as e:                  # noqa: BLE001
        swallow('collab_store.table_columns', e)
        return set()


def _migrate_schema(conn) -> None:
    """
    schema 2 → 3 的**增量迁移**（幂等）。

    只加列、不改老列：老库照常打开；新列有默认值（role=editor /
    locked_until=NULL，NULL 表示"按 locked_at + 租约推"）。
    """
    beat_cols = _table_columns(conn, 'beats')
    if beat_cols and 'locked_until' not in beat_cols:
        conn.execute('ALTER TABLE beats ADD COLUMN locked_until TEXT')
    user_cols = _table_columns(conn, 'users')
    if user_cols and 'role' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT "
                     "NOT NULL DEFAULT 'editor'")


@contextmanager
def connect(db_path, write: bool = False):
    """
    打开数据库（自动建表/迁移），用完提交并关闭。

    `write=True`：进入时立刻 `BEGIN IMMEDIATE` 拿写锁。
      · 协作里"读一下再写"的操作（push / lock / 状态机）必须用它 ——
        否则两个进程可以同时读到旧 revision，然后各写各的（CAS 失效）；
      · 纯读（status / pull）用默认的 `write=False`，WAL 下不挡别人写。
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    try:
        _configure_connection(conn)
        conn.executescript(_DDL)
        if write:
            # 先把写锁拿到手：DDL 之后、任何"读-改-写"之前。
            conn.execute('BEGIN IMMEDIATE')
        _migrate_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),))
        # 老库（schema 1）：`FileBlocks` 还在每行 payload 里 → 搬到独立表。
        # 幂等：搬过一次就在 `meta` 里记一笔，之后的 connect 不再全表扫。
        _migrate_file_blocks(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path, users=(), owner: str = '') -> Path:
    """
    建库并登记成员。

    `owner` 为空时：**第一个成员**自动成为 owner（新库总得有人能管权限）；
    老库已有 owner 时不重复指定。角色只是最小三档，见 `ROLES`。
    """
    with connect(db_path, write=True) as conn:
        for u in users:
            ensure_user(conn, u)
        target = str(owner or '')
        if not target and users:
            target = str(users[0] or '')
        if target and not _has_owner(conn):
            _set_role(conn, target, ROLE_OWNER)
    return Path(db_path)


def ensure_user(conn, user_id: str, display_name: str = '',
                role: str = '') -> None:
    if not user_id:
        return
    role = role if role in ROLES else ROLE_EDITOR
    conn.execute(
        'INSERT OR IGNORE INTO users(id, display_name, role, created_at) '
        'VALUES(?,?,?,?)', (user_id, display_name or user_id, role, _now()))
    if display_name:
        conn.execute('UPDATE users SET display_name=? WHERE id=? AND '
                     "(display_name='' OR display_name=id)",
                     (display_name, user_id))


def _audit(conn, actor, action, line_id='', detail=''):
    conn.execute(
        'INSERT INTO audit(ts, actor, action, line_id, detail) '
        'VALUES(?,?,?,?,?)',
        (_now(), actor or '', action, line_id or '',
         json.dumps(detail, ensure_ascii=False) if not isinstance(detail, str)
         else detail))


# ==================================================================
#  成员角色（最小三档：viewer / editor / owner）
# ==================================================================

def role_of(conn, user_id: str) -> str:
    """成员角色（没登记的成员 → 空串；调用方按 `editor` 兜底）。"""
    row = conn.execute('SELECT role FROM users WHERE id=?',
                       (str(user_id or ''),)).fetchone()
    if row is None:
        return ''
    role = str(row['role'] or '')
    return role if role in ROLES else ROLE_EDITOR


def _has_owner(conn) -> bool:
    row = conn.execute(
        'SELECT 1 FROM users WHERE role=? LIMIT 1', (ROLE_OWNER,)).fetchone()
    return row is not None


def _set_role(conn, user_id: str, role: str) -> None:
    """直接改角色（不做权限判断；权限在 `set_role` 那层）。"""
    if role not in ROLES:
        raise ValueError(f'角色只能是 {"/".join(ROLES)}')
    ensure_user(conn, user_id)
    conn.execute('UPDATE users SET role=? WHERE id=?', (role, user_id))


def set_role(db_path, member: str, role: str, actor: str = '') -> dict:
    """
    改成员角色（只有 owner 能改；**新库无 owner** 时允许第一次认领）。

    为什么留"无 owner 可认领"：schema 2 的老库里所有成员都是默认 editor，
    没有这个口子就永远没人能设第一个 owner —— 迁移后权限会死锁。
    """
    member = str(member or '').strip()
    role = str(role or '').strip()
    if not member:
        return {'ok': False, 'reason': '要指定成员（--member）'}
    if role not in ROLES:
        return {'ok': False, 'reason': f'角色只能是 {"/".join(ROLES)}'}
    with connect(db_path, write=True) as conn:
        ensure_user(conn, actor or member)
        if _has_owner(conn):
            caller = role_of(conn, actor or member) or ROLE_EDITOR
            if not _can(caller, 'manage'):
                return {'ok': False,
                        'reason': f'只有所有者能改成员角色（你是 {caller}）'}
        _set_role(conn, member, role)
        _audit(conn, actor or member, 'set_role', member,
               {'role': role})
    return {'ok': True, 'member': member, 'role': role}


def members(db_path) -> list:
    """成员清单：`[{'id','display_name','role','role_label'}]`。"""
    with connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT id, display_name, role, created_at FROM users '
            'ORDER BY id').fetchall()]
    for r in rows:
        role = str(r.get('role') or ROLE_EDITOR)
        r['role'] = role if role in ROLES else ROLE_EDITOR
        r['role_label'] = ROLE_LABELS.get(r['role'], r['role'])
    return rows


def _actor_role(conn, actor: str) -> str:
    """调用者角色：未登记先按默认 editor 登记（保持既有"首次出现即成员"）。"""
    ensure_user(conn, actor)
    return role_of(conn, actor) or ROLE_EDITOR


def _can(role: str, action: str) -> bool:
    return action in _ROLE_ACTIONS.get(role or ROLE_EDITOR, frozenset())


# ==================================================================
#  锁租约（lease）
# ==================================================================

def _parse_ts(value):
    """ISO 时间串 → datetime（认不出返回 None；只用于租约判断）。"""
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _lease_deadline(row, now=None):
    """
    这一行的锁什么时候到期：
      · 有 `locked_until` → 用它；
      · 老库只有 `locked_at` → `locked_at + LOCK_LEASE_SECONDS`；
      · 两个都没有 → None（**保守**：当作没过期，宁可让人问一句）。
    """
    if row is None:
        return None
    keys = row.keys() if hasattr(row, 'keys') else ()
    if 'locked_until' in keys:
        until = _parse_ts(row['locked_until'])
        if until is not None:
            return until
    if 'locked_at' in keys:
        at = _parse_ts(row['locked_at'])
        if at is not None:
            return at + timedelta(seconds=LOCK_LEASE_SECONDS)
    return None


def lock_state(row, now=None) -> dict:
    """
    锁的展示状态：`{'locked','owner','until','expires_in','expired'}`。

    `expires_in` 为负 = 已过期；None = 不知道（没有时间戳）。
    """
    now = now or datetime.now()
    owner = str((row or {})['locked_by'] or '') if row else ''
    if not owner:
        return {'locked': False, 'owner': '', 'until': '',
                'expires_in': None, 'expired': False}
    deadline = _lease_deadline(row, now)
    until = deadline.isoformat(timespec='seconds') if deadline else ''
    expires_in = (int((deadline - now).total_seconds())
                  if deadline is not None else None)
    return {'locked': True, 'owner': owner, 'until': until,
            'expires_in': expires_in,
            'expired': bool(deadline is not None and expires_in is not None
                            and expires_in <= 0)}


def _lock_is_free_for(row, actor: str) -> tuple:
    """
    这一行的锁对 `actor` 是不是"可写"：`(可以写, 原因)`。

    过期的锁**不阻塞**别人（这正是租约的意义）；但审计里要留一笔，
    不能让人以为"锁还在"。
    """
    owner = str((row or {})['locked_by'] or '')
    if not owner or owner == actor:
        return True, ''
    state = lock_state(row)
    if state['expired']:
        return True, f"{owner} 的锁已过期（{state['until']}）"
    return False, f'被 {owner} 锁定'


# ==================================================================
#  行 ↔ 记录
# ==================================================================

#: 章节级字段在 payload 里的键（不在 `BEAT_FIELDS` 里，没有 Excel 列）。
#:
#: ⚠ 2026-10-18 起**不再写进 payload**（schema 2）：它是文件级数据，一行
#: 一拍带着它走 = 一行 64KB，Excel 的 `BasePayload` 抄不下。现在住在
#: `file_blocks` 表里（整库一份）。这个名字只剩"读老库"一个用途。
_FILE_BLOCKS_KEY = 'FileBlocks'


def _beat_to_payload(beat: Beat) -> dict:
    """
    Beat → 可入库的 payload。

    **走与 Excel 完全相同的列编解码器**（`to_row` / `from_row`），而不是
    逐个 `getattr` —— 这是 2026-10-17 的修法，此前用
    `getattr(beat, field.lower(), '')` 猜属性名：

        StageOrder → stageorder   （实际是 stage_order）→ 空
        OpOrder    → oporder      （实际是 op_order）   → 空
        CodeBlocks → codeblocks   （实际是 code_blocks） → 空
        With       → with         （实际是 with_）       → 空
        SpecHead   → spechead     （实际是 spec_head）   → 空
        …

    过一遍协作库，舞台顺序、代码块边界、菜单位置、自定义语句头、
    循环/相机头、行尾注释**全部静默丢失**，而当时的 round-trip 测试
    只断言了 LineId 和 Text，所以一直绿。

    现在的契约：**payload 的键 = `BEAT_FIELDS`**，值 = 写进 Excel 那一格
    的东西。Excel 能往返的，协作库就能往返（同一段编解码器，不可能漂移）。

    `file_blocks` **不在 payload 里**（2026-10-18，schema 2）：它是文件级
    数据，从前塞进"第一拍"的 payload，一行 64KB —— pull 时那段 JSON 写进
    `BasePayload` 单元格被 Excel 截成 32767，第二次协作起字段级合并全部
    退化。现在它住 `file_blocks` 表（整库一份），payload 回到几百字节。
    """
    return dict(zip(BEAT_FIELDS, beat.to_row()))


def beat_from_payload(payload: dict) -> Beat:
    """
    库里取出的 payload → Beat（`_beat_to_payload` 的逆）。

    ⚠ `Seq` **不再 pop 掉**（2026-10-17 修）：从前行里手动写了
    `payload.pop('Seq', None)`，于是数据库里存了 Seq 也永远回不到拍上，
    pull 出来全是 0。现在让 `from_row` 正常读它；`pull` / `export_ir_from_db`
    还会用数据库那一列覆盖（列是排序权威）。
    """
    payload = dict(payload or {})
    file_blocks = payload.pop(_FILE_BLOCKS_KEY, '')
    beat = Beat.from_row(payload)
    # 老库（schema 1）的 payload 里可能还带着它；新库由 `_attach_file_blocks`
    # 从 `file_blocks` 表补上（那里才是真源）。
    beat.file_blocks = str(file_blocks or '')
    return beat


def _payload_from_beat(beat: Beat) -> dict:
    """兼容旧名字（`_beat_to_payload` 的别名）。"""
    return _beat_to_payload(beat)


# ==================================================================
#  文件级保真块（整库一份）—— schema 2 起独立成表
# ==================================================================
#
# 为什么必须是**表**而不是行里一个键（2026-10-18 实测）：
#
#   库刚 import-ir   → 每行 payload 中位数 622 字符
#   某人 push 一次   → book.0045 的 payload 69,341 字符（FileBlocks 64,000）
#   第二次 pull      → BasePayload 单元格被 Excel 截成 32,767，JSON 断在 char 592
#   再 push          → base=None → 退化两方比较 → 字段级明细全没了
#
# 一行 64KB 的文件级数据 + 一个 32767 的单元格上限 = 第二次协作必然坏。
# 而"顶层 define/screen/init"是**文件级**的：它不属于任何一拍，也不属于
# 任何一章 —— 渲染时它写在所有 label 之前一次。
#
# 所以这里是**一整库一份**（`LIBRARY_BLOCKS` 那一个键），而不是一章一份。
# 为什么不做"一章一份"（试过，当天就翻了）：Excel 的「文件级保真」表里
# 没有章节信息（`块号 + 行文本`），读回来只能挂到"第一张拍"上；而 pull
# 出去的拍序与送回来的可能不同（admi… ↔ sr_about_render_test），于是同一
# 段文本会被登记到两个章节名下 —— 再渲染就是**两份 init python**。

#: `file_blocks` 表里"整库一份"那一行的键（空串 = 不绑任何章节）。
LIBRARY_BLOCKS = ''


def _merged_blocks(beats) -> str:
    """这批拍上的顶层块 → 合并文本（按拍序、去重，与渲染同口径）。"""
    from storygraph.story.beat_model import (join_file_block_parts,
                                             split_file_blocks)

    seen, parts = set(), []
    for b in beats or ():
        for part in split_file_blocks(str(getattr(b, 'file_blocks', '') or '')):
            if part.strip() and part not in seen:
                seen.add(part)
                parts.append(part)
    return join_file_block_parts(parts)


def _store_file_blocks(conn, beats, actor: str = '') -> int:
    """
    push 之后：把这批拍带来的顶层块写进 `file_blocks`（整库一份）。

    空文本 → **不动库里那份**：那张隐藏表是工具维护的搬运工，用户删掉它
    多半只是"另存丢了"，不代表"我要删 init python"。真删顶层块请改
    `.rpy` —— 那才是它的真源。
    """
    text = _merged_blocks(beats)
    if not text.strip():
        return 0
    conn.execute(
        'INSERT INTO file_blocks(label, text, updated_by, updated_at) '
        'VALUES(?,?,?,?) ON CONFLICT(label) DO UPDATE SET '
        'text=excluded.text, updated_by=excluded.updated_by, '
        'updated_at=excluded.updated_at',
        (LIBRARY_BLOCKS, text, actor or '', _now()))
    return 1


def load_file_blocks(conn) -> dict:
    """`file_blocks` 表 → `{键: 合并文本}`（新库只有一个键：`LIBRARY_BLOCKS`）。"""
    return {str(r['label']): str(r['text'] or '')
            for r in conn.execute('SELECT label, text FROM file_blocks')}


def _attach_file_blocks(beats, blocks: dict) -> list:
    """
    `{键: 文本}` → 挂在**第一张拍**上（渲染器与 Excel 导出读拍列表）。

    为什么挂第一拍：`Beat.file_blocks` 是**传输副本**，真源是
    `ChapterDocument.file_blocks`（见 `story.chapter`）。挂哪一拍只影响
    "谁能把它带出去"；挂第一拍是既有契约（渲染器按拍序收集），而且
    **不会因为删掉某一张拍而丢** —— 每次都是从表里取一份新的挂上去。

    老库兼容：以前按章节名分过行（schema 2 的早期版本），那就按章节名挂。
    """
    if not blocks:
        return beats
    text = str(blocks.get(LIBRARY_BLOCKS) or '')
    if text.strip() and beats:
        beats[0].file_blocks = text
        return beats
    first = {}
    for b in beats:
        first.setdefault(str(getattr(b, 'label', '') or ''), b)
    for label, value in blocks.items():
        beat = first.get(str(label))
        if beat is not None and str(value or '').strip():
            beat.file_blocks = str(value)
    return beats


def _migrate_file_blocks(conn) -> None:
    """
    schema 1 的库：`FileBlocks` 还在某一行 payload 里 → 搬进 `file_blocks`。

    幂等：`meta.file_blocks_split` 记一笔，之后不再全表扫。
    老数据里同一段块可能同时挂在多张拍上（复制粘贴/撤销恢复），
    这里按块去重合并成一份。
    """
    done = conn.execute(
        "SELECT value FROM meta WHERE key='file_blocks_split'").fetchone()
    if done and str(done[0]) == '1':
        return
    rows = conn.execute('SELECT line_id, label, payload FROM beats').fetchall()
    changed = []
    for r in rows:
        try:
            payload = json.loads(r['payload'] or '{}')
        except (TypeError, ValueError):
            continue
        if _FILE_BLOCKS_KEY not in payload:
            continue
        text = str(payload.pop(_FILE_BLOCKS_KEY) or '')
        changed.append((json.dumps(payload, ensure_ascii=False), r['line_id'],
                        text))
    for payload_text, lid, _text in changed:
        conn.execute('UPDATE beats SET payload=? WHERE line_id=?',
                     (payload_text, lid))
    merged = _merged_blocks([Beat(file_blocks=text) for _p, _l, text in changed])
    if merged.strip():
        conn.execute(
            'INSERT INTO file_blocks(label, text, updated_by, updated_at) '
            'VALUES(?,?,?,?) ON CONFLICT(label) DO UPDATE SET text=excluded.text',
            (LIBRARY_BLOCKS, merged, '', _now()))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) "
                 "VALUES('file_blocks_split', '1')")
    if changed:
        _audit(conn, '', 'migrate_file_blocks', '',
               {'rows': len(changed), 'chars': len(merged)})


def _apply_row_identity(beat: Beat, row) -> Beat:
    """
    用 `beats` 表那几个**独立列**校正拍（列是排序/身份的权威）。

    payload 是内容的投影，`line_id` / `label` / `seq` 三列是数据库的索引；
    两者不一致时以列为准，否则 export/pull 出来的表会带着旧序号。
    """
    beat.line_id = str(row['line_id'] or beat.line_id or '')
    beat.label = str(row['label'] or beat.label or '')
    try:
        beat.seq = int(row['seq'] if row['seq'] is not None else beat.seq)
    except (TypeError, ValueError) as e:            # noqa: BLE001 - 坏 seq 不该打不开库
        swallow('collab_store._apply_row_identity.seq', e)
    return beat



# ==================================================================
#  push / pull（Excel ↔ DB）
# ==================================================================

def pull(db_path, sheet_path, actor: str = '', registry=None,
         char_limit: int = 0) -> dict:
    """
    数据库 → Excel（前端）。写 Beats + 可选候选表 + 协作信息表。

    协作信息表带上每行的 Revision / LockedBy / Status，
    这样下一次 push 能判断"我这份是不是基于最新版改的"。
    """
    with connect(db_path) as conn:
        ensure_user(conn, actor)
        rows = conn.execute(
            'SELECT * FROM beats WHERE deleted = 0 ORDER BY label, seq'
        ).fetchall()
        blocks = load_file_blocks(conn)

    beats = [_apply_row_identity(beat_from_payload(json.loads(r['payload'])), r)
             for r in rows]
    for b in beats:
        b.line_id = b.line_id or ''
    assign_identity(beats)
    # 章节级顶层块：从**独立表**补回拍上（payload 里已经没有它了）
    _attach_file_blocks(beats, blocks)

    out = Path(sheet_path)
    export_beats_to_excel(beats, out, registry=registry)

    # 追加协作信息表
    import openpyxl
    wb = openpyxl.load_workbook(out)
    if COLLAB_SHEET in wb.sheetnames:
        del wb[COLLAB_SHEET]
    ws = wb.create_sheet(COLLAB_SHEET)
    ws.append(COLLAB_FIELDS)
    #: 每一行"拉取时它长什么样" = 三方合并的 base（见 `COLLAB_FIELDS` 注释）。
    #: 用它而不是数据库里的 revision 号当 base：revision 只能说"变没变"，
    #: 说不出"哪个字段变了" —— 而后者才决定两条改动打不打架。
    base_by_id = {b.line_id: _beat_to_payload(b) for b in beats}
    base_forms = {'full': 0, 'hashed': 0, 'none': 0}
    for r in rows:
        lid = r['line_id']
        base_json, how = _base_cell_text(base_by_id.get(lid)
                                         or json.loads(r['payload']))
        base_forms[how] = base_forms.get(how, 0) + 1
        ws.append([lid, r['revision'], r['locked_by'] or '',
                   r['status'], r['updated_by'], r['updated_at'], base_json])
    # 拉取时间（两方比较降级时要说"这份表是什么时候的"）
    ws.cell(row=ws.max_row + 2, column=1, value=PULLED_AT_KEY)
    ws.cell(row=ws.max_row, column=2, value=_now())
    for col, w in zip('ABCDEFG', (16, 10, 14, 12, 14, 20, 60)):
        ws.column_dimensions[col].width = w
    wb.save(out)

    with connect(db_path) as conn:
        _audit(conn, actor, 'pull', '', {'sheet': str(out),
                                        'rows': len(beats),
                                        'base_forms': base_forms})
    return {'sheet': str(out), 'rows': len(beats), 'base_forms': base_forms}


def read_collab_sheet(sheet_path) -> dict:
    """
    读协作信息表 → `{line_id: {'revision', 'locked_by', 'status', 'base'}}`。

    `base` 是**拉取那一刻这一行的 payload**（老表没有这一列 → `None`）。
    `None` 不等于"没变"：它表示"这份表说不清基线"，`push` 会降级成两方比较。
    """
    import openpyxl

    sheet_path = Path(sheet_path)
    if not sheet_path.exists():
        return {}
    wb = openpyxl.load_workbook(sheet_path, data_only=True)
    try:
        if COLLAB_SHEET not in wb.sheetnames:
            return {}
        ws = wb[COLLAB_SHEET]
        headers = [c.value for c in ws[1]]
        out = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            data = {h: row[i] if i < len(row) else None
                    for i, h in enumerate(headers) if h}
            lid = data.get('LineId')
            if not lid:
                continue
            # Excel 里手敲的 ID 常常带着首尾空格（复制粘贴/输入法）：
            # 不归一就会"这一行查不到基线"，看起来像工具在乱报冲突。
            lid = str(lid).strip()
            if not lid or lid == PULLED_AT_KEY:
                continue          # 表尾那行是"拉取时间"标记，不是数据行
            try:
                revision = int(data.get('Revision') or 0)
            except (TypeError, ValueError) as e:     # noqa: BLE001 - 手改坏了当没基线
                swallow('collab_store.read_collab_sheet.revision', e)
                revision = 0
            out[lid] = {
                'revision': revision,
                'locked_by': str(data.get('LockedBy') or ''),
                'status': str(data.get('Status') or STATUS_DRAFT),
                'base': _parse_base(data.get(BASE_FIELD)),
            }
        return out
    finally:
        wb.close()


def _parse_base(raw):
    """`BasePayload` 单元格 → dict（空 / 坏 JSON → `None` = 没有基线）。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        got = json.loads(text)
    except (TypeError, ValueError) as e:          # noqa: BLE001 - 手改坏了当没基线
        swallow('collab_store._parse_base', e)
        return None
    return got if isinstance(got, dict) and got else None


# ==================================================================
#  基线：**一定能塞进一个单元格**（2026-10-18，B 轮）
# ==================================================================
#
# 事故：`BasePayload` 是 `json.dumps(整行 payload)` —— 一句 3 万字的台词，
# 加上 JSON 的引号/换行转义就顶到 32767，Excel **静默截断**（实测 char 592
# 就断了），`_parse_base` 拿到半个 JSON → `None` → 退化两方比较 →
# 字段级合并从此失效（"第二次协作起就坏"）。
#
# 修法：基线只需要回答**一个**问题 —— "这个字段还是我拉取时那样吗"。
# 那是**相等性**判断，哈希就能答，而且固定长度、永远塞得下。
# 所以：JSON 装得下就存原文（人可读、可排查）；
#       装不下就存「每字段 sha1」，合并语义**一字不差**（仍按字段三方比较）。
# 冲突界面从来不显示 base（用户 2026-10-19 定的），所以摘要化不损失任何
# 用户可见信息。

#: 摘要基线的形状：`{'__hashes__': {'Text': '…', 'Speaker': '…'}}`
BASE_HASHES_KEY = '__hashes__'
#: 字段摘要的标记前缀（私用区字符 —— 策划打不出来，不会与正文混淆）。
HASH_TAG = '\ue006'
#: `BasePayload` 单元格的预算。必须**明显小于** `CELL_TEXT_LIMIT`：
#: 单元格上限是硬红线的最后一道，不该让基线贴着它走。
BASE_CELL_BUDGET = CELL_TEXT_LIMIT - 1500


def _field_hash(value) -> str:
    """字段值 → 定长摘要（`HASH_TAG` + 16 位 hex）。"""
    text = '' if value is None else str(value)
    return HASH_TAG + hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]


def _base_cell_text(payload) -> tuple:
    """
    一行 payload → 写进 `BasePayload` 单元格的 JSON。返回 `(文本, 形态)`。

    形态：`'full'`（原文，能读）/ `'hashed'`（每字段摘要）/ `'none'`（连摘要都放不下）。

    ⚠ 绝不再返回"会被截断的半个 JSON"：`'none'` 时写空字符串，push 会
    明确报"这份表没有这一行的基线"，比拿半截 JSON 去猜好一万倍。
    """
    full = {k: _norm_value(v) for k, v in (payload or {}).items()
            if k not in _MERGE_SKIP}
    text = json.dumps(full, ensure_ascii=False)
    if len(text) <= BASE_CELL_BUDGET:
        return text, 'full'
    text = json.dumps({BASE_HASHES_KEY: {k: _field_hash(v)
                                         for k, v in full.items()}},
                      ensure_ascii=False)
    if len(text) <= BASE_CELL_BUDGET:
        return text, 'hashed'
    return '', 'none'


def _base_same(base_entry, value) -> bool:
    """基线里的这一项 == 现在的值吗（摘要基线和原文基线都认）。"""
    b = '' if base_entry is None else str(base_entry)
    v = '' if value is None else str(value)
    if b.startswith(HASH_TAG):
        return _field_hash(v) == b
    return b == v


def _payload_matches_base(base, payload) -> bool:
    """整行是否**与基线逐字段相同**（摘要基线也认）。"""
    b, p = _norm_payload(base), _norm_payload(payload)
    return all(_base_same(b.get(k, ''), p.get(k, '')) for k in p)


def read_pulled_at(sheet_path) -> str:
    """协作信息表里记的拉取时间（空 = 老表 / 没有）。"""
    import openpyxl

    sheet_path = Path(sheet_path)
    if not sheet_path.exists():
        return ''
    wb = openpyxl.load_workbook(sheet_path, data_only=True)
    try:
        if COLLAB_SHEET not in wb.sheetnames:
            return ''
        ws = wb[COLLAB_SHEET]
        for row in ws.iter_rows(values_only=True):
            if row and str(row[0] or '').strip() == PULLED_AT_KEY:
                return str(row[1] or '').strip() if len(row) > 1 else ''
        return ''
    finally:
        wb.close()


#: 字段名 → 给人看的名字（冲突界面按**字段**说事，比整行 precise）
FIELD_LABELS = {
    'Text': '台词', 'Speaker': '说话人', 'Kind': '类型', 'Comment': '备注',
    'Condition': '条件', 'Scene': '场景', 'Show': '显示', 'At': '位置',
    'With': '转场', 'Hide': '隐藏', 'BGM': '音乐', 'BGMParams': '音乐参数',
    'SFX': '音效', 'SFXParams': '音效参数', 'Voice': '语音', 'Stage': '舞台',
    'CodeBlock': '代码块', 'StageOrder': '舞台顺序', 'OpOrder': '操作顺序',
    'CodeBlocks': '代码边界', 'Nest': '结构位', 'Inline': '行内',
    'Opaque': '保真块', 'SpecName': '语句名', 'SpecHead': '语句头',
    'LoopHeader': '循环头', 'CameraHeader': '相机头', 'SayTail': '行尾',
    'LabelHeader': '章节头', 'MenuName': '菜单名', 'MenuArgs': '菜单参数',
    'Seq': '序号', 'Label': '章节', 'FileBlocks': '文件级保真',
}


def field_label(field: str) -> str:
    """字段 → 中文名（没有登记就原样返回 —— 不假装认识）。"""
    return FIELD_LABELS.get(str(field or ''), str(field or ''))


def _norm_value(value) -> str:
    """payload 里的一个值 → 可比的形式（`None` 与 `''` 是同一种"空"）。"""
    if value is None:
        return ''
    return str(value)


def _norm_payload(payload) -> dict:
    """
    整行 payload → `{列: 字符串}`（只留列契约里的键）。

    两种形状都认：

      · 原文基线 `{'Text': '台词…', …}`（老表 / 短行）；
      · 摘要基线 `{'__hashes__': {'Text': '\\ue006…', …}}`（长行，
        见 `_base_cell_text`）—— 拆开内层，值保持"摘要 token"，
        比较时由 `_base_same` 认。

    ⚠ `FileBlocks` **不在列契约里**（2026-10-18，schema 2 起住独立表）：
    老 payload 里残留的那一个键不参与比较，否则会凭空多出"每行都变了"。
    """
    payload = payload or {}
    if BASE_HASHES_KEY in payload:
        payload = payload.get(BASE_HASHES_KEY) or {}
    out = {}
    for key in BEAT_FIELDS:
        if key in _MERGE_SKIP:
            continue
        out[key] = _norm_value(payload.get(key))
    return out


def _payload_equal(a, b) -> bool:
    """两行内容一样吗（按列契约比，不比派生列）。"""
    return _norm_payload(a) == _norm_payload(b)


def merge_payloads(base, mine, theirs) -> tuple:
    """
    字段级三方合并（2026-10-19，C1）。

    对每个字段 f：

        m == t → 取 m        （两边改成一样）
        m == b → 取 t        （只有对方改）
        t == b → 取 m        （只有我改）
        否则   → 冲突        （两边都改了同一字段，算法替不了人）

    ⚠ 判据是**字段**不是整行：A 改台词、B 改同一行的音乐，从前整行拒绝，
    现在两处都静默合进来。真正的冲突只剩"同一句的同一格两边各写了一个版本"。

    ⚠ `b` 可能是**摘要**（长行的基线存成 `\\ue006 + sha1`）：相等性判断走
    `_base_same`，语义与原文基线完全一致（摘要只用来比"变没变"）。

    返回 `(merged, conflicts)`；`conflicts` 每项 `{'field','base','mine','theirs'}`
    —— 界面按**字段**显示（比 git 的 hunk 精确，也不用给编剧看 base）。
    """
    base = _norm_payload(base)
    mine = _norm_payload(mine)
    theirs = _norm_payload(theirs)
    merged = {}
    conflicts = []
    for key in mine:                      # 键集合 = 列契约（三边同源）
        b, m, t = base.get(key, ''), mine.get(key, ''), theirs.get(key, '')
        if m == t:
            merged[key] = m
        elif _base_same(b, m):
            merged[key] = t
        elif _base_same(b, t):
            merged[key] = m
        else:
            conflicts.append({'field': key, 'base': b, 'mine': m, 'theirs': t})
            merged[key] = m               # 占位：有冲突时这一行不写库
    return merged, conflicts


def auto_merge_summary(result: dict) -> str:
    """
    push 结果 → 一句人话（"自动合并了 N 行 / M 处；K 句要你决定"）。

    为什么必须报（用户 2026-10-19）：

    > git merge 成功也会说"自动合并了 N 个文件"。这个反馈不能省，
    > 否则用户不知道工具替她做了什么。
    """
    merged = len(result.get('merged') or ())
    fields = int(result.get('auto_merged_fields') or 0)
    conflicts = len([c for c in (result.get('conflicts') or ())
                     if str(c.get('kind') or 'fields') != 'structure'])
    parts = []
    if merged:
        parts.append(f'自动合并了 {merged} 行 / {fields} 处')
    if conflicts:
        parts.append(f'{conflicts} 句要你决定')
    if result.get('degraded'):
        parts.append('这份表没有基线，只能比谁新（不能自动合并）')
    if not parts:
        return '没有需要合并的改动'
    return '；'.join(parts)


def push(db_path, sheet_path, actor: str, force: bool = False,
         only=None) -> dict:
    """
    Excel（前端）→ 数据库。**冲突判据 = 字段级三方比较**（2026-10-19，C1）。

    规则：
      · 行被别人锁着            → 拒（除非 force）
      · 有 base（拉取时的 payload）→ 逐字段三方合并：
          只有一方改的字段**静默合进来**；两边都改同一格才算冲突，列出来。
      · 没有 base（老表 / 隐藏表被 Excel 另存丢掉）→ **降级成两方比较**：
          库里的 revision 比你拉取时新就拒绝，并明确说"这次只能比谁新"。
      · 你在 Excel 新建的行      → 插入；与 TA 新插的行**同位置**（同 Label+Seq）
          → 冲突（可以"两句都留"）
      · 表里没有的行            → 软删除；但 TA 改过的那句**保留**并记一条
        "你删过这句"（默认不丢别人的字）
      · 软删除的行又出现在表里   → 若你没改过它（= 旧表），不复活；改过才复活
      `only=[...]`：**部分提交** —— 只写这些行，也不扫删除（其余的回头再说）。

    返回 `{'added','updated','unchanged','revived','merged','kept',
    'skipped_stale','conflicts','rejected','auto_merged_fields'}`。
    """
    beats = import_beats_from_excel(sheet_path)
    assign_identity(beats)
    base = read_collab_sheet(sheet_path)
    has_baseline_sheet = bool(base)
    #: 表里有行、但**没有一行带内容基线** = 旧版 Excel 另存把 BasePayload 丢了。
    #: 这时只能比谁新（降级），并且必须把这件事说出来。
    has_any_base = any(bool(v.get('base')) for v in base.values())
    only_set = ({str(x).strip() for x in only if str(x).strip()}
                if only else None)

    result = {'added': [], 'updated': [], 'unchanged': [], 'revived': [],
              'merged': [], 'kept': [], 'skipped_stale': [],
              'conflicts': [], 'rejected': [], 'auto_merged_fields': 0,
              'degraded': False}

    with connect(db_path, write=True) as conn:
        role = _actor_role(conn, actor)
        # **成员权限**（2026-10-19）：viewer 只读；force 只有 owner 能用。
        if not _can(role, 'force' if force else 'push'):
            why = (f'只读成员不能 push（你是 {role}）'
                   if not force else
                   f'只读成员不能强制写入（你是 {role}）')
            result.update({'ok': False, 'error': why, 'permission_denied': True,
                           'role': role})
            _audit(conn, actor, 'push_permission_denied', '', {'why': why})
            return result
        # 章节级顶层块：**不进 payload**（那正是"第二次协作就坏"的根因），
        # 从拍上收下来存进 `file_blocks`（只增改、不删，见函数说明）。
        _store_file_blocks(conn, beats, actor)
        existing = {r['line_id']: r for r in
                    conn.execute('SELECT * FROM beats').fetchall()}
        # TA 在我拉取之后新插的行：库里有、我的基线里没有。
        # 用它来判断"两边各插一行在同一个位置"。
        ta_inserted = {}
        for lid, r in existing.items():
            if r['deleted'] or lid in base:
                continue
            ta_inserted[(str(r['label'] or ''), int(r['seq'] or 0))] = r

        for beat in beats:
            lid = beat.line_id
            if only_set is not None and lid not in only_set:
                continue
            row = existing.get(lid)
            if row is not None:
                # 空 Label / 0 号 Seq 是"这一格没填"，不是"改成空/改成 0"：
                # 用数据库里那一列兜住，否则一次手滑（清空序号格）就把
                # 整行的顺序写到 0 —— 而它可能只是没看见那一列。
                if not str(beat.label or '').strip():
                    beat.label = str(row['label'] or '')
                try:
                    if int(beat.seq or 0) <= 0:
                        beat.seq = int(row['seq'] or 0)
                except (TypeError, ValueError) as e:     # noqa: BLE001
                    swallow('collab_store.push.seq_fallback', e)
            payload = _beat_to_payload(beat)
            chash = beat.beat_fingerprint

            if row is None:
                # **同位置各插一行**（2026-10-19，C3）：不静默排成两行同 Seq
                slot = (str(beat.label or ''), int(beat.seq or 0))
                clash = ta_inserted.get(slot)
                if clash is not None and not force:
                    _audit(conn, actor, 'conflict_insert', lid,
                           {'theirs': clash['line_id'], 'seq': slot[1]})
                    result['conflicts'].append({
                        'kind': 'insert_same_slot', 'reason': 'insert_same_slot',
                        'line_id': lid, 'label': slot[0], 'seq': slot[1],
                        'theirs_line_id': clash['line_id'],
                        'updated_by': clash['updated_by'],
                        'mine': payload,
                        'theirs': json.loads(clash['payload']),
                        'hint': '你和 TA 在同一个位置各插了一句 —— '
                                '可以"两句都留"（自动排成一前一后）',
                    })
                    continue
                conn.execute(
                    'INSERT INTO beats(line_id,label,seq,payload,content_hash,'
                    'status,revision,updated_by,updated_at) '
                    'VALUES(?,?,?,?,?,?,1,?,?)',
                    (lid, beat.label, beat.seq,
                     json.dumps(payload, ensure_ascii=False), chash,
                     STATUS_DRAFT, actor, _now()))
                _audit(conn, actor, 'add', lid, {'label': beat.label})
                result['added'].append(lid)
                continue

            # 锁：别人锁着就不能改；**过期租约不再阻塞**（force 只该在
            # 明确沟通过后使用，且 force 本身已经限定 owner）。
            locker = row['locked_by']
            if not force:
                free, why = _lock_is_free_for(row, actor)
                if not free:
                    _audit(conn, actor, 'push_rejected_locked', lid,
                           {'locked_by': locker})
                    result['rejected'].append(
                        {'line_id': lid, 'reason': why})
                    continue
                if locker and locker != actor:
                    # 过期锁被越过：留痕，但不当成"抢锁"（租约本来就允许）
                    _audit(conn, actor, 'push_lock_expired', lid,
                           {'locked_by': locker, 'why': why})

            theirs_payload = json.loads(row['payload'])
            base_payload = (base.get(lid) or {}).get('base')

            # **软删除的行又出现在表里**：老表是"用户要它回来"，
            # 但有了 base 就能分清"我真的要它回来"和"我手上还是删除前那份旧表"。
            if row['deleted']:
                if (not force and base_payload is not None
                        and _payload_matches_base(base_payload, payload)):
                    result['skipped_stale'].append(lid)
                    continue
                conn.execute(
                    'UPDATE beats SET deleted=0, payload=?, content_hash=?, '
                    'label=?, seq=?, revision=revision+1, updated_by=?, '
                    'updated_at=? WHERE line_id=?',
                    (json.dumps(payload, ensure_ascii=False), chash,
                     beat.label, beat.seq, actor, _now(), lid))
                _audit(conn, actor, 'revive', lid, {})
                result['revived'].append(lid)
                continue

            # `Seq` / `Label` **不在内容指纹里**（否则插一行会让后面所有行
            # 都"内容变了"），所以它们要单独比 —— 只调了顺序 / 换了章节时，
            # 内容哈希一样，但数据库那两列必须跟着走，否则 pull 出来的
            # 顺序还是旧的（Excel 里拖动排序永远不生效）。
            meta_changed = (int(row['seq'] or 0) != int(beat.seq or 0)
                            or str(row['label'] or '') != str(beat.label or ''))
            if row['content_hash'] == chash and not meta_changed:
                result['unchanged'].append(lid)
                continue

            # ---------- 有 base：字段级三方合并 ----------
            if not force and base_payload is not None:
                merged, field_conflicts = merge_payloads(base_payload, payload,
                                                         theirs_payload)
                if field_conflicts:
                    _audit(conn, actor, 'conflict_fields', lid,
                           {'fields': [c['field'] for c in field_conflicts],
                            'db_revision': row['revision']})
                    result['conflicts'].append({
                        'kind': 'fields', 'reason': 'fields',
                        'line_id': lid, 'label': row['label'],
                        'db_revision': row['revision'],
                        'updated_by': row['updated_by'],
                        'fields': field_conflicts,
                        'mine': payload, 'theirs': theirs_payload,
                        'base': base_payload,
                        'hint': '你和 TA 改了同一句的同一格 —— 选一个，或者两句都留',
                    })
                    continue
                if _payload_equal(merged, theirs_payload):
                    # 合出来就是库里那份 = 我其实没改动任何要写的东西
                    result['unchanged'].append(lid)
                    continue
                got = _write_merged(conn, lid, row['revision'], merged,
                                    actor, payload, theirs_payload)
                if got is None:
                    result['conflicts'].append({
                        'kind': 'race', 'reason': 'race', 'line_id': lid,
                        'label': row['label'], 'db_revision': row['revision'],
                        'updated_by': row['updated_by'], 'fields': [],
                        'mine': payload, 'theirs': theirs_payload,
                        'base': base_payload,
                        'hint': '库在你合并的这一刻又被改了 —— 重新提交一次就好',
                    })
                    continue
                result['updated'].append(lid)
                if got:
                    # `merged` = `updated` 里"确实从对方那里合进了字段"的那些
                    # （只调了顺序、内容全是自己写的 → 只算 updated）
                    result['merged'].append(lid)
                    result['auto_merged_fields'] += got
                continue

            # ---------- 没有 base：两方比较（降级，明确告知） ----------
            base_row = base.get(lid) or {}
            base_rev = int(base_row.get('revision') or 0)
            if not force and (not has_baseline_sheet or base_rev <= 0):
                _audit(conn, actor, 'conflict_no_baseline', lid,
                       {'db_revision': row['revision']})
                result['degraded'] = True
                result['conflicts'].append({
                    'kind': 'no_baseline', 'reason': 'no_baseline',
                    'line_id': lid, 'label': row['label'],
                    'db_revision': row['revision'],
                    'base_revision': 0,
                    'updated_by': row['updated_by'],
                    'fields': [], 'mine': payload, 'theirs': theirs_payload,
                    'base': {},
                    'hint': '这份表没有基线（不是 pull 出来的，或用旧版 Excel '
                            '另存丢了「协作信息」表）：这次只能比谁新，'
                            '不能自动合。先 pull 一份再改；确实要整表覆盖请用 --force',
                })
                continue
            if not force and not has_any_base:
                # 表在、revision 也在，但**每一行的内容基线**没了（旧版 Excel
                # 另存会丢隐藏列）。这时只能比谁新：
                #   · 库里 revision 没动 → 我的改动是唯一改动，写下去是安全的；
                #   · 库里动过 → 说不清两边改了哪儿，必须人看。
                result['degraded'] = True
                if row['revision'] > base_rev:
                    _audit(conn, actor, 'conflict_no_base_payload', lid,
                           {'db_revision': row['revision'],
                            'base_revision': base_rev})
                    result['conflicts'].append({
                        'kind': 'no_baseline', 'reason': 'no_baseline',
                        'line_id': lid, 'label': row['label'],
                        'db_revision': row['revision'],
                        'base_revision': base_rev,
                        'updated_by': row['updated_by'],
                        'fields': [], 'mine': payload,
                        'theirs': theirs_payload, 'base': {},
                        'hint': '这份表没有每一行的基线内容（「协作信息」里 '
                                'BasePayload 不见了，可能用旧版 Excel 另存过）：'
                                '这次只能比谁新，不能自动合并。先 pull 一份再改，'
                                '或确认后覆盖（--force）',
                    })
                    continue
            if not force and row['revision'] > base_rev:
                _audit(conn, actor, 'conflict', lid,
                       {'db_revision': row['revision'],
                        'base_revision': base_rev})
                result['conflicts'].append({
                    'kind': 'revision', 'reason': 'revision',
                    'line_id': lid, 'label': row['label'],
                    'db_revision': row['revision'],
                    'base_revision': base_rev,
                    'updated_by': row['updated_by'],
                    'fields': [], 'mine': payload, 'theirs': theirs_payload,
                    'base': {},
                    'hint': '这份表没有这一行的基线内容（老表）：只能比谁新 —— '
                            '库里的版本比你拉取时新。先 pull 并比对，'
                            '或用 --force 覆盖',
                })
                continue

            conn.execute(
                'UPDATE beats SET payload=?, content_hash=?, label=?, seq=?, '
                'deleted=0, revision=revision+1, updated_by=?, updated_at=? '
                'WHERE line_id=?',
                (json.dumps(payload, ensure_ascii=False), chash, beat.label,
                 beat.seq, actor, _now(), lid))
            _audit(conn, actor, 'update', lid, {'revision': row['revision'] + 1,
                                                'meta_only': row['content_hash'] == chash})
            result['updated'].append(lid)

        # 表里没有的行 = 被删掉了。**但别人改过的那些要留住**（C3）：
        # 你删的是你看到的那一版，TA 后来写的字不该跟着消失。
        if only_set is None:
            sheet_ids = {b.line_id for b in beats}
            labels_kept = {}
            for lid, row in existing.items():
                if row['deleted'] or lid in sheet_ids:
                    continue
                free, _why = _lock_is_free_for(row, actor)
                if not free and not force:
                    continue
                base_payload = (base.get(lid) or {}).get('base')
                if not force and base_payload is not None:
                    theirs_payload = json.loads(row['payload'])
                    if not _payload_matches_base(base_payload, theirs_payload):
                        result['kept'].append({
                            'line_id': lid, 'label': row['label'],
                            'updated_by': row['updated_by'],
                            'reason': 'deleted_by_you_but_changed',
                            'hint': f'你删过 {lid}，但 {row["updated_by"] or "TA"} '
                                    '改过它 —— 已保留对方的修改'
                                    '（确实要删请用 --force）',
                        })
                        labels_kept[str(row['label'] or '')] = \
                            labels_kept.get(str(row['label'] or ''), 0) + 1
                        continue
                conn.execute('UPDATE beats SET deleted=1, updated_by=?, '
                             'updated_at=?, revision=revision+1 WHERE line_id=?',
                             (actor, _now(), lid))
                _audit(conn, actor, 'delete', lid, {})
            # **整段删除 + 对方在里面加了句子** = 结构冲突（要人）：
            # 保住数据，但明确告诉人"你删的这段里对方动过 N 句"。
            for label, n in labels_kept.items():
                if n < 2:
                    continue
                result['conflicts'].append({
                    'kind': 'structure', 'reason': 'structure',
                    'line_id': '', 'label': label, 'kept': n,
                    'updated_by': '',
                    'hint': f'你删了「{label}」这一段，但里面 {n} 句对方改过 —— '
                            '已经全部保留。确实要整段删掉请用 --force 重新提交',
                    'fields': [], 'mine': {}, 'theirs': {}, 'base': {},
                })

    return result


def _write_merged(conn, lid, base_revision, merged, actor, mine, theirs):
    """
    把合出来的行写进库（**带 revision CAS**：`WHERE revision=?`）。

    返回"从对方那里合进来的字段数"；`rowcount == 0` 表示库在这一刻又变了
    （别人抢在前面写了同一行）→ 返回 `None`，调用方报冲突让人刷新重试。
    从前 `UPDATE ... WHERE line_id=?` 没有 CAS：两个人同时 push 时后写的
    静默覆盖前写的，审计里还写着"更新成功"。
    """
    beat = beat_from_payload(merged)
    beat.line_id = lid
    payload = _beat_to_payload(beat)
    cur = conn.execute(
        'UPDATE beats SET payload=?, content_hash=?, label=?, seq=?, '
        'deleted=0, revision=revision+1, updated_by=?, updated_at=? '
        'WHERE line_id=? AND revision=?',
        (json.dumps(payload, ensure_ascii=False), beat.beat_fingerprint,
         beat.label, beat.seq, actor, _now(), lid, int(base_revision)))
    if cur.rowcount == 0:
        return None
    mine_n = _norm_payload(mine)
    merged_n = _norm_payload(payload)
    taken = sum(1 for k, v in merged_n.items() if v != mine_n.get(k, ''))
    _audit(conn, actor, 'merge', lid,
           {'revision': int(base_revision) + 1, 'fields_from_theirs': taken})
    return taken

# ==================================================================
#  冲突处理：把界面上的选择落地（C2）
# ==================================================================

def resolve_conflicts(db_path, sheet_path, actor: str, resolutions) -> dict:
    """
    把「你和 TA 改了同一句」里用户的选择写回库 + 本地表（**部分提交**）。

    `resolutions` 每项：

        {'line_id': 'ch01.0002', 'kind': 'fields',
         'values': {'Text': 'mine' | 'theirs' | '最终文本', …}}
        {'line_id': 'ch01.0002', 'kind': 'keep_both',
         'order': 'mine_first' | 'theirs_first'}
        {'line_id': 'ch01.0100', 'kind': 'insert_both', 'order': …,
         'theirs_line_id': '…'}          # 两边同位置各插一行 → 两句都留
        {'line_id': 'ch01.0100', 'kind': 'insert_drop'}   # 用对方那行，我的不留

    为什么要"部分"（用户 2026-10-19）：

    > 有 3 处冲突、200 行自动合并成功时，应该允许"先把这 200 行提交，
    > 3 处回头再说"。不能因为一处台词打架，整个人的工作都卡住。

    所以这里**只碰被解决的那几行**：写库（带 revision CAS）+ 把最终值写回
    本地 Excel 那一行 + 刷新「协作信息」里的 Revision / BasePayload。
    最后一步不能省 —— 否则下一次 push 会拿旧基线再报一次同样的冲突。
    """
    sheet_path = Path(sheet_path)
    base = read_collab_sheet(sheet_path)
    local = {b.line_id: b for b in import_beats_from_excel(sheet_path)}

    result = {'applied': [], 'both_kept': [], 'errors': [], 'skipped': [],
              'local_rows': {}}          # line_id → 最终 payload（写回 Excel 用）

    with connect(db_path, write=True) as conn:
        role = _actor_role(conn, actor)
        if not _can(role, 'push'):
            result['errors'].append(
                {'line_id': '', 'error': f'只读成员不能解决冲突（你是 {role}）'})
            return result
        rows = {r['line_id']: r
                for r in conn.execute('SELECT * FROM beats').fetchall()}
        for res in resolutions or ():
            lid = str(res.get('line_id') or '').strip()
            kind = str(res.get('kind') or 'fields')
            if kind in ('insert_both', 'insert_drop'):
                got = _resolve_insert(conn, res, local, actor)
                if got.get('error'):
                    result['errors'].append({'line_id': lid,
                                             'error': got['error']})
                elif kind == 'insert_drop':
                    result['skipped'].append(lid)
                    _drop_local_row(sheet_path, lid)
                else:
                    result['applied'].append(lid)
                    result['local_rows'][lid] = got['payload']
                continue
            row = rows.get(lid)
            if row is None:
                result['errors'].append(
                    {'line_id': lid, 'error': '库里没有这一行（可能已被删除）'})
                continue
            beat = local.get(lid)
            if beat is None:
                result['errors'].append(
                    {'line_id': lid, 'error': '本地表里已经没有这一行（先 pull 一份）'})
                continue
            mine = _beat_to_payload(beat)
            theirs = json.loads(row['payload'])
            base_payload = (base.get(lid) or {}).get('base') or {}
            kind = str(res.get('kind') or 'fields')

            if kind == 'keep_both':
                got = _keep_both(conn, lid, row, mine, theirs,
                                 str(res.get('order') or 'mine_first'), actor)
                if got is None:
                    result['errors'].append(
                        {'line_id': lid, 'error': '库在你提交的这一刻又变了 —— 重新来一次'})
                    continue
                result['both_kept'].append(got)
                result['local_rows'][lid] = got['kept']
                result['local_rows'][got['dup_line_id']] = got['dup']
                continue

            merged, conflicts = merge_payloads(base_payload, mine, theirs)
            values = res.get('values') or {}
            undecided = []
            for c in conflicts:
                choice = str(values.get(c['field'], '') or '')
                if choice == 'mine':
                    merged[c['field']] = c['mine']
                elif choice == 'theirs':
                    merged[c['field']] = c['theirs']
                elif choice:
                    merged[c['field']] = choice      # 自己拼的文本
                else:
                    undecided.append(field_label(c['field']))
            if undecided:
                result['errors'].append(
                    {'line_id': lid,
                     'error': f'还没决定：{"、".join(undecided)}'})
                continue
            out = beat_from_payload(merged)
            out.line_id = lid
            payload = _beat_to_payload(out)
            cur = conn.execute(
                'UPDATE beats SET payload=?, content_hash=?, label=?, seq=?, '
                'deleted=0, revision=revision+1, updated_by=?, updated_at=? '
                'WHERE line_id=? AND revision=?',
                (json.dumps(payload, ensure_ascii=False), out.beat_fingerprint,
                 out.label, out.seq, actor, _now(), lid, int(row['revision'])))
            if cur.rowcount == 0:
                result['errors'].append(
                    {'line_id': lid, 'error': '库在你提交的这一刻又变了 —— 重新来一次'})
                continue
            _audit(conn, actor, 'resolve', lid,
                   {'picked': {c['field']: str(values.get(c['field'], ''))
                               for c in conflicts}})
            result['applied'].append(lid)
            result['local_rows'][lid] = payload

    # 本地表：写回最终值；协作信息：刷新 revision / base
    touched = list(result['local_rows'])
    if touched:
        _write_local_rows(sheet_path, result['local_rows'])
        _refresh_collab_rows(db_path, sheet_path, touched)
    return result


def _keep_both(conn, lid, row, mine, theirs, order, actor):
    """
    「两句都留」：原行留一份，另一份作为**新的一行**插在它后面。

    `order='mine_first'` → 原行 = 我的，新行 = TA 的；`theirs_first` 反之。
    新行的 Seq = 原 Seq + 1，并把它后面同 Label 的行**整体顺延**一位 ——
    不这么做两行会撞在同一个 Seq 上，pull 出来的顺序说不准。
    """
    keep_mine = (order != 'theirs_first')
    kept = _norm_payload(mine if keep_mine else theirs)
    dup = _norm_payload(theirs if keep_mine else mine)

    label = str(row['label'] or kept.get('Label') or '')
    try:
        seq = int(row['seq'] or kept.get('Seq') or 0)
    except (TypeError, ValueError):
        seq = 0
    beat = beat_from_payload(kept)
    beat.line_id = lid
    beat.label = label or beat.label
    beat.seq = seq or beat.seq
    kept_payload = _beat_to_payload(beat)
    cur = conn.execute(
        'UPDATE beats SET payload=?, content_hash=?, label=?, seq=?, '
        'deleted=0, revision=revision+1, updated_by=?, updated_at=? '
        'WHERE line_id=? AND revision=?',
        (json.dumps(kept_payload, ensure_ascii=False), beat.beat_fingerprint,
         beat.label, beat.seq, actor, _now(), lid, int(row['revision'])))
    if cur.rowcount == 0:
        return None

    new_seq = seq + 1
    if label:
        conn.execute('UPDATE beats SET seq = seq + 1 WHERE label=? AND seq>=?',
                     (label, new_seq))
    new_lid = _unique_line_id(conn, lid)
    dup['LineId'] = new_lid
    dup['Seq'] = str(new_seq)
    dup['Label'] = label or dup.get('Label', '')
    dbeat = beat_from_payload(dup)
    dbeat.line_id = new_lid
    dbeat.label = dup['Label']
    dbeat.seq = new_seq
    dup_payload = _beat_to_payload(dbeat)
    conn.execute(
        'INSERT INTO beats(line_id,label,seq,payload,content_hash,status,'
        'revision,updated_by,updated_at) VALUES(?,?,?,?,?,?,1,?,?)',
        (new_lid, dbeat.label, new_seq,
         json.dumps(dup_payload, ensure_ascii=False), dbeat.beat_fingerprint,
         STATUS_DRAFT, actor, _now()))
    _audit(conn, actor, 'keep_both', lid,
           {'dup': new_lid, 'order': order, 'seq': new_seq})
    return {'line_id': lid, 'dup_line_id': new_lid, 'seq': new_seq,
            'kept': kept_payload, 'dup': dup_payload}


def _unique_line_id(conn, lid: str) -> str:
    """给"两句都留"的新行找个没人用的 LineId（`ch01.0002~2`、`~3`…）。"""
    for i in range(2, 100):
        cand = f'{lid}~{i}'
        got = conn.execute('SELECT 1 FROM beats WHERE line_id=?', (cand,)).fetchone()
        if got is None:
            return cand
    return f'{lid}~{_now().replace(":", "")}'


def _resolve_insert(conn, res, local, actor) -> dict:
    """
    「两边在同一个位置各插了一行」的两种落地。

    · `insert_both`：我的新行**也**进库，排在我/TA 那行旁边（另一行顺延）；
    · `insert_drop`：用 TA 那行，我的新行不进库（本地也要删掉，
      否则下一次 push 会再报同一个位置）。
    """
    lid = str(res.get('line_id') or '').strip()
    beat = (local or {}).get(lid)
    if beat is None:
        return {'error': '本地表里没有这一行'}
    kind = str(res.get('kind') or '')
    if kind == 'insert_drop':
        _audit(conn, actor, 'insert_drop', lid, {})
        return {'dropped': lid}
    theirs_lid = str(res.get('theirs_line_id') or '').strip()
    other = conn.execute('SELECT * FROM beats WHERE line_id=?',
                         (theirs_lid,)).fetchone() if theirs_lid else None
    if other is None:
        return {'error': 'TA 的那一行已经不在了（可能被删）'}
    label = str(other['label'] or '')
    try:
        base_seq = int(other['seq'] or 0)
    except (TypeError, ValueError):
        base_seq = 0
    order = str(res.get('order') or 'mine_first')
    new_seq = base_seq if order == 'mine_first' else base_seq + 1
    conn.execute('UPDATE beats SET seq = seq + 1 WHERE label=? AND seq>=?',
                 (label, new_seq))
    beat.line_id = lid
    beat.label = label or beat.label
    beat.seq = new_seq
    payload = _beat_to_payload(beat)
    conn.execute(
        'INSERT INTO beats(line_id,label,seq,payload,content_hash,status,'
        'revision,updated_by,updated_at) VALUES(?,?,?,?,?,?,1,?,?)',
        (lid, beat.label, new_seq, json.dumps(payload, ensure_ascii=False),
         beat.beat_fingerprint, STATUS_DRAFT, actor, _now()))
    _audit(conn, actor, 'insert_both', lid,
           {'near': theirs_lid, 'order': order, 'seq': new_seq})
    return {'payload': payload, 'seq': new_seq}


def _drop_local_row(sheet_path, lid: str) -> bool:
    """把本地表里的某一行删掉（`insert_drop`：用 TA 的那行，我的不留）。"""
    import openpyxl

    path = Path(sheet_path)
    if not path.exists():
        return False
    wb = openpyxl.load_workbook(path)
    try:
        ws = wb['Beats']
        cols = _sheet_headers_by_field(ws)
        id_col = cols.get('LineId')
        if not id_col:
            return False
        for r in range(2, ws.max_row + 1):
            if str(ws.cell(row=r, column=id_col).value or '').strip() == lid:
                ws.delete_rows(r)
                wb.save(path)
                return True
        return False
    finally:
        wb.close()


def _sheet_headers_by_field(ws) -> dict:
    """`{BEAT_FIELDS 的键: 列号}`（按表头文字匹配；动态列自然不在里面）。"""
    from storygraph.story.beat_fields import BEAT_HEADERS
    want = {str(BEAT_HEADERS.get(f, f)): f for f in BEAT_FIELDS}
    out = {}
    for cell in ws[1]:
        key = want.get(str(cell.value or '').strip())
        if key and key not in out:
            out[key] = cell.column
    return out


def _row_of_line_id(ws, col: int, lid: str):
    """Beats 表里 LineId = lid 的那一行号（找不到 None）。"""
    for r in range(2, ws.max_row + 1):
        if str(ws.cell(row=r, column=col).value or '').strip() == lid:
            return r
    return None


def _write_local_rows(sheet_path, payloads: dict) -> int:
    """
    把最终 payload 写回本地 Excel 的对应行。

    只改**列契约里的那几格**，不重排、不重建工作簿 —— 用户在这份表上
    还有别的没提交的改动，整表重写会把它们冲掉（这正是"部分提交"要避免的）。
    行不存在（`keep_both` 的新行）→ 追加到末尾。
    """
    if not payloads:
        return 0
    import openpyxl

    wb = openpyxl.load_workbook(sheet_path)
    try:
        ws = wb['Beats']
        cols = _sheet_headers_by_field(ws)
        id_col = cols.get('LineId')
        if not id_col:
            return 0
        n = 0
        for lid, payload in payloads.items():
            r = _row_of_line_id(ws, id_col, lid)
            if r is None:
                r = ws.max_row + 1
            for field, col in cols.items():
                value = payload.get(field)
                if field == 'Seq':
                    try:
                        value = int(value or 0)
                    except (TypeError, ValueError):
                        value = 0
                ws.cell(row=r, column=col, value='' if value is None else value)
            n += 1
        wb.save(sheet_path)
        return n
    finally:
        wb.close()


def _refresh_collab_rows(db_path, sheet_path, line_ids) -> int:
    """
    刷新「协作信息」里那几行的 Revision / BasePayload（= 新的基线）。

    不刷新的后果很具体：下次 push 时本地还是旧 base，同一个字段会被再判
    一次"两边都改了"—— 用户刚选过的东西又冒出来，等于没解决。
    """
    if not line_ids:
        return 0
    import openpyxl

    with connect(db_path) as conn:
        rows = {}
        for lid in line_ids:
            got = conn.execute('SELECT * FROM beats WHERE line_id=?',
                               (str(lid),)).fetchone()
            if got is not None:
                rows[lid] = got
    if not rows:
        return 0

    wb = openpyxl.load_workbook(Path(sheet_path))
    try:
        if COLLAB_SHEET not in wb.sheetnames:
            return 0
        ws = wb[COLLAB_SHEET]
        headers = [str(c.value or '').strip() for c in ws[1]]
        col = {h: i + 1 for i, h in enumerate(headers) if h}
        lid_col = col.get('LineId')
        if not lid_col:
            return 0
        index = {}
        for r in range(2, ws.max_row + 1):
            lid = str(ws.cell(row=r, column=lid_col).value or '').strip()
            if lid:
                index[lid] = r
        n = 0
        for lid, row in rows.items():
            r = index.get(lid)
            if r is None:
                r = ws.max_row + 1
                ws.cell(row=r, column=lid_col, value=lid)
            payload = json.loads(row['payload'])
            # 基线走同一个函数：长行自动存摘要，**永远塞得进单元格**
            # （从前这里直接 json.dumps，一行 64KB 就被 Excel 截成半个 JSON）
            base_json, _how = _base_cell_text(payload)
            values = {
                'Revision': int(row['revision']),
                'LockedBy': row['locked_by'] or '',
                'Status': row['status'],
                'UpdatedBy': row['updated_by'],
                'UpdatedAt': row['updated_at'],
                'BasePayload': base_json,
            }
            for key, value in values.items():
                c = col.get(key)
                if c:
                    ws.cell(row=r, column=c, value=value)
            n += 1
        wb.save(sheet_path)
        return n
    finally:
        wb.close()

# ==================================================================
#  锁
# ==================================================================

def _lease_until(ttl=None) -> tuple:
    """`(现在, 到期时间 ISO)` —— 租约时长的**唯一算法**。"""
    seconds = int(ttl or LOCK_LEASE_SECONDS)
    seconds = max(60, seconds)                 # 低于 1 分钟的租约没有意义
    now = datetime.now()
    return now, (now + timedelta(seconds=seconds)).isoformat(timespec='seconds')


def lock(db_path, line_id: str, actor: str, note: str = '',
         ttl: int = 0) -> dict:
    """
    锁一行（**带租约**，默认 `LOCK_LEASE_SECONDS` = 15 分钟）。

    租约的意义：一个人下班没解锁，不至于让整章永远写不进去。别人可以
    **越过过期锁**写入/上锁，但审计里会留一笔；没过期时照旧拒绝。
    """
    _, until = _lease_until(ttl)
    with connect(db_path, write=True) as conn:
        role = _actor_role(conn, actor)
        if not _can(role, 'lock'):
            return {'ok': False, 'reason': f'只读成员不能上锁（你是 {role}）',
                    'permission_denied': True}
        row = conn.execute(
            'SELECT locked_by, locked_at, locked_until FROM beats '
            'WHERE line_id=?', (line_id,)).fetchone()
        if row is None:
            return {'ok': False, 'reason': f'没有这一行：{line_id}'}
        state = lock_state(row)
        if state['locked'] and state['owner'] != actor and not state['expired']:
            return {'ok': False,
                    'reason': f"已被 {state['owner']} 锁定（"
                              f"{state['expires_in']} 秒后到期）",
                    'expires_in': state['expires_in']}
        if state['locked'] and state['owner'] != actor and state['expired']:
            _audit(conn, actor, 'lock_takeover_expired', line_id,
                   {'previous_owner': state['owner'], 'until': state['until']})
        conn.execute('UPDATE beats SET locked_by=?, locked_at=?, '
                     'locked_until=? WHERE line_id=?',
                     (actor, _now(), until, line_id))
        _audit(conn, actor, 'lock', line_id, note)
    return {'ok': True, 'line_id': line_id, 'owner': actor,
            'expires_at': until, 'lease_seconds': int(ttl or LOCK_LEASE_SECONDS)}


def renew_lock(db_path, line_id: str, actor: str, ttl: int = 0) -> dict:
    """续租（只有持锁人；锁已经过期时允许原持锁人续上）。"""
    _, until = _lease_until(ttl)
    with connect(db_path, write=True) as conn:
        role = _actor_role(conn, actor)
        if not _can(role, 'renew'):
            return {'ok': False, 'reason': f'只读成员不能续租（你是 {role}）'}
        row = conn.execute(
            'SELECT locked_by, locked_at, locked_until FROM beats '
            'WHERE line_id=?', (line_id,)).fetchone()
        if row is None:
            return {'ok': False, 'reason': f'没有这一行：{line_id}'}
        owner = str(row['locked_by'] or '')
        if owner and owner != actor:
            return {'ok': False, 'reason': f'锁属于 {owner}，不能替别人续租'}
        if not owner:
            return {'ok': False, 'reason': '这一行没有锁，先上锁'}
        conn.execute('UPDATE beats SET locked_at=?, locked_until=? '
                     'WHERE line_id=?', (_now(), until, line_id))
        _audit(conn, actor, 'lock_renew', line_id, {'until': until})
    return {'ok': True, 'line_id': line_id, 'owner': actor,
            'expires_at': until}


def unlock(db_path, line_id: str, actor: str, force: bool = False) -> dict:
    with connect(db_path, write=True) as conn:
        role = _actor_role(conn, actor)
        if not _can(role, 'unlock'):
            return {'ok': False, 'reason': f'只读成员不能解锁（你是 {role}）'}
        if force and not _can(role, 'force'):
            return {'ok': False,
                    'reason': f'只读成员不能强制解锁（你是 {role}）'}
        row = conn.execute(
            'SELECT locked_by, locked_at, locked_until FROM beats '
            'WHERE line_id=?', (line_id,)).fetchone()
        if row is None:
            return {'ok': False, 'reason': f'没有这一行：{line_id}'}
        state = lock_state(row)
        if (state['locked'] and state['owner'] != actor and not force
                and not state['expired']):
            return {'ok': False, 'reason': f"锁属于 {state['owner']}"}
        conn.execute('UPDATE beats SET locked_by=NULL, locked_at=NULL, '
                     'locked_until=NULL WHERE line_id=?', (line_id,))
        _audit(conn, actor, 'unlock', line_id, {'force': force})
    return {'ok': True, 'line_id': line_id}


def lock_range(db_path, label: str, actor: str, ttl: int = 0) -> dict:
    """整章上锁：一个文案认领一整章时最省事的方式（同样带租约）。"""
    _, until = _lease_until(ttl)
    with connect(db_path, write=True) as conn:
        role = _actor_role(conn, actor)
        if not _can(role, 'lock'):
            return {'label': label, 'locked': [], 'skipped': [],
                    'ok': False, 'reason': f'只读成员不能上锁（你是 {role}）'}
        ids = [r['line_id'] for r in conn.execute(
            'SELECT line_id FROM beats WHERE label=? AND deleted=0', (label,))]
        locked, skipped = [], []
        for lid in ids:
            row = conn.execute(
                'SELECT locked_by, locked_at, locked_until FROM beats '
                'WHERE line_id=?', (lid,)).fetchone()
            state = lock_state(row)
            if (state['locked'] and state['owner'] != actor
                    and not state['expired']):
                skipped.append(lid)
                continue
            conn.execute('UPDATE beats SET locked_by=?, locked_at=?, '
                         'locked_until=? WHERE line_id=?',
                         (actor, _now(), until, lid))
            locked.append(lid)
        _audit(conn, actor, 'lock_range', label,
               {'locked': len(locked), 'skipped': len(skipped)})
    return {'label': label, 'locked': locked, 'skipped': skipped,
            'expires_at': until}


def locks(db_path) -> list:
    """当前锁列表（含租约状态：`expired` / `expires_in`）。"""
    with connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT b.line_id, b.label, b.locked_by, b.locked_at, '
            'b.locked_until, b.status '
            'FROM beats b WHERE b.locked_by IS NOT NULL AND b.deleted=0 '
            'ORDER BY b.label, b.seq').fetchall()]
    for r in rows:
        state = lock_state(r)
        r['expires_in'] = state['expires_in']
        r['expired'] = state['expired']
        r['lease_until'] = state['until']
    return rows


# ==================================================================
#  状态机 / 查询
# ==================================================================

def set_status(db_path, line_id: str, status: str, actor: str) -> dict:
    if status not in STATUSES:
        return {'ok': False, 'reason': f'状态只能是 {"/".join(STATUSES)}'}
    with connect(db_path, write=True) as conn:
        role = _actor_role(conn, actor)
        action = 'approve' if status == STATUS_APPROVED else 'status'
        if not _can(role, action):
            return {'ok': False,
                    'reason': f'只读成员不能改状态（你是 {role}）'}
        cur = conn.execute('UPDATE beats SET status=?, updated_by=?, '
                           'updated_at=?, revision=revision+1 '
                           'WHERE line_id=?', (status, actor, _now(), line_id))
        if cur.rowcount == 0:
            return {'ok': False, 'reason': f'没有这一行：{line_id}'}
        _audit(conn, actor, 'status', line_id, {'status': status})
    return {'ok': True, 'line_id': line_id, 'status': status}


def upsert_assets(db_path, registry, actor: str = '') -> int:
    """把资源表同步进库（Unity 导出、校验都从这里取同一份）。"""
    if registry is None:
        return 0
    count = 0
    with connect(db_path, write=True) as conn:
        role = _actor_role(conn, actor)
        if not _can(role, 'assets'):
            _audit(conn, actor, 'assets_permission_denied', '',
                   {'role': role})
            return 0
        for attr, kind in (('images', 'image'), ('music', 'music'),
                           ('sounds', 'sound'), ('voices', 'voice'),
                           ('transforms', 'transform'),
                           ('characters', 'character')):
            for aid, row in (getattr(registry, attr, {}) or {}).items():
                conn.execute(
                    'INSERT INTO assets(asset_type, asset_id, payload, '
                    'updated_by, updated_at) VALUES(?,?,?,?,?) '
                    'ON CONFLICT(asset_type, asset_id) DO UPDATE SET '
                    'payload=excluded.payload, updated_by=excluded.updated_by, '
                    'updated_at=excluded.updated_at',
                    (kind, aid, json.dumps(row, ensure_ascii=False, default=str),
                     actor, _now()))
                count += 1
        _audit(conn, actor, 'assets_upsert', '',
               {'count': count})
    return count


def status_summary(db_path) -> dict:
    """协作总览：谁锁了多少、各状态多少、最近改了什么、成员是什么角色。"""
    with connect(db_path) as conn:
        total = conn.execute(
            'SELECT COUNT(*) c FROM beats WHERE deleted=0').fetchone()['c']
        by_status = {s: conn.execute(
            'SELECT COUNT(*) c FROM beats WHERE deleted=0 AND status=?',
            (s,)).fetchone()['c'] for s in STATUSES}
        by_owner = {}
        for r in conn.execute(
                'SELECT locked_by, COUNT(*) c FROM beats '
                'WHERE deleted=0 AND locked_by IS NOT NULL '
                'GROUP BY locked_by').fetchall():
            by_owner[r['locked_by']] = r['c']
        lock_rows = conn.execute(
            'SELECT locked_by, locked_at, locked_until FROM beats '
            'WHERE deleted=0 AND locked_by IS NOT NULL').fetchall()
        expired_total = sum(1 for r in lock_rows if lock_state(r)['expired'])
        recent = [dict(r) for r in conn.execute(
            'SELECT ts, actor, action, line_id, detail FROM audit '
            'ORDER BY id DESC LIMIT 20').fetchall()]
        labels = [dict(r) for r in conn.execute(
            'SELECT label, COUNT(*) total, '
            "SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) approved, "
            "SUM(CASE WHEN status='in_review' THEN 1 ELSE 0 END) in_review, "
            'SUM(CASE WHEN locked_by IS NOT NULL THEN 1 ELSE 0 END) locked '
            'FROM beats WHERE deleted=0 GROUP BY label ORDER BY label'
        ).fetchall()]
        users = [dict(r) for r in conn.execute(
            'SELECT id, display_name, role FROM users ORDER BY id').fetchall()]
    for u in users:
        role = str(u.get('role') or ROLE_EDITOR)
        u['role'] = role if role in ROLES else ROLE_EDITOR
        u['role_label'] = ROLE_LABELS.get(u['role'], u['role'])
    return {
        'lines': total,
        'by_status': by_status,
        'locked_by': by_owner,
        'locked_total': sum(by_owner.values()),
        'expired_locks': expired_total,
        'labels': labels,
        'users': users,
        'recent': recent,
    }


def export_ir_from_db(db_path):
    """从数据库直接造 IR（不经 Excel），给 CI/构建用。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            'SELECT * FROM beats WHERE deleted=0 ORDER BY label, seq'
        ).fetchall()
        assets = conn.execute('SELECT * FROM assets').fetchall()
        blocks = load_file_blocks(conn)
    beats = [_apply_row_identity(beat_from_payload(json.loads(r['payload'])), r)
             for r in rows]
    assign_identity(beats)
    # 顶层块住独立表（`file_blocks`）：渲染器读拍列表，所以要挂回第一拍
    _attach_file_blocks(beats, blocks)
    return beats, [dict(a) for a in assets]
