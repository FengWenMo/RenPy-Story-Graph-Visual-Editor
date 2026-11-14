"""
storygraph.app.table_structure
==============================
表格**结构编辑**的撤销栈：插卡 / 删卡 / 清空 / 移动，以及各自的逆操作。

为什么单独一层（2026-11-01，服务化第四刀）
------------------------------------------
前三刀（`AssetCatalog`、`table_edits`、`export_plan`）的判据都是
"**只拿输入**能不能回答"。这一组不一样：它要**改列表 + 推撤销栈**，
判据天然落在会话身上。所以这一层换了切法 —— 按**归属**切，而不是
按"纯不纯"切：

  · 栈**归这里**：`StructureUndoStack` 是有界的，谁往里推、谁会溢出，
    从前是 `_struct_undo` 这个"用 `getattr` 懒创建"的属性 ——
    全仓库七处 `getattr(self, '_struct_undo', …)` 各写一遍默认值，
    写错一个字母永远不报错，只是悄悄少了一次撤销。
  · **逆操作**也归这里（`undo`）：四条判据（插/删/清/移）原本是
    `undo_structural` 里一个 60 行的 if/elif。它要建一个真会话、加载
    一个真项目才能验一条，红了也不知道红在"逆操作算错"还是"会话状态"。
  · **记什么**（`InsertOp` 等四个记录）归这里：`('delete', idx, beats)`
    这种裸元组没有字段名，`op[3]` 是拍还是旧路径全看分支。

留在 `SessionTableMixin` 的是"会话自己才知道的事"：哪一份文档
（`_chapter_doc()`）、顶层块下发（`_sync_chapter`）、重排拍号
（`_renumber`）、内容撤销作废（`forget_undo`）、脏标记
（`refresh_edits_flag`）。`undo()` 通过 `host`（就是会话）调它们，
所以这一层能用**假 host** 单测 —— 见 `tests/test_table_structure.py`。

⚠ 一条别改坏的语义：`undo_structural` 是**先弹栈再尝试**。逆操作失败
（比如那一拍已经被别的操作弄没了）时，这条记录**不还回去** —— 与旧版
逐行一致，`test_struct_undo` 的"栈会退"用例压在这上面。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from storygraph.story.chapter_tree import ChapterTreeError

#: 结构撤销栈最多留多少步（交互退路，不是版本控制）
STRUCT_UNDO_LIMIT = 50


@dataclass(frozen=True)
class InsertOp:
    """插了一张卡；逆操作 = 把它删掉。"""
    at: int


@dataclass(frozen=True)
class DeleteOp:
    """删了一组拍；逆操作 = 原位插回（`beats` 是删掉的那几拍，可能不止一张）。"""
    at: int
    beats: list = field(default_factory=list)


@dataclass(frozen=True)
class ClearOp:
    """清空了一拍的某些通道；逆操作 = 把清掉的字段原样放回去。"""
    at: int
    saved: dict = field(default_factory=dict)
    mode: str = 'text'


@dataclass(frozen=True)
class MoveOp:
    """移动了一拍；逆操作 = 移回去 + 还原结构位与条件。"""
    src: int
    dst: int
    beat: Any = None
    path: Any = None
    condition: str = ''


@dataclass(frozen=True)
class SplitOp:
    """把一句台词拆成两口（`{nw}` + 接续卡）；逆操作 = 拔掉新卡 + 还原原文。

    `at` 是**新插进来那张**的拍号（原卡 +1）。只记 `old_text` 一份就够：
    拆的时候除了"原文末尾加了 `{nw}`"没有别的改动（说话人 / 标签 / 舞台
    都原样留着），所以撤的时候把原文写回去、再把新卡删掉，就回到了没拆之前
    —— 一步到位，**不是两次撤销**（用户要的是"这一下能不能退回去"）。
    """
    at: int
    old_text: str = ''


class StructureUndoStack:
    """有界结构撤销栈：只记"做过什么"，不碰会话状态。

    有界是刻意的 —— 它是交互退路，不是版本控制；`del ops[:-limit]` 与旧版
    的 `del stack[:-STRUCT_UNDO_LIMIT]` 同一个语义（超过就丢最老的）。
    """

    def __init__(self, limit: int = STRUCT_UNDO_LIMIT):
        self.limit = int(limit)
        self._ops: list = []

    def __bool__(self) -> bool:
        return bool(self._ops)

    def __len__(self) -> int:
        return len(self._ops)

    def push(self, op) -> None:
        self._ops.append(op)
        del self._ops[:-self.limit]         # 只留最近的

    def pop(self):
        """弹出最近一次；栈空返回 `None`（**不抛** —— 调用方要出提示语）。"""
        return self._ops.pop() if self._ops else None

    def clear(self) -> None:
        """换文件 / 重新对齐后调用：索引全变了，旧记录不再有意义。"""
        self._ops.clear()


def undo(op, host) -> dict:
    """把一次结构动作**倒回去**（`op` 见本模块四个 `*Op`）。

    返回 `{ok, error, index, note}`；`ok=False` 时 `error` 说明为什么退不了。

    `host` 是会话（`SessionTableMixin`），它必须提供：

      · `beats`（可读可写的列表 —— 删卡撤销是**就地**插回，然后
        `doc.adopt(host.beats)` 按拍上原有结构位重建树）
      · `_chapter_doc()` / `_sync_chapter()` / `_renumber()`
      · `forget_undo()`（内容撤销栈作废：拍号变了）
      · `refresh_edits_flag()`（撤到与磁盘一致时别再喊"未导出"）
      · `_last_removed`（单级"恢复删除"那个字段）
    """
    doc = host._chapter_doc()
    kind = type(op).__name__
    if isinstance(op, InsertOp):
        at = int(op.at)
        if not 0 <= at < len(host.beats):
            return {'ok': False,
                    'error': '落点已经不在了（可能被别的操作改过）',
                    'index': -1}
        try:
            doc.remove_beat_at(at)
        except ChapterTreeError as e:
            return {'ok': False, 'error': str(e), 'index': -1}
        host.beats = doc.beats
        note = '撤销插卡'
        index = min(at, max(0, len(host.beats) - 1))
    elif isinstance(op, DeleteOp):
        at = max(0, min(int(op.at), len(host.beats)))
        removed = (list(op.beats) if isinstance(op.beats, (list, tuple))
                   else [op.beats])
        host.beats[at:at] = removed
        doc.adopt(host.beats)
        host.beats = doc.beats
        note = '恢复删除'
        index = at
        if host._last_removed is not None and host._last_removed[0] == at:
            host._last_removed = None
    elif isinstance(op, ClearOp):
        # 分级删除的"清空"：把清掉的字段原样放回去
        at = int(op.at)
        if not 0 <= at < len(host.beats):
            return {'ok': False,
                    'error': '那一拍已经不在了（可能被别的操作改过）',
                    'index': -1}
        from storygraph.app.beat_clear import restore_fields
        restore_fields(host.beats[at], op.saved or {})
        note = '撤销清空'
        index = at
    elif isinstance(op, SplitOp):
        # 一句拆两口：新卡拔掉、原文写回 —— **一步**回到没拆之前。
        at = int(op.at)
        src = at - 1
        if not (0 <= src < len(host.beats)) or not (0 <= at < len(host.beats)):
            return {'ok': False,
                    'error': '那两张卡已经不在了（可能被别的操作改过）',
                    'index': -1}
        try:
            doc.remove_beat_at(at)
        except ChapterTreeError as e:
            return {'ok': False, 'error': str(e), 'index': -1}
        host.beats = doc.beats
        host.beats[src].text = str(op.old_text or '')
        note = '撤销「句中换表情」'
        index = src
    elif isinstance(op, MoveOp):
        src, new_index = int(op.src), int(op.dst)
        try:
            doc.move_beat(new_index, src)
        except ChapterTreeError as e:
            return {'ok': False, 'error': str(e), 'index': -1}
        # 跨块移动会重写结构位/条件；撤销要连它们一起还原，再按 beat
        # 上的旧值重建树（节点 path 也跟着回来）。
        op.beat.nest_path = op.path
        op.beat.condition = op.condition
        doc._rebuild_tree()
        host.beats = doc.beats
        note = '撤销移动'
        index = src
    else:
        return {'ok': False, 'error': f'不认识的结构操作：{kind!r}',
                'index': -1}
    host._sync_chapter()                    # 结构撤销同样不许把顶层块弄丢
    host._renumber()
    host.forget_undo()
    host.refresh_edits_flag()               # 撤到与磁盘一致时，别再喊"未导出"
    return {'ok': True, 'error': '', 'index': index, 'note': note}
