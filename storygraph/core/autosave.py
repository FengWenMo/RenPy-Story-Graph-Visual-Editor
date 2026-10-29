"""
自动保存 / 崩溃恢复（**存储层**）
==========================================================================

用户 2026-10-22（体验报告 P0-2，原话）：

> 拍的改动没有自动保存 —— 关窗会问，崩了就三拍没了。
> 50 人 × 每天几小时，这件事一定会发生。

## 这件事的边界（先写清楚，免得越做越大）

这一层**只做一件事**：把"内存里的这一章"整份抄到用户目录，崩了下次能拿回来。

  · **不代替导出**：它不写用户的 `.rpy`（那要过安全门禁，见 `core/safeguard`
    与 `story.generation_guard`），也**不改变**"改拍 → 导出才落盘"这条既有语义。
    恢复之后状态栏照样是「● 未导出」，用户看过、按「⤓ 导出 .rpy」才写文件。
  · **不写进项目仓库**：`%LOCALAPPDATA%/StoryGraph/autosave/`，与
    `session_store.py`（记忆）、`ai_settings.py`、`crashlog.py` 同一处约定。
    理由是同一个：项目里那份要进 git、要评审，"我这一分钟改到哪了"不该污染 diff。
  · **不自动恢复**：只写盘 + 下次打开时问一句。替用户决定"这就是你要的"
    正是丢东西最典型的方式 —— 她可能已经在编辑器里改过那段了。

## 存什么形状

```json
{
  "version": 1,
  "root": "D:/千连万理/RenpyTest/sr_general",
  "chapter": "D:/…/game/chapter/chapter1_4.rpy",
  "chapter_rel": "game/chapter/chapter1_4.rpy",
  "saved_at": "2026-10-22T21:03:11",
  "source": {"hash": "9f2c…", "size": 31181, "mtime": 1761133355.2},
  "dirty": {"changed": 3, "added": 1, "removed": 0},
  "total": 284,
  "beats": [ {"seq": 0, "label": "chapter1_4", "text": "…"}, … ]
}
```

`beats` 是**整章**（不是 diff）：只有整章才能"恢复成一个完整可导出的状态"，
而 diff 一旦有一条对不上，剩下全要猜。实测 284 拍 ≈ 270 KB、写一遍 3 ms
（见 `tools/_probe_autosave.py`），所以"整份抄"在体积与耗时上都付得起。

`source.hash` 是**写快照那一刻**源文件的指纹：恢复时要拿它和**现在**的
文件比 —— 一样，拍的 `src_first/src_last` 仍然有效（增量补丁能对上）；
不一样，说明文件在崩溃之后被谁动过，那就得让用户知道（见 `stale_reason`）。

## 三条硬规矩

1. **写不进去不拦人**：盘满 / 只读 / 没权限 —— 返回 `ok=False` + 原因，
   一律只记台账（`safeguard.swallow`），绝不在编辑中途弹框；
2. **原子替换**（`*.tmp` + `os.replace`）：断电不会留下半截 JSON
   （半截 JSON 比没有快照更坏：它会让人以为"恢复过了"）；
3. **读不懂就当没有**：版本更新 / 字段缺 / 坏文件 → `ok=False` + 原因，
   绝不猜、也**绝不删**（用户可能想手工看一眼那份文本）。

## 一键关掉

`STORYGRAPH_NO_AUTOSAVE=1`（自动化 / 打包自检 / "我今天不想让它写盘"），
以及 `set_enabled(False)`（测试用；见 `tests/conftest.py` 的 autouse 夹具）。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.core.user_path import app_data_dir

__all__ = ['VERSION', 'KEEP', 'root_dir', 'project_dir', 'snapshot_path',
           'enabled', 'set_enabled', 'write', 'read', 'candidates',
           'discard', 'discard_project', 'prune', 'file_stamp', 'stale_reason',
           'entry_line']

#: 结构版本。**只增不改**：加字段不必进版本，改语义必须进（读到更大的就拒）。
VERSION = 1

#: 每个项目最多留几份快照（按 `saved_at` 留最新的）。
#:
#: 为什么是 8 而不是"每个章节一份、永不删"：一个人 seldom 同时改 8 章以上，
#: 而每份可能 1–2 MB —— 不封顶就变成"工具在我盘上越攒越多"，
#: 那种事用户最后会用"删掉整个目录"来回应。
KEEP = 8

#: 一键关掉（任何非空值都算关）
_ENV_OFF = 'STORYGRAPH_NO_AUTOSAVE'
#: 换目录（测试 / 打包 / 多用户共用一台机器）
_ENV_DIR = 'STORYGRAPH_AUTOSAVE_DIR'

_ENABLED = True


# ===========================================================================
#  开关与路径
# ===========================================================================

def enabled() -> bool:
    """现在允许读写自动保存吗？（`STORYGRAPH_NO_AUTOSAVE` 或 `set_enabled`）"""
    return bool(_ENABLED) and not bool(os.environ.get(_ENV_OFF, '').strip())


def set_enabled(on: bool):
    """
    全局开 / 关（测试用）。

    ⚠ 与 `session_store.set_enabled` 同一个理由：跑一次全量测试会造几百个
    临时项目当窗口，不关的话真实目录里会攒一堆 `tmp_path` 的快照 ——
    下次你开工具时它会问"要不要恢复 3 拍"，而那 3 拍来自某条用例。
    """
    global _ENABLED
    _ENABLED = bool(on)


def root_dir() -> Path:
    """
    自动保存的根目录（`STORYGRAPH_AUTOSAVE_DIR` 可覆盖）。

    位置规则只有一份：`core/user_path.app_data_dir`，与 `session.json`、
    `ai.json`、`crash.log` 同一处 —— 用户已经知道"这工具的东西存在
    `%LOCALAPPDATA%/StoryGraph/`"，再开一个新地方会让人找不到该删什么。
    """
    env = os.environ.get(_ENV_DIR, '').strip()
    if env:
        return Path(env)
    return app_data_dir('autosave')


def _norm(path) -> str:
    """规范化成"同一个项目只算一次"的键（绝对路径 + 正斜杠，不解符号链接）。"""
    try:
        return os.path.abspath(str(path or '')).replace('\\', '/')
    except Exception as e:                       # noqa: BLE001 - 算不出就用原串
        swallow('core.autosave.norm', e)
        return str(path or '')


def _key(text: str) -> str:
    """一段路径 → 目录 / 文件名用的短键（12 位足够，还能互相区分）。"""
    return hashlib.md5(str(text or '').encode('utf-8', 'replace')).hexdigest()[:12]


def project_dir(root) -> Path:
    """某个项目的快照目录（**不保证存在**）。"""
    return root_dir() / _key(_norm(root))


def snapshot_path(root, chapter) -> Path:
    """
    某一章的快照文件。

    键用**章节绝对路径**：同一章在同一个项目里只该有一份（新快照覆盖旧的）。
    不用 `chapter_rel` 当键：项目搬过家之后相对路径可能变，
    而"快照属于那个文件"这件事由文件自己（`chapter` 字段）说。
    """
    return project_dir(root) / f'{_key(_norm(chapter))}.json'


# ===========================================================================
#  文件指纹
# ===========================================================================
def file_stamp(path) -> dict:
    """
    源文件指纹：`{hash, size, mtime, exists}`（读不出来 → `exists=False`）。

    `hash` 用 sha1 全文 —— 31 KB 的章节 0.2 ms，20 万字的整章也只有几十毫秒；
    但要**只在写快照 / 打开项目时**调（不在滚动、翻页那条路上调）。
    """
    out = {'hash': '', 'size': 0, 'mtime': 0.0, 'exists': False}
    try:
        fp = Path(str(path))
        if not fp.is_file():
            return out
        data = fp.read_bytes()
        out['hash'] = hashlib.sha1(data).hexdigest()
        out['size'] = len(data)
        try:
            out['mtime'] = float(fp.stat().st_mtime)
        except Exception as e:                   # noqa: BLE001 - 时间戳取不到不影响比对
            swallow('core.autosave.file_stamp.mtime', e)
        out['exists'] = True
    except Exception as e:                       # noqa: BLE001 - 读不了就当没有
        swallow('core.autosave.file_stamp', e)
    return out


# ===========================================================================
#  写
# ===========================================================================
def write(root, chapter, beats, *, meta=None, extra=None) -> dict:
    """
    把一份快照写到用户目录（**不抛**）。

    返回 `{'ok', 'path', 'reason'}`。`beats` 是**已经序列化好的字典列表**
    （拍 → dict 的转换在 `app.session_recover`，这一层不 import `story`）。

    `extra`：调用方要随快照一起存、但这一层不认识的字段（现在只有一个：
    `file_blocks` —— 章节级保真块，见 `app.session_recover`）。
    写在这里而不是"写完再补一刀"，是为了**一次原子替换**就落全
    （补写要读-改-写一遍，中途断电会留下"拍在、块没了"的半份快照）。
    """
    path = snapshot_path(root, chapter)
    if not enabled():
        return {'ok': False, 'path': str(path), 'reason': '自动保存已关'}
    stamp = file_stamp(chapter)
    payload = {
        'version': VERSION,
        'root': str(root or ''),
        'chapter': str(chapter or ''),
        'chapter_rel': str((meta or {}).get('chapter_rel') or ''),
        'saved_at': datetime.now().isoformat(timespec='seconds'),
        'source': {k: stamp[k] for k in ('hash', 'size', 'mtime')},
        'dirty': dict((meta or {}).get('dirty') or {}),
        'total': int((meta or {}).get('total') or len(beats or ())),
        'beats': list(beats or ()),
    }
    for key, value in (extra or {}).items():
        key = str(key or '')
        # 固定字段**不许**被覆盖：`version` / `beats` 被改掉就是一份读不懂的快照，
        # 而"存进去时还好好的、读出来才发现不对"是最难查的一类坏文件。
        if key and key not in payload:
            payload[key] = value
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, path)                    # 原子替换：不会留下半截 JSON
        prune(root)
        return {'ok': True, 'path': str(path), 'reason': ''}
    except Exception as e:                       # noqa: BLE001 - 写不进去不该拦人
        swallow('core.autosave.write', e)
        return {'ok': False, 'path': str(path),
                'reason': f'{type(e).__name__}: {e}'}
    finally:
        if tmp is not None and tmp.exists():
            # `os.replace` 成功后 tmp 已经不在了；失败时留着的那个半截文件
            # 会被这里清掉 —— 残留的 `*.tmp` 只会在下次列目录时变成噪声。
            try:
                tmp.unlink()
            except Exception as e:               # noqa: BLE001
                swallow('core.autosave.write.cleanup', e)


# ===========================================================================
#  读
# ===========================================================================
def read(path) -> dict:
    """
    读一份快照：`{'ok', 'error', 'data'}`（**不抛**）。

    判据（任一不满足就拒，且**不删文件**）：
      · 是 JSON 对象；
      · `version` 不超过本模块的 `VERSION`（更新的工具写的 → 读不懂就别猜）；
      · `beats` 是列表。
    """
    fp = Path(str(path))
    if not fp.is_file():
        return {'ok': False, 'error': '快照文件不在了', 'data': {}}
    try:
        data = json.loads(fp.read_text(encoding='utf-8'))
    except Exception as e:                       # noqa: BLE001 - 手写 / 半截文件容错
        swallow('core.autosave.read', e)
        return {'ok': False, 'error': f'{type(e).__name__}: {e}', 'data': {}}
    if not isinstance(data, dict):
        return {'ok': False, 'error': '快照不是一个对象', 'data': {}}
    try:
        version = int(data.get('version') or 0)
    except (TypeError, ValueError):
        version = 0
    if version <= 0:
        return {'ok': False, 'error': '快照没有版本号', 'data': {}}
    if version > VERSION:
        return {'ok': False,
                'error': f'快照来自更新版本的工具（v{version} > v{VERSION}），'
                         f'这一版读不懂它', 'data': {}}
    if not isinstance(data.get('beats'), list):
        return {'ok': False, 'error': '快照里没有拍列表', 'data': {}}
    return {'ok': True, 'error': '', 'data': data}


def _entry_of(path: Path, data: dict) -> dict:
    """快照 → 供界面显示的一条（**不带 beats**，省内存）。"""
    stamp = data.get('source') if isinstance(data.get('source'), dict) else {}
    beats = data.get('beats') or ()
    return {
        'path': str(path),
        'root': str(data.get('root') or ''),
        'chapter': str(data.get('chapter') or ''),
        'chapter_rel': str(data.get('chapter_rel') or ''),
        'saved_at': str(data.get('saved_at') or ''),
        'source_hash': str(stamp.get('hash') or ''),
        'total': int(data.get('total') or len(beats)),
        'dirty': dict(data.get('dirty') or {}),
    }


def candidates(root, chapter=None) -> list:
    """
    这个项目（或其中一章）有哪些快照，**最新的在前**。

    `chapter` 给了就只留那一章。读不出来的文件**跳过但不删**
    （它们可能是上一个版本写的；用户要清理有 `discard_project`）。
    """
    out = []
    folder = project_dir(root)
    try:
        if not folder.is_dir():
            return out
        files = list(folder.glob('*.json'))
    except Exception as e:                       # noqa: BLE001 - 列不出来就当没有
        swallow('core.autosave.candidates', e)
        return out
    want = _norm(chapter) if chapter else ''
    for fp in files:
        got = read(fp)
        if not got.get('ok'):
            swallow('core.autosave.candidates.bad',
                    RuntimeError(f'{fp.name}: {got.get("error")}'))
            continue
        entry = _entry_of(fp, got['data'])
        if want and _norm(entry['chapter']) != want:
            continue
        out.append(entry)
    out.sort(key=lambda e: e.get('saved_at') or '', reverse=True)
    return out


def stale_reason(entry: dict) -> str:
    """
    这份快照还能不能"原样恢复"，返回**人话理由**（空串 = 文件没变过，可以）。

    三种情况都要说清楚，因为用户下一步做的事完全不同：
      · 文件不见了 → 恢复出来只能另存（原文件被人删了 / 移走了）；
      · 内容变了   → 恢复会**盖掉**崩溃之后别人（或她自己）在那份文件里的改动；
      · 读不出来   → 连比都没法比，按"变了"处理。
    """
    entry = entry or {}
    chapter = str(entry.get('chapter') or '')
    if not chapter:
        return '快照里没记是哪一章'
    now = file_stamp(chapter)
    if not now.get('exists'):
        return f'{Path(chapter).name} 现在不在磁盘上了'
    if not entry.get('source_hash'):
        return '快照里没记源文件指纹（旧版本写的），没法确认文件变没变'
    if now['hash'] != entry['source_hash']:
        when = str(entry.get('saved_at') or '')
        return (f'{Path(chapter).name} 在 {when} 之后**被改过**'
                f'（也许是你在编辑器里改的）')
    return ''


def entry_line(entry: dict) -> str:
    """
    一条快照 → 界面上一行字（"哪一章 · 几拍 · 什么时候"）。

    放在 core 而不是 ui：CLI / 测试 / 诊断报告都要用同一句话。
    """
    entry = entry or {}
    name = (Path(str(entry.get('chapter') or '')).name
            or str(entry.get('chapter_rel') or '') or '（未知章节）')
    dirty = entry.get('dirty') or {}
    parts = []
    for key, word in (('changed', '拍内容改了'), ('added', '拍是新加的'),
                      ('removed', '拍删掉了')):
        n = int(dirty.get(key) or 0)
        if n:
            parts.append(f'{n} {word}')
    what = '、'.join(parts) or f'整章 {int(entry.get("total") or 0)} 拍'
    when = str(entry.get('saved_at') or '')[:16].replace('T', ' ')
    return f'{name}：{what}（{when} 自动保存）'


# ===========================================================================
#  清理
# ===========================================================================
def discard(root, chapter) -> dict:
    """删掉某一章的补偿快照（导出成功 / 用户明确不要了）。"""
    path = snapshot_path(root, chapter)
    try:
        if path.is_file():
            path.unlink()
            return {'ok': True, 'path': str(path), 'removed': True}
    except Exception as e:                       # noqa: BLE001 - 删不掉也不拦人
        swallow('core.autosave.discard', e)
        return {'ok': False, 'path': str(path), 'removed': False,
                'reason': f'{type(e).__name__}: {e}'}
    return {'ok': True, 'path': str(path), 'removed': False}


def discard_project(root) -> dict:
    """删掉这个项目的**所有**快照（界面上"不用恢复 / 清理"那个按钮）。"""
    folder = project_dir(root)
    removed = 0
    try:
        if folder.is_dir():
            for fp in folder.glob('*.json'):
                try:
                    fp.unlink()
                    removed += 1
                except Exception as e:           # noqa: BLE001 - 单个删不掉就跳过
                    swallow('core.autosave.discard_project.one', e)
            try:
                folder.rmdir()                   # 空了就顺手收掉目录
            except Exception as e:               # noqa: BLE001 - 非空 / 正被占用
                swallow('core.autosave.discard_project.dir', e)
    except Exception as e:                       # noqa: BLE001
        swallow('core.autosave.discard_project', e)
        return {'ok': False, 'removed': removed,
                'reason': f'{type(e).__name__}: {e}'}
    return {'ok': True, 'removed': removed, 'reason': ''}


def prune(root) -> int:
    """
    只留最新的 `KEEP` 份（写完之后顺手做，返回删了几份）。

    为什么在 `write` 里做而不是定时做：写的时候**正好**知道这个项目有几份，
    也不会出现"清理任务自己崩了、目录越攒越大"这种没人看得见的失败。
    """
    folder = project_dir(root)
    removed = 0
    try:
        files = [fp for fp in folder.glob('*.json')]
    except Exception as e:                       # noqa: BLE001
        swallow('core.autosave.prune', e)
        return 0
    if len(files) <= KEEP:
        return 0
    stamped = []
    for fp in files:
        try:
            stamped.append((fp.stat().st_mtime, fp))
        except Exception as e:                   # noqa: BLE001 - 取不到时间就当最旧
            swallow('core.autosave.prune.stat', e)
            stamped.append((0.0, fp))
    stamped.sort(key=lambda pair: pair[0], reverse=True)
    for _mtime, fp in stamped[KEEP:]:
        try:
            fp.unlink()
            removed += 1
        except Exception as e:                   # noqa: BLE001
            swallow('core.autosave.prune.unlink', e)
    return removed
