"""
`EditorSession` 的**崩溃恢复**那一半：把内存里的这一章抄成快照、再抄回来。
==========================================================================

用户 2026-10-22（体验报告 P0-2）：

> 拍的改动没有自动保存 —— 关窗会问，崩了就三拍没了。
> 50 人 × 每天几小时，这件事一定会发生。

## 两件事，分开放

  · **存哪儿 / 什么形状 / 原子写 / 读不懂就拒** → `core/autosave.py`
    （纯文件，不 import `story.*`，好测）；
  · **拍 ↔ JSON** 与"把快照摊回会话" → 这里（要认识 `Beat` 与章节文档）。

## "整章"而不是"差量"

快照里放**整章所有拍**。差量（只存改过的那几拍）看起来省，但它要求
"改过的拍能被无歧义地定位"，而定位靠的 `seq` / `line_id` 恰恰是插卡删卡时
最先变的东西 —— 一条对不上，剩下全要猜。整章则永远能还原成一个
**能直接导出**的状态。体积实测：284 拍 ≈ 270 KB / 3 ms
（`tools/_probe_autosave.py`），付得起。

## 恢复之后是"未导出"，不是"已保存"

这是这条功能最容易做错的一处：把快照摊回会话之后，**绝不能**
`take_edits_baseline()` —— 那会把"内存比磁盘新"这件事抹掉，用户看一眼
以为已经写回文件了，随手一关就真丢了。所以：

```
恢复 → beats 被替换 → mark_edits_dirty() → 状态栏「● 未导出」
     → 用户看过 → 点「⤓ 导出 .rpy」→ 那一刻才落盘
```

`_saved_prints`（载入时按**磁盘**记的基线）也不动 —— 于是
`unsaved_edits()` 报出来的正是"快照比磁盘多了/改了什么"，一句谎都没有。

## 源文件变过怎么办

快照记了写它那一刻源文件的指纹（`source.hash`）。恢复时拿**现在**的文件比：

  · **一样** → 拍的 `src_first/src_last` 仍然有效，导出走增量补丁，
    未改的行逐字节保留；
  · **不一样**（崩溃之后又被谁动过）→ 把行号**清零**，交给
    `story.patcher` 的 `_fill_missing_src` 按内容重新对位。
    这比"拿旧行号硬写"安全得多：硬写的下一个镜头就是**写到别人的行上**。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from storygraph.core import autosave as AS
from storygraph.core.safeguard import swallow
from storygraph.story.beat_model import Beat

__all__ = ['RecoverMixin', 'beat_to_dict', 'beats_from_snapshot']

#: `Beat` 的全部字段名（**不手抄清单**：加字段时少一处要记得改）。
_BEAT_FIELDS = tuple(f.name for f in dataclasses.fields(Beat))

#: 列表型字段里**元素是二元组**的那些（JSON 会把它变成 `[a, b]`，回来时要还原）。
_PAIR_LIST_FIELDS = ('code_blocks',)

#: 整数字段（JSON 里可能是浮点 / 字符串；`seq` 拿去切片会当场炸）。
_INT_FIELDS = ('seq', 'src_first', 'src_last')


def beat_to_dict(beat) -> dict:
    """一拍 → 纯 JSON 值（**全部字段**，含 `line_id` / `src_first`）。"""
    try:
        return dataclasses.asdict(beat)
    except Exception as e:                      # noqa: BLE001 - 极端对象也要能存
        swallow('app.session_recover.beat_to_dict', e)
        return {name: getattr(beat, name, '') for name in _BEAT_FIELDS}


def _coerce(data: dict) -> dict:
    """JSON 里的 dict → `Beat(**…)` 能吃的 kwargs（去未知键、补类型）。"""
    out = {}
    for name, value in (data or {}).items():
        if name not in _BEAT_FIELDS:
            continue                            # 别的版本多出来的字段：丢掉（不猜）
        if name in _INT_FIELDS:
            try:
                out[name] = int(value or 0)
            except (TypeError, ValueError) as e:
                swallow('app.session_recover.coerce.int', e)
                out[name] = 0
        elif name in _PAIR_LIST_FIELDS and isinstance(value, list):
            pairs = []
            for item in value:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    try:
                        pairs.append((int(item[0]), int(item[1])))
                    except (TypeError, ValueError) as e:
                        swallow('app.session_recover.coerce.pair', e)
            out[name] = pairs
        elif name in ('ordered', 'sanitized_cells', 'runtime_notes'):
            out[name] = list(value or []) if isinstance(value, (list, tuple)) else []
        elif name == 'nest_path':
            out[name] = None if value is None else str(value)
        else:
            out[name] = value
    return out


def beats_from_snapshot(rows, *, drop_src: bool = False) -> list:
    """
    快照里的 `beats` → `Beat` 列表。

    `drop_src=True`：**清掉源文件行号**（源文件在快照之后变过时必须这么做，
    见模块 docstring"源文件变过怎么办"）。
    """
    out = []
    for row in rows or ():
        kwargs = _coerce(row if isinstance(row, dict) else {})
        if drop_src:
            kwargs['src_first'] = 0
            kwargs['src_last'] = 0
            # 这两列只服务增量对位；留着旧行号 = 让补丁写到别人的行上。
            kwargs.pop('beat_fingerprint', None)
        try:
            out.append(Beat(**kwargs))
        except Exception as e:                  # noqa: BLE001 - 一拍坏了不该整章丢
            swallow('app.session_recover.beats_from_snapshot', e)
    return out


class RecoverMixin:
    """
    崩溃恢复（见模块 docstring）。

    它只要求宿主提供**已经存在**的会话成员：拍列表 / 源路径 / 项目根 /
    章节文档，以及"脏了"的标记与基线重算。
    """

    #: 本 mixin 要求宿主（或其它 mixin）提供的成员（由拆分类的守卫测试守）。
    REQUIRES = ('beats', 'chapter', 'script_path', 'project_root',
                'forget_undo', 'mark_edits_dirty', 'unsaved_edits')

    # ------------------------------------------------------------------
    #  写：内存 → 快照
    # ------------------------------------------------------------------

    def autosave_chapter_rel(self) -> str:
        """这一章在项目里的相对路径（不在项目里 / 没项目 → 空串）。"""
        chapter = getattr(self, 'script_path', None)
        root = getattr(self, 'project_root', None)
        if not chapter or not root:
            return ''
        try:
            return str(Path(chapter).resolve()
                       .relative_to(Path(root).resolve())).replace('\\', '/')
        except Exception as e:                  # noqa: BLE001 - 不在项目里就算了
            swallow('app.session_recover.chapter_rel', e)
            return ''

    def autosave_capture(self) -> dict:
        """
        现在就把这一章抄一份到用户目录（`{'ok','path','reason','dirty'}`）。

        **没载入任何章节 / 没有项目根** → 不写（快照必须绑定一个项目，
        否则下次打开时不知道它属于谁）。写不进去只记台账，绝不拦用户。
        """
        chapter = getattr(self, 'script_path', None)
        root = getattr(self, 'project_root', None)
        beats = list(getattr(self, 'beats', None) or ())
        if not chapter or not root or not beats:
            return {'ok': False, 'path': '', 'reason': '还没打开项目或章节'}
        dirty = self._dirty_counts()
        try:
            rows = [beat_to_dict(b) for b in beats]
        except Exception as e:                  # noqa: BLE001
            return {'ok': False, 'path': '',
                    'reason': f'拍序列化失败：{type(e).__name__}: {e}'}
        res = AS.write(root, chapter, rows,
                       meta={'chapter_rel': self.autosave_chapter_rel(),
                             'dirty': dirty, 'total': len(beats)},
                       extra={'file_blocks': self.chapter_file_blocks()})
        res['dirty'] = dirty
        return res

    def _dirty_counts(self) -> dict:
        """`unsaved_edits()` 里那三个数字（拿不到就是空的）。"""
        try:
            info = self.unsaved_edits() or {}
        except Exception as e:                  # noqa: BLE001
            swallow('app.session_recover.dirty_counts', e)
            return {}
        return {k: int(info.get(k) or 0)
                for k in ('changed', 'added', 'removed')}

    # ------------------------------------------------------------------
    #  读：列出这个项目（这一章）有哪些快照
    # ------------------------------------------------------------------

    def autosave_entries(self, chapter=None) -> list:
        """这个项目的快照（`chapter` 给了就只留那一章），最新的在前。"""
        root = getattr(self, 'project_root', None)
        if not root:
            return []
        return AS.candidates(root, chapter if chapter is not None
                             else getattr(self, 'script_path', None))

    def autosave_stale_reason(self, entry) -> str:
        """这份快照还能不能原样恢复（空串 = 源文件没变过）。"""
        return AS.stale_reason(entry)

    # ------------------------------------------------------------------
    #  恢复：快照 → 内存
    # ------------------------------------------------------------------

    def autosave_restore(self, entry) -> dict:
        """
        把一份快照摊回会话（返回 `{'ok','error','beats','realigned','diff'}`）。

        ⚠ **调用方必须先按磁盘载入过这一章**（`load()`）：恢复是"在磁盘那一版
        之上换掉拍列表"，不是"打开快照"。这样：

          · `_saved_prints` 是**磁盘**的基线 → `unsaved_edits()` 报的是真话；
          · 源文件行号在"文件没变过"时仍然有效（增量补丁对得上）。

        失败（读不出来 / 版本不认识 / 拍为空）→ `ok=False` + 人话原因，
        **不动**内存里的东西。
        """
        entry = dict(entry or {})
        got = AS.read(entry.get('path'))
        if not got.get('ok'):
            return {'ok': False, 'error': got.get('error') or '读不出来',
                    'beats': 0, 'realigned': False, 'diff': {}}
        data = got['data']
        stale = bool(self.autosave_stale_reason(entry))
        beats = beats_from_snapshot(data.get('beats'), drop_src=stale)
        if not beats:
            return {'ok': False, 'error': '快照里没有可用的拍',
                    'beats': 0, 'realigned': False, 'diff': {}}
        first = self._first_change_index(beats)
        try:
            self.beats = beats
            self._adopt_recovered_beats(beats, data)
        except Exception as e:                  # noqa: BLE001
            swallow('app.session_recover.restore', e)
            return {'ok': False, 'error': f'{type(e).__name__}: {e}',
                    'beats': 0, 'realigned': False, 'diff': {}}
        # 恢复出来的东西**不是"已保存"**：基线仍停在磁盘那一版（见 docstring）。
        try:
            self.forget_undo()                  # 撤销栈说的是旧的拍，留着会撤错
            self.mark_edits_dirty()
        except Exception as e:                  # noqa: BLE001 - 标记失败不拦恢复
            swallow('app.session_recover.restore.mark', e)
        return {'ok': True, 'error': '', 'beats': len(beats),
                'realigned': stale, 'diff': self._dirty_counts(),
                'first_change': first}

    def _first_change_index(self, beats) -> int:
        """
        快照与**磁盘那一版**第一处不一样在**新列表**的哪一拍（-1 = 算不出来）。

        为什么值得算（要全章渲染两遍，1800 拍约 160 ms）：恢复完把用户直接
        停在改动开始的那一拍，她才能一眼确认"回来的就是这些"。停在第一拍
        等于让她自己往下找 —— 一章 1800 拍，找到第 700 拍之前她不会相信
        恢复成功了。
        """
        from storygraph.app.session_table import beat_edits_print
        fallback = getattr(self, 'rendered_lines_of', None)
        try:
            cur = [beat_edits_print(b, fallback) for b in (self.beats or ())]
            new = [beat_edits_print(b, fallback) for b in (beats or ())]
        except Exception as e:                  # noqa: BLE001 - 算不出来就当没有
            swallow('app.session_recover.first_change', e)
            return -1
        import difflib
        matcher = difflib.SequenceMatcher(None, cur, new, autojunk=False)
        for tag, _i1, _i2, j1, _j2 in matcher.get_opcodes():
            if tag == 'equal':
                continue
            return j1 if j1 < len(new) else max(0, len(new) - 1)
        return -1

    def _adopt_recovered_beats(self, beats, data: dict):
        """
        让**章节文档**接管新拍列表（结构树 + 顶层保真块一起对齐）。

        `chapter` 可能不是 `ChapterDocument`（单测直接塞 `beats`）——
        那就只换列表，别为了一个可选步骤把恢复整条弄失败。
        """
        doc = getattr(self, 'chapter', None)
        adopt = getattr(doc, 'adopt', None)
        if callable(adopt):
            adopt(beats)
            blocks = str((data or {}).get('file_blocks') or '')
            if blocks and getattr(doc, 'file_blocks', '') != blocks:
                # 顶层块是章节级真源：快照里带了就以快照为准（导出会写它）。
                doc.file_blocks = blocks
                sync = getattr(doc, 'sync', None)
                if callable(sync):
                    sync()
            self.beats = doc.beats
        else:
            self.beats = list(beats)

    def chapter_file_blocks(self) -> str:
        """当前章节的顶层保真块（拿不到 → 空串：快照里就不带这一项）。"""
        doc = getattr(self, 'chapter', None)
        try:
            text = getattr(doc, 'file_blocks', '')
            return str(text or '')
        except Exception as e:                  # noqa: BLE001
            swallow('app.session_recover.chapter_file_blocks', e)
            return ''

    # ------------------------------------------------------------------
    #  清：没有可恢复的东西了
    # ------------------------------------------------------------------

    def autosave_discard(self, chapter=None) -> dict:
        """删掉某一章的快照（默认当前章）。"""
        root = getattr(self, 'project_root', None)
        target = chapter if chapter is not None else getattr(self,
                                                            'script_path', None)
        if not root or not target:
            return {'ok': False, 'removed': False, 'reason': '没有项目或章节'}
        return AS.discard(root, target)
