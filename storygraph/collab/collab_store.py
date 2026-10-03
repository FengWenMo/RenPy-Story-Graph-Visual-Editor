"""
collab_store.py
===============
团队协作后端：SQLite 作为单一真源，Excel 作为编辑前端。

为什么是这个方向（八，也来自调研结论）：
  表格作为唯一的"真相"有三个必然衰减——**没有字段约束**、**状态会烂**、
  **版本互相覆盖**。研究里那句话很准：表格是唯一值得升级成"带必填字段的
  数据库"的地方。但它同时是文案/策划唯一愿意用的界面（占位符：
  本地化厂商也收 xlsx）。所以这里的取舍是：

      Excel = 前端（人编辑的地方）
      SQLite = 后端（真源、约束、锁、审计、状态机）

  · 每次 `push` 都按 LineId 做 upsert，并检查"你拉取时的版本"是否已过期
    → 别人改了同一行，你不会静默覆盖，而是收到冲突清单。
  · 行级锁：`lock` 之后别人 push 这一行会被拒（同一行两个人同时改是
    叙事工具里最贵的错——改了 A 的意思，B 没看到）。
  · 状态机 + 审计：draft → in_review → approved，每次变更留痕。
    这是"字段约束"和"状态不腐烂"的落点。
  · 资产表同样入库（assets），这样 Unity 导出 / 校验都能从同一个库取。

设计原则：本模块不依赖 tkinter，可被 CLI、编辑器、CI 共用。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from storygraph.story.beat_model import BEAT_FIELDS, Beat, assign_identity
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

SCHEMA_VERSION = 1

STATUS_DRAFT = 'draft'
STATUS_REVIEW = 'in_review'
STATUS_APPROVED = 'approved'
STATUSES = (STATUS_DRAFT, STATUS_REVIEW, STATUS_APPROVED)

# push/pull 时携带协作状态的工作表名（不污染 Beats 表的列）
COLLAB_SHEET = '协作信息'
COLLAB_FIELDS = ['LineId', 'Revision', 'LockedBy', 'Status', 'UpdatedBy',
                 'UpdatedAt']

_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
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

@contextmanager
def connect(db_path):
    """打开数据库（自动建表），用完提交并关闭。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_DDL)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),))
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path, users=()) -> Path:
    """建库并登记成员（角色：文案/策划/程序）。"""
    with connect(db_path) as conn:
        for u in users:
            ensure_user(conn, u)
    return Path(db_path)


def ensure_user(conn, user_id: str, display_name: str = '') -> None:
    if not user_id:
        return
    conn.execute(
        'INSERT OR IGNORE INTO users(id, display_name, created_at) '
        'VALUES(?,?,?)', (user_id, display_name or user_id, _now()))


def _audit(conn, actor, action, line_id='', detail=''):
    conn.execute(
        'INSERT INTO audit(ts, actor, action, line_id, detail) '
        'VALUES(?,?,?,?,?)',
        (_now(), actor or '', action, line_id or '',
         json.dumps(detail, ensure_ascii=False) if not isinstance(detail, str)
         else detail))


# ==================================================================
#  行 ↔ 记录
# ==================================================================

def _beat_to_payload(beat: Beat) -> dict:
    """Beat → 可入库的 payload（BEAT_FIELDS 键序，Seq 单独成列）。"""
    return _payload_from_beat(beat)


_FIELD_ATTR = {
    'LineId': 'line_id', 'Seq': 'seq', 'Label': 'label',
    'Condition': 'condition', 'Comment': 'comment', 'Kind': 'kind',
    'Speaker': 'speaker', 'Text': 'text', 'Scene': 'scene',
    # 三列人话演出（DSL）。内部字段 show/hide/at/music/... 是它们解析出来的
    # 派生结果，不入库——库里存原文，谁来读谁负责解释（避免两套真源打架）。
    'Stage': 'stage', 'BGM': 'bgm', 'SFX': 'sfx',
    'CodeBlock': 'code', 'Voice': 'voice', 'Notes': 'notes',
    'ContentHash': 'content_hash',
}


def _field_attr(field: str) -> str:
    return _FIELD_ATTR.get(field, field.lower())


def beat_from_payload(payload: dict) -> Beat:
    payload = dict(payload or {})
    payload.pop('Seq', None)
    return Beat.from_row(payload)


def _payload_from_beat(beat: Beat) -> dict:
    row = {}
    for f in BEAT_FIELDS:
        if f == 'Seq':
            continue
        row[f] = getattr(beat, _field_attr(f), '')
    return row


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

    beats = [beat_from_payload(json.loads(r['payload'])) for r in rows]
    for b in beats:
        b.line_id = b.line_id or ''
    assign_identity(beats)

    out = Path(sheet_path)
    export_beats_to_excel(beats, out, registry=registry)

    # 追加协作信息表
    import openpyxl
    wb = openpyxl.load_workbook(out)
    if COLLAB_SHEET in wb.sheetnames:
        del wb[COLLAB_SHEET]
    ws = wb.create_sheet(COLLAB_SHEET)
    ws.append(COLLAB_FIELDS)
    for r in rows:
        ws.append([r['line_id'], r['revision'], r['locked_by'] or '',
                   r['status'], r['updated_by'], r['updated_at']])
    for col, w in zip('ABCDEF', (16, 10, 14, 12, 14, 20)):
        ws.column_dimensions[col].width = w
    wb.save(out)

    with connect(db_path) as conn:
        _audit(conn, actor, 'pull', '', {'sheet': str(out),
                                        'rows': len(beats)})
    return {'sheet': str(out), 'rows': len(beats)}


def read_collab_sheet(sheet_path) -> dict:
    """读协作信息表 → {line_id: {'revision': int, 'locked_by': str, 'status': str}}"""
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
            out[str(lid)] = {
                'revision': int(data.get('Revision') or 0),
                'locked_by': str(data.get('LockedBy') or ''),
                'status': str(data.get('Status') or STATUS_DRAFT),
            }
        return out
    finally:
        wb.close()


def push(db_path, sheet_path, actor: str, force: bool = False) -> dict:
    """
    Excel（前端）→ 数据库。

    冲突规则（都不静默覆盖）：
      · 行被别人锁着          → 拒（除非 force）
      · 库里的 revision 比你拉取时更新 → 冲突，列出来让人先看 diff
      · 你在 Excel 新建的行    → 直接插入
    返回 {'added','updated','unchanged','conflicts','rejected'}
    """
    beats = import_beats_from_excel(sheet_path)
    assign_identity(beats)
    base = read_collab_sheet(sheet_path)

    result = {'added': [], 'updated': [], 'unchanged': [], 'conflicts': [],
              'rejected': []}

    with connect(db_path) as conn:
        ensure_user(conn, actor)
        existing = {r['line_id']: r for r in
                    conn.execute('SELECT * FROM beats').fetchall()}

        for beat in beats:
            lid = beat.line_id
            row = existing.get(lid)
            payload = _payload_from_beat(beat)
            chash = beat.content_hash

            if row is None:
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

            # 锁：别人锁着就不能改（force 只该在明确沟通过后使用）
            locker = row['locked_by']
            if locker and locker != actor and not force:
                _audit(conn, actor, 'push_rejected_locked', lid,
                       {'locked_by': locker})
                result['rejected'].append(
                    {'line_id': lid, 'reason': f'被 {locker} 锁定'})
                continue

            if row['content_hash'] == chash:
                result['unchanged'].append(lid)
                continue

            base_rev = (base.get(lid) or {}).get('revision', 0)
            if not force and base_rev and row['revision'] > base_rev:
                _audit(conn, actor, 'conflict', lid,
                       {'db_revision': row['revision'],
                        'base_revision': base_rev})
                result['conflicts'].append({
                    'line_id': lid,
                    'label': row['label'],
                    'db_revision': row['revision'],
                    'base_revision': base_rev,
                    'updated_by': row['updated_by'],
                    'hint': '库里的版本比你拉取时新：先 pull 并比对，'
                            '或用 --force 覆盖',
                })
                continue

            conn.execute(
                'UPDATE beats SET payload=?, content_hash=?, label=?, seq=?, '
                'revision=revision+1, updated_by=?, updated_at=? '
                'WHERE line_id=?',
                (json.dumps(payload, ensure_ascii=False), chash, beat.label,
                 beat.seq, actor, _now(), lid))
            _audit(conn, actor, 'update', lid, {'revision': row['revision'] + 1})
            result['updated'].append(lid)

        # 表里没有的行 = 被删掉了 → 标记软删除（保留审计）
        sheet_ids = {b.line_id for b in beats}
        for lid, row in existing.items():
            if row['deleted'] or lid in sheet_ids:
                continue
            if row['locked_by'] and row['locked_by'] != actor and not force:
                continue
            conn.execute('UPDATE beats SET deleted=1, updated_by=?, '
                         'updated_at=?, revision=revision+1 WHERE line_id=?',
                         (actor, _now(), lid))
            _audit(conn, actor, 'delete', lid, {})

    return result


# ==================================================================
#  锁
# ==================================================================

def lock(db_path, line_id: str, actor: str, note: str = '') -> dict:
    with connect(db_path) as conn:
        ensure_user(conn, actor)
        row = conn.execute('SELECT locked_by FROM beats WHERE line_id=?',
                           (line_id,)).fetchone()
        if row is None:
            return {'ok': False, 'reason': f'没有这一行：{line_id}'}
        if row['locked_by'] and row['locked_by'] != actor:
            return {'ok': False, 'reason': f"已被 {row['locked_by']} 锁定"}
        conn.execute('UPDATE beats SET locked_by=?, locked_at=? '
                     'WHERE line_id=?', (actor, _now(), line_id))
        _audit(conn, actor, 'lock', line_id, note)
    return {'ok': True, 'line_id': line_id, 'owner': actor}


def unlock(db_path, line_id: str, actor: str, force: bool = False) -> dict:
    with connect(db_path) as conn:
        row = conn.execute('SELECT locked_by FROM beats WHERE line_id=?',
                           (line_id,)).fetchone()
        if row is None:
            return {'ok': False, 'reason': f'没有这一行：{line_id}'}
        if row['locked_by'] and row['locked_by'] != actor and not force:
            return {'ok': False, 'reason': f"锁属于 {row['locked_by']}"}
        conn.execute('UPDATE beats SET locked_by=NULL, locked_at=NULL '
                     'WHERE line_id=?', (line_id,))
        _audit(conn, actor, 'unlock', line_id, {'force': force})
    return {'ok': True, 'line_id': line_id}


def lock_range(db_path, label: str, actor: str) -> dict:
    """整章上锁：一个文案认领一整章时最省事的方式。"""
    with connect(db_path) as conn:
        ensure_user(conn, actor)
        ids = [r['line_id'] for r in conn.execute(
            'SELECT line_id FROM beats WHERE label=? AND deleted=0', (label,))]
        locked, skipped = [], []
        for lid in ids:
            row = conn.execute('SELECT locked_by FROM beats WHERE line_id=?',
                               (lid,)).fetchone()
            if row['locked_by'] and row['locked_by'] != actor:
                skipped.append(lid)
                continue
            conn.execute('UPDATE beats SET locked_by=?, locked_at=? '
                         'WHERE line_id=?', (actor, _now(), lid))
            locked.append(lid)
        _audit(conn, actor, 'lock_range', label,
               {'locked': len(locked), 'skipped': len(skipped)})
    return {'label': label, 'locked': locked, 'skipped': skipped}


def locks(db_path) -> list:
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            'SELECT b.line_id, b.label, b.locked_by, b.locked_at, b.status '
            'FROM beats b WHERE b.locked_by IS NOT NULL AND b.deleted=0 '
            'ORDER BY b.label, b.seq').fetchall()]


# ==================================================================
#  状态机 / 查询
# ==================================================================

def set_status(db_path, line_id: str, status: str, actor: str) -> dict:
    if status not in STATUSES:
        return {'ok': False, 'reason': f'状态只能是 {"/".join(STATUSES)}'}
    with connect(db_path) as conn:
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
    with connect(db_path) as conn:
        ensure_user(conn, actor)
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
    """协作总览：谁锁了多少、各状态多少、最近改了什么。"""
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
            'SELECT id, display_name FROM users ORDER BY id').fetchall()]
    return {
        'lines': total,
        'by_status': by_status,
        'locked_by': by_owner,
        'locked_total': sum(by_owner.values()),
        'labels': labels,
        'users': users,
        'recent': recent,
    }


def export_ir_from_db(db_path):
    """从数据库直接造 IR（不经 Excel），给 CI/构建用。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            'SELECT payload FROM beats WHERE deleted=0 ORDER BY label, seq'
        ).fetchall()
        assets = conn.execute('SELECT * FROM assets').fetchall()
    beats = [beat_from_payload(json.loads(r['payload'])) for r in rows]
    assign_identity(beats)
    return beats, [dict(a) for a in assets]
