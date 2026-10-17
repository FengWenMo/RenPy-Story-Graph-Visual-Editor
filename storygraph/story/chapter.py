"""
storygraph.story.chapter
========================
**章节文档**：拍列表 + 章节级元数据（顶层保真块 / 源路径 / 章节名）。

为什么要有它（调研 P0-2）
-------------------------
`file_blocks`（顶层 `define` / `init python` / `screen` …）从前挂在**第一张拍**
上：

    # beat_build.attach_file_blocks
    first.file_blocks = text

而删除第一拍只是 `beats.pop(idx)` —— 没有任何"把顶层块转移给新第一拍"的
逻辑。于是"删掉第一张卡"就等于：

    原始：init python: persistent.x = 1 → label a: e "one" / e "two"
    删第一拍后：label a: e "two" / return     ← init python 整块消失

这正是文档里反复强调的 P0：顶层 `default` / `init python` / `define` 被
生成冲掉。根因不是某一行漏了转移，而是**章节级的东西住在一个可删除的对象上**。

现在的分工
----------
  · `ChapterDocument.file_blocks` 是**真源**（章节级，删多少拍都不会动它）；
  · `Beat.file_blocks` 降级成**传输副本**：渲染器与 Excel 导出读的是拍列表，
    所以在 `sync()` 时把真源写到第一张拍上，其余拍一律清空；
  · 拍列表被外部整体替换（解析 / Excel 导入 / 单测直接赋值）时，
    `sync()` 先把拍上带来的块**收编**进真源，再重新下发 —— 两个方向都不丢。

`sync()` 是幂等的：连续调用结果一致。它不做任何解析/渲染，只搬运文本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from storygraph.story.chapter_tree import ChapterTree, ChapterTreeError

__all__ = ['ChapterDocument', 'ChapterTreeError', 'order_beats_by_seq']


@dataclass
class ChapterDocument:
    """一章的文档模型：拍 + 章节级元数据。"""

    beats: List = field(default_factory=list)
    #: 顶层保真块（合并文本，块之间用 `FILE_BLOCK_SEP` 分隔）
    file_blocks: str = ''
    #: 这一章从哪个 `.rpy` 载入（会话用；不属于任何一拍）
    source_path: Optional[Path] = None
    #: 章节名（label）；拍上通常已经带着，这里留作空章节的兜底
    label: str = ''
    #: **整章结构树**（2026-10-18）：场景 → 块（if/循环/菜单/选项体）→ 拍。
    #: 它是结构编辑的真源；`beats` 是它的扁平投影（`tree.flatten()`）。
    tree: Optional[ChapterTree] = field(default=None, repr=False)

    # ------------------------------------------------------------------
    #  构造 / 收编
    # ------------------------------------------------------------------
    @classmethod
    def from_beats(cls, beats, source_path=None, label: str = ''):
        """从拍列表建文档：拍上带来的顶层块收编为文档级真源，并建结构树。"""
        # **不复制列表**（只把非 list 转成 list）：`load()` 之后会话的
        # `self.beats` 与文档的 `beats` 必须是同一个对象，否则第一次
        # `_chapter_doc()` 会误判"会话替换了拍列表"而白重建一次树。
        items = beats if isinstance(beats, list) else list(beats or ())
        doc = cls(beats=items, source_path=source_path, label=label)
        doc.file_blocks = doc.collect()
        doc._rebuild_tree()
        doc.sync()
        return doc

    def collect(self) -> str:
        """
        拍列表里所有 `file_blocks` → 一份合并文本（按拍序，去重）。

        **不是**简单字符串相加：同一段块可能同时挂在多张拍上（历史数据、
        复制粘贴、撤销恢复），相加会让产物里出现两遍。
        """
        from storygraph.story.beat_model import (join_file_block_parts,
                                                 split_file_blocks)
        seen, parts = set(), []
        for b in self.beats or ():
            for part in split_file_blocks(getattr(b, 'file_blocks', '')):
                if part.strip() and part not in seen:
                    seen.add(part)
                    parts.append(part)
        return join_file_block_parts(parts)

    # ------------------------------------------------------------------
    #  同步（唯一写入口）
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    #  结构树（2026-10-18：整章树形真源）
    # ------------------------------------------------------------------
    def _rebuild_tree(self):
        """从当前拍列表重建结构树（外部整体替换拍列表时用）。"""
        self.tree = ChapterTree.build(self.beats, self.file_blocks)
        return self.tree

    def _ensure_tree(self):
        """
        保证树与 `beats` 对得上。

        · 没有树 → 建；
        · 拍列表被外部整体替换（解析 / Excel 导入 / 单测直接赋值）→ 重建。
        结构性编辑**不**走这里：它们先改树，再由 `flatten()` 生成新投影。
        """
        if self.tree is None or not self.tree.matches(self.beats):
            self._rebuild_tree()
        return self.tree

    def adopt(self, beats):
        """接管一份新的拍列表（外部替换的唯一入口）：重建树 + 收编顶层块。"""
        self.beats = list(beats or ())
        self._rebuild_tree()
        self.sync()
        return self

    def insert_beat(self, beat, at: int, inherit: bool = True) -> int:
        """在扁平位置 `at` 插一拍（落点/结构位由树决定），返回新下标。"""
        self._ensure_tree()
        index = self.tree.insert_beat(beat, at, inherit=inherit)
        self.beats = self.tree.flatten()
        return index

    def remove_beat_at(self, idx: int, cascade: bool = False):
        """
        删掉第 `idx` 拍；块头带块体时默认抛 `ChapterTreeError`。

        `cascade=True`（明确"删整块"）→ 连同整棵子树一起删。
        返回**按顺序的被删拍列表**（单个删除也返回长度 1 的列表）——
        这样级联删除可以原样交给撤销栈。
        """
        self._ensure_tree()
        if cascade:
            removed = self.tree.remove_subtree(int(idx))
        else:
            removed = [self.tree.remove_beat(int(idx))]
        self.beats = self.tree.flatten()
        return removed

    def move_beat(self, src: int, dst: int) -> int:
        """移动一拍（块头不许动；跨块会重写结构位），返回新下标。"""
        self._ensure_tree()
        index = self.tree.move_beat(int(src), int(dst))
        self.beats = self.tree.flatten()
        return index

    def outline(self) -> list:
        """结构树的人话大纲（日志 / 报告用）。"""
        return self._ensure_tree().outline()

    def sync(self):
        """
        真源 ↔ 拍上的传输副本对齐；返回 `self`（便于链式调用）。

        两种方向都要照顾：

          · 拍上出现了文档里没有的块（解析 / Excel 导入 / 外部改过）→ **收编**；
          · 拍上少了（删了携带拍）→ 从文档**重新下发**。

        收编优先：外部带来的新内容比文档里的旧副本新。收编判据是
        "合并后文本不同"，相同则不动（幂等）。

        ⚠ 结构树在这里只做**一致性检查**：外部替换了拍列表才重建；
        结构性编辑（`insert_beat` / `remove_beat_at`）自己维护树，不会被
        这次调用冲掉。
        """
        carried = self.collect()
        if carried and carried != self.file_blocks:
            self.file_blocks = carried
        self._ensure_tree()
        # 清掉每一拍上的传输副本（避免下一拍又被当成真源、也避免重复渲染）
        for b in self.beats or ():
            b.file_blocks = ''
        if self.beats and self.file_blocks:
            self.beats[0].file_blocks = self.file_blocks
        return self

    def attach(self, text: str):
        """
        追加/覆盖一段顶层块文本（表 → 拍那条路用）。

        与 `beat_build.attach_file_block_text` 同义，但落点是**文档**：
        没有拍也丢不了（文档自己记着）。
        """
        from storygraph.story.beat_model import (join_file_block_parts,
                                                 split_file_blocks)
        parts = split_file_blocks(self.file_blocks) + split_file_blocks(text)
        # 去重保序
        seen, kept = set(), []
        for p in parts:
            if p.strip() and p not in seen:
                seen.add(p)
                kept.append(p)
        self.file_blocks = join_file_block_parts(kept)
        return self.sync()

    def take(self) -> str:
        """取走真源文本并把拍上的副本清空（导出/另存时的"交出"）。"""
        text = self.file_blocks
        self.file_blocks = ''
        for b in self.beats or ():
            b.file_blocks = ''
        return text


# ======================================================================
#  排序权威（P0-3）
# ======================================================================
def order_beats_by_seq(beats) -> tuple:
    """
    按 `Seq` 把**行顺序**理顺；返回 `(新列表, 警告列表)`。

    为什么必须有这一条（调研 P0-3）
    ------------------------------
    `Seq` 看起来是排序键，但 Excel 导入从来不按它排；渲染又按**列表顺序**
    走。于是"在 Excel 里按说话人排一下 / 拖动一行"就会**静默改剧情顺序**：

        原始：Seq 1 = ONE, Seq 2 = TWO
        Excel 里交换两行 → 产物 e "TWO" / e "ONE"

    而 `validate` 的断号检查只看重复和缺口，不看倒序，所以一声不吭。

    处置口径（唯一权威 = `Seq`，但**不硬来**）
    -----------------------------------------
      · 只在**同一个 label 的连续段**内排（渲染也是按连续 label 分段）；
      · 段内 `Seq > 0` 的行按 Seq 稳定排序，**Seq 缺省（0/空）的行不动** ——
        手工新加、还没编号的行必须留在它被放的位置，不能因为"没号"被甩到
        段尾；
      · `Seq` 有重复 → **不排**（无法判断谁先），交给 `validate` 报 E203；
      · 顺序真的变了 → 返回一条警告，调用方把它记进
        `Beat.runtime_notes`，`validate` 报 W203 让人看见"工具替你重排了"。

    为什么不是"检测到不一致就报错拒读"：表是给人用的，排序/筛选是常规操作；
    拒读会让"我只是按说话人排了一下"变成打不开。按 Seq 重排 + 明确警告，
    既不改剧情，也不静默。
    """
    items = list(beats or ())
    warnings: list = []
    if len(items) < 2:
        return items, warnings

    out = []
    i = 0
    while i < len(items):
        label = getattr(items[i], 'label', '') or ''
        j = i + 1
        while j < len(items) and (getattr(items[j], 'label', '') or '') == label:
            j += 1
        run = items[i:j]
        ordered = _order_run(run)
        if ordered is not run:
            moved = sum(1 for a, b in zip(run, ordered) if a is not b)
            warnings.append(
                f"label '{label or '（空）'}' 的行顺序与 Seq 不一致："
                f"已按 Seq 重排（动了 {moved} 行）。"
                f"要改剧情顺序，请同时改「序号」列 —— 只拖动行不会生效。")
        out.extend(ordered)
        i = j
    return out, warnings


def _order_run(run: list) -> list:
    """一段同 label 的拍：按 Seq 重排（缺号/重复时按规则保守处理）。"""
    slots = [k for k, b in enumerate(run)
             if _seq_of(b) > 0]
    if len(slots) < 2:
        return run
    seqs = [_seq_of(run[k]) for k in slots]
    if len(set(seqs)) != len(seqs):
        return run                     # 重复 → 不排，交给 validate 报 E203
    picked = sorted((run[k] for k in slots), key=_seq_of)
    candidate = list(run)
    for slot, beat in zip(slots, picked):
        candidate[slot] = beat
    # 顺序没动就返回**原列表对象**：调用方用 `is` 判"要不要警告"，
    # 一律返回新列表会让"本来就排好了"也报 W203（假警告比漏报更烦人）。
    if all(a is b for a, b in zip(run, candidate)):
        return run
    return candidate


def _seq_of(beat) -> int:
    try:
        return int(getattr(beat, 'seq', 0) or 0)
    except (TypeError, ValueError):
        return 0
