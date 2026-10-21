"""
storygraph.app.session_table
============================
`EditorSession` 的**表格 / 拍编辑那一半** —— P2-6 拆分的**最后一块**。

它是这个类的**核心**：其余三块（剧情图 / 资产 / 导出）都只是围绕它
转的外围。所以它放在最后拆 —— 前面三块把"拆分该怎么做"的流程和守卫
都跑熟了，再动最密的那张网。

拆完之后 `EditorSession` 自己只剩 9 个成员（`__init__` / `registry` /
`source` / `refresh_symbols` / `has` / `_renumber` / `__len__` /
`forget_undo` / `compile`）—— 那是**被四块共用的基建**，不该再拆：
任何一块把它搬进去，都会让另外三块反向依赖那一块。

职责边界
--------
**属于这里**：单张拍的读写（`fields` / `cards` / `apply` / `set_speaker` /
  `remove_sprite` / `stage_digest_text` / `inherited_hint` …）、
  拍的增删（`insert_beat` / `delete_beat` / `restore_last_removed`）、
  逐拍撤销（`snapshot` / `restore` / `save` / `undo` / `has_undo` /
  `mark_loaded`）、载入（`load`）。

**留在 `EditorSession`**（见那边的 `KEEP` 说明）：整张会话的状态与基建。

`BeatSnapshot` / `_SNAPSHOT_FIELDS` / `PRIMARY_FIELDS` / `ADVANCED_FIELDS` /
`uniform_position_of` 也搬到这里 —— 前四个只有本块与前端用，最后一个
（`uniform_position_of`）是 `apply` 的私有投影。`editor.py` 里留了
re-export，`main_window.py:73` 那种 `from …editor import ADVANCED_FIELDS`
的老写法继续可用。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from storygraph.core.safeguard import swallow

from storygraph.app import beats as B
from storygraph.story.beat_build import (beats_from_parse_result,
                                         hydrate_from_dsl, stage_only_ops)
from storygraph.story.beat_render import make_symbols
from storygraph.story.chapter import ChapterDocument
from storygraph.story.chapter_tree import ChapterTreeError
from storygraph.story.beat_model import (Beat, KIND_MENU, MENU_NO_CAPTION,
                                         SNAPSHOT_FIELDS,
                                         compose_stage_extra)
from storygraph.story.spec_args import is_dynamic_column


__all__ = ['TableSessionMixin', 'BeatSnapshot', 'uniform_position_of']


#: 撤销快照要装的字段。
#:
#: **不再手写**（2026-10-17）：由 `story.beat_fields.SNAPSHOT_FIELDS` 派生 ——
#: 同一份 `FieldSpec` 表同时决定列、快照、指纹与协作 payload。
#: 从前这里是一张手写清单，漏了 `spec_head` / `loop_header` /
#: `camera_header` / `opaque` / `nest_path` / `code_blocks` /
#: `inline_comment` / `cond_comment` / `file_blocks` —— 改了这些字段后
#: 快照指纹不变、撤销按钮不亮、restore 把它们留在新值上。
#:
#: 仍然刻意不含 `src_first` / `src_last`（增量导出的源文件行号）与
#: `_from_script`：撤销一次字段编辑不该改动"这一拍在源文件的哪几行"。
_SNAPSHOT_FIELDS = SNAPSHOT_FIELDS


@dataclass
class BeatSnapshot:
    """
    一拍在某个时刻的**内容快照**（撤销的载体）。

    为什么不用 `copy.deepcopy(beat)`：`deepcopy` 会把 `ordered`、
    `_hydrated_stage` 这些派生状态一起抄走，撤销之后可能带着一份
    "已经吸过"的标记却对应着新的 stage —— 于是 hydrate 跳过、立绘丢失。
    所以只抄**明确列出的字段**，其余由 `restore` 之后的重算负责。
    """
    idx: int
    data: dict = field(default_factory=dict)

    def get(self, name, default=''):
        return self.data.get(name, default)

    def fingerprint(self) -> str:
        """
        快照内容的指纹（用来判"这次保存到底改了没有"）。

        **不能拿 `Beat.beat_fingerprint` 当判据**：那是**持久化**字段
        （进 Excel 的「内容指纹」列、用于协作冲突检测），只在
        `_assign_identity` 时重算 —— 编辑之后它还是旧值，于是
        "改了内容却判成没变"（正好是这个功能的判据要避免的事）。

        这里按快照里**当前的**值现算一个短哈希：字段集固定，
        所以两份快照可比。
        """
        import hashlib
        blob = '\x1f'.join(f'{k}={self.data.get(k)!r}'
                           for k in sorted(self.data))
        return hashlib.md5(blob.encode('utf-8', 'replace')).hexdigest()[:12]
def uniform_position_of(cards: list) -> str:
    """
    卡数组 → 整拍位置。**只有所有立绘位置一致才返回它**，否则留空。

    留空很关键：位置是"整拍"的属性，若不一致还硬写一个，
    会兜底套到别的立绘头上 → 立绘错位（实测踩过）。
    """
    rows = [{'at': (str(c.get('position') or '').strip()
                    or str(c.get('at') or '').strip())} for c in (cards or [])]
    return B.uniform_sprite_position(rows)


class TableSessionMixin:
    """
    表格 / 拍编辑那一半（见模块 docstring 的职责边界）。

    它**要求宿主提供**会话级基建：`beats` / `symbols` / `script_path` /
    `registry()` / `refresh_symbols()` / `has()` / `_renumber()` /
    `forget_undo()` / `_last_saved` / `_last_idx` / `_last_removed` ——
    见 `REQUIRES`（**实测**出来的，不是凭印象）。
    """

    #: 本 mixin 要求宿主（或其它 mixin）提供的成员。
    REQUIRES = ('beats', 'symbols', 'script_path', 'registry',
                'refresh_symbols', 'has', '_renumber', 'forget_undo',
                '_last_saved', '_last_idx', '_last_removed', 'chapter')

    # ------------------------------------------------------------------
    #  章节级真源：顶层保真块（P0-2）
    # ------------------------------------------------------------------
    def _chapter_doc(self) -> ChapterDocument:
        """
        取本章文档对象；没有（老调用方 / 单测直接塞 `beats`）就**懒建**。

        `chapter` 在 `EditorSession.__init__` 里有默认值，但会话也可能
        被测试直接构造后替换 `beats` —— 懒建让两条路都安全。
        """
        doc = getattr(self, 'chapter', None)
        if not isinstance(doc, ChapterDocument):
            doc = ChapterDocument.from_beats(
                getattr(self, 'beats', None) or (),
                source_path=getattr(self, 'script_path', None))
            self.chapter = doc
        if doc.beats is not self.beats:
            # 会话替换了拍列表（解析 / 导入 / 直接赋值）：文档跟着换，
            # 并把拍上带来的顶层块收编进来（sync 的第一条方向）。
            doc.adopt(self.beats)
        return doc

    def _sync_chapter(self):
        """
        把章节真源与拍上的传输副本对齐。**每次结构编辑之后都要调**。

        为什么必须集中成一个方法：从前"顶层块挂在第一拍上"这条约定散在
        `attach_file_blocks` / Excel 导出 / 渲染三处，谁都没负责"删除第一拍
        之后怎么办"。现在只有一个写入口（`ChapterDocument.sync`）。
        """
        return self._chapter_doc().sync()

    def chapter_file_blocks(self) -> str:
        """当前章节的顶层保真块（文档级真源，与第一拍在不在无关）。"""
        return self._chapter_doc().file_blocks

    def load(self, path, label=None) -> int:
        """
        载入一个 .rpy，解析成 Beat 列表。返回拍数。

        与旧路径**同一条语义**：ParseResult → beats → 补 Stage → hydrate DSL。

        `label` 只决定界面**先落到哪一拍**（同一个 .rpy 里可以有多个 label）。
        它不改变"载入整个文件"这件事 —— 导出、保存、变量分析仍然面对整个文件。
        找不到时由前端明确提示，并退回第 1 拍（`index_of_label()` 返回 -1）。
        """
        from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                     RenPyParser)
        path = Path(path)
        pr = ParseResultBuilder().build(RenPyParser().parse(str(path)))
        raw = beats_from_parse_result(pr)
        symbols = self.refresh_symbols()
        self.beats = list(raw)
        #: 界面当前聚焦的 label（同一个文件里点第二个节点时用它定位）。
        #: 空串 = 没指定，按老行为从第一拍开始。
        self.focus_label = str(label or '').strip()
        for b in self.beats:
            b.stage = compose_stage_extra(b, symbols)
        hydrate_from_dsl(self.beats, symbols)
        #: **章节真源**：顶层 `init python` / `default` / `define` … 从"第一张拍
        #: 的附属字段"升成章节级（2026-10-17，P0-2）。`from_beats` 会把拍上
        #: 带来的块收编进来，再把传输副本挂回第一拍（幂等）。
        self.chapter = ChapterDocument.from_beats(self.beats,
                                                  source_path=path)
        self.script_path = path
        # **记下"我这一份拍是从哪份字节解析出来的"**（2026-10-05 用户实测报：
        # "我只调了一个卡片，都不能替换"）。拍的 `src_first` / `src_last` 是
        # **运行期真迹**——只要磁盘上那份文件没变，它们就逐行可靠；
        # 补丁那条路因此可以相信行号，而不是只靠"内容能不能对上"（用户把一句
        # 旁白改成台词、同时加了一张立绘时，内容证据本来就全没了）。
        # 文件要是被外部改过，指纹就对不上 → 退回保守的对位守卫（今天的默认）。
        self._source_fingerprint = self._fingerprint_of(path)
        # 换了文件，旧的撤销记录就没有意义了（拍号对不上别的章节）。
        self.forget_undo()
        self._struct_undo = []          # 结构撤销栈同理：索引全变了
        return len(self.beats)

    def label_names(self) -> list:
        """这个文件里按出现顺序有哪些 label（去重；空 label 不列）。"""
        out: list = []
        for b in self.beats:
            name = str(getattr(b, 'label', '') or '').strip()
            if name and name not in out:
                out.append(name)
        return out

    def index_of_label(self, label) -> int:
        """
        `label` 的第一拍在 `beats` 里的下标；找不到返回 -1。

        为什么前端需要它：同一个 `.rpy` 可以有多个 label，而卡片页从前
        只会 `_show_index(0)` —— 点第二个节点，打开的永远是文件第一拍。
        数据层本来就知道每拍的 `label`，缺的只是这个查询。
        """
        wanted = str(label or '').strip()
        if not wanted:
            return 0
        for i, b in enumerate(self.beats):
            if str(getattr(b, 'label', '') or '').strip() == wanted:
                return i
        return -1

    @staticmethod
    def _fingerprint_of(path):
        """
        `(大小, mtime_ns, sha1[:16])`；读不出来返回 None。

        为什么三样都要：大小/mtime 便宜（绝大多数情况一眼就够），sha1 兜住
        "改了内容又把 mtime 改回去"这种刻意情形（备份还原、编辑器另存）。
        """
        try:
            p = Path(path)
            st = p.stat()
            import hashlib
            digest = hashlib.sha1(p.read_bytes()).hexdigest()[:16]
            return (int(st.st_size), int(st.st_mtime_ns), digest)
        except OSError:
            return None

    def source_unchanged(self) -> bool:
        """
        磁盘上这一章还是"载入时那一份"吗？

        `False` 时（读不出来、或真变了）调用方要退回**保守**的判据 ——
        行号可能已经过期，只能靠内容对位（见 `patcher._range_looks_like`）。
        """
        fp = getattr(self, '_source_fingerprint', None)
        if fp is None or not self.script_path:
            return False
        return self._fingerprint_of(self.script_path) == fp

    def insert_beat(self, idx: int = None, after: bool = True) -> dict:
        """
        在当前这一拍**后面**插入一张空卡片，返回新拍号。

        用户 2026-10-03：

        > 你需要加入一个：插入新卡片的功能和删除该卡片的功能，
        > 不然只能够修改，不能增删，还是没用！

        新卡的字段**刻意是空的**（不是从上一拍复制的）：
        复制会让"背景/音乐"这类持续状态被**显式写死**到新的一拍上 ——
        用户以为自己只是插了一句话，结果脚本里多了一条 `scene`。

        `label` 沿用邻拍的（新卡要落在同一个 label 块里，否则生成的代码
        会跑到别的章节去）。`seq`/`line_id` 由这里重排，保证唯一。
        """
        if not self.beats:
            return {'ok': False, 'error': '还没有载入章节', 'index': -1}
        pos = len(self.beats) if idx is None else int(idx)
        pos = max(0, min(pos, len(self.beats) - 1))
        at = pos + 1 if after else pos
        from storygraph.story.beat_model import Beat
        template = self.beats[pos]
        beat = Beat(seq=0, label=str(getattr(template, 'label', '') or ''),
                    kind='say', text='')
        doc = self._chapter_doc()
        at = doc.insert_beat(beat, at)
        self.beats = doc.beats
        self._sync_chapter()                  # 插到第一张之前也不许丢顶层块
        self._push_struct_undo(('insert', at))
        self._renumber()
        self.forget_undo()                    # 拍号变了，旧的撤销记录对不上
        return {'ok': True, 'error': '', 'index': at,
                'label': beat.label, 'count': len(self.beats)}

    def insert_flow_beat(self, idx=None, after=True, flow_type='call',
                         target='', args='', from_clause='') -> dict:
        """
        在当前拍后（或前）插入一张 `call` / `jump` 控制流卡。

        与 `insert_beat` 的区别只有一处：新拍的 `raw` 是结构化渲染出来的
        `call X` / `jump X`，不是空台词。这样它立刻能被卡片上的控制流条
        认出来，导出也走同一行。
        """
        if not self.beats:
            return {'ok': False, 'error': '还没有载入章节', 'index': -1}
        from storygraph.story.flow import (is_valid_label, render_flow_line)
        flow_type = str(flow_type or 'call').strip()
        target = str(target or '').strip()
        if flow_type not in ('call', 'jump'):
            return {'ok': False, 'error': '控制流类型只能是 call / jump',
                    'index': -1}
        if not is_valid_label(target):
            return {'ok': False, 'error': '目标 label 不合法', 'index': -1}
        pos = len(self.beats) if idx is None else int(idx)
        pos = max(0, min(pos, len(self.beats) - 1))
        at = pos + 1 if after else pos
        from storygraph.story.beat_model import Beat
        template = self.beats[pos]
        beat = Beat(seq=0, label=str(getattr(template, 'label', '') or ''),
                    kind='narration',
                    raw=render_flow_line(flow_type, target, args, from_clause))
        doc = self._chapter_doc()
        at = doc.insert_beat(beat, at)
        self.beats = doc.beats
        self._sync_chapter()
        self._push_struct_undo(('insert', at))
        self._renumber()
        self.forget_undo()
        return {'ok': True, 'error': '', 'index': at,
                'label': beat.label, 'count': len(self.beats)}

    def delete_preview(self, idx: int, cascade: bool = False) -> dict:
        """
        删这一拍会让**产物**少什么（几行、前几行是什么）—— **不改任何状态**。

        为什么需要（用户 2026-10-21 实测）：

        > breakend.0027 这一拍包含 7 行代码 + 1 句台词……删这一拍就是删 8 行，
        > -8+2=-6，数目完全对。所以数据是对的，问题是**人看不见**：这一拍的
        > 源码写在表里「代码」那一格，而 Excel 单元格默认只有一行高，滚动时
        > 根本看不出里面塞了 7 行。

        确认框从前只写"谁：台词"，而删掉的可能是 8 行（7 行代码 + 1 句台词）。
        这里按**与导出一模一样的渲染路径**算出那几行，交给界面原样说出来 ——
        不比改作用域便宜得更划算的东西了：它直接对上"不许静默丢行"。

        返回 `{ok, count, lines, head, beats, blocked, blocked_count,
        can_cascade, error}`；`head` 是前几行（最多 3 行，已去掉缩进）。
        """
        from copy import copy

        empty = {'ok': False, 'error': '', 'count': 0, 'lines': 0, 'head': [],
                 'beats': [], 'blocked': '', 'blocked_count': 0,
                 'can_cascade': False}
        if not self.has(idx):
            return dict(empty, error='这一拍不存在')
        if len(self.beats) <= 1:
            # 与 `delete_beat` 同一条规矩：预览也不许说"能删"然后真删时被拒
            return dict(empty, error='一章至少要留一拍')
        try:
            # **在副本上试删**：`from_beats` 不复制列表，直接拿 self.beats 建
            # 文档会在真文档上动结构（预览不许有副作用）。
            temp = ChapterDocument.from_beats(
                [copy(b) for b in self.beats],
                source_path=getattr(self, 'script_path', None))
            removed = temp.remove_beat_at(int(idx), cascade=bool(cascade))
        except ChapterTreeError as e:
            return dict(empty, error=str(e), blocked=e.kind,
                        blocked_count=int(getattr(e, 'count', 0) or 0),
                        can_cascade=e.kind == 'has_children')
        except Exception as e:                    # noqa: BLE001 - 预览失败不拦操作
            swallow('app.session_table.delete_preview', e)
            return dict(empty, error='算不出这一拍会删掉什么')

        rows, head, total = [], [], 0
        for beat in removed:
            lines = self.rendered_lines_of(beat)
            total += len(lines)
            for ln in lines:
                if len(head) < 3:
                    head.append(str(ln).strip())
            rows.append({
                'line_id': str(getattr(beat, 'line_id', '') or ''),
                'speaker': str(getattr(beat, 'speaker', '') or ''),
                'text': str(getattr(beat, 'text', '') or '')[:60],
                'lines': len(lines),
            })
        return {'ok': True, 'error': '', 'count': len(removed),
                'lines': total, 'head': head, 'beats': rows,
                'blocked': '', 'blocked_count': 0, 'can_cascade': False}

    def rendered_lines_of(self, beat) -> list:
        """
        这一拍在**产物**里是哪些行（与导出同一条渲染路径，不另写一套）。

        `deepcopy` 是必须的：`hydrate_from_dsl` 会往拍上写结构化字段，
        而这里只是"预览会删掉什么"，绝不许改到用户手里那一拍。
        """
        from copy import deepcopy

        try:
            probe = deepcopy(beat)
        except Exception as e:                    # noqa: BLE001 - 拷不动就原地渲染
            swallow('app.session_table.rendered_lines_of.copy', e)
            probe = beat
        try:
            from storygraph.story.beat_build import hydrate_from_dsl
            hydrate_from_dsl([probe], getattr(self, 'symbols', None))
        except Exception as e:                    # noqa: BLE001 - 不 hydrate 也要给行
            swallow('app.session_table.rendered_lines_of.hydrate', e)
        try:
            from storygraph.story.beat_render import render_beat_to_rpy
            return list(render_beat_to_rpy(probe))
        except Exception as e:                    # noqa: BLE001
            swallow('app.session_table.rendered_lines_of.render', e)
            return []

    def delete_beat(self, idx: int, cascade: bool = False) -> dict:
        """
        删掉第 idx 张卡片，返回 `{ok, index, removed, count}`。

        `cascade=True`：明确"删整块"—— 块头（菜单/循环/选项体）连同整棵
        子树一起删；默认只删单拍，块头带块体时**拒绝**（`blocked=has_children`），
        把"要不要连块体一起删"交回给人确认。

        **只改内存**：落盘仍要按「⤓ 导出」，所以误删的退路是
        `restore_last_removed()`（单级）或重新载入这一章。
        """
        if not self.has(idx):
            return {'ok': False, 'error': '这一拍不存在', 'removed': None}
        if len(self.beats) <= 1:
            return {'ok': False, 'error': '一章至少要留一拍', 'removed': None}
        doc = self._chapter_doc()
        try:
            removed = doc.remove_beat_at(idx, cascade=cascade)
        except ChapterTreeError as e:
            # 块头带着块体：删掉它会把选项/循环体变成块外的普通拍 —— 拒绝。
            return {'ok': False, 'error': str(e), 'removed': None,
                    'count': len(self.beats), 'blocked': e.kind,
                    'blocked_count': e.count,
                    'can_cascade': e.kind == 'has_children'}
        self.beats = doc.beats
        # **删除携带拍 ≠ 删除章节级顶层块**（P0-2）：`ChapterDocument` 是真源，
        # 这里把文档里的块重新下发给新的第一拍。没有这一步，删掉第一张卡
        # 就等于把 `init python` / `default` 整块丢掉。
        self._sync_chapter()
        self._last_removed = (idx, list(removed))
        self._push_struct_undo(('delete', idx, list(removed)))
        self._renumber()
        self.forget_undo()
        return {'ok': True, 'error': '', 'index': min(idx, len(self.beats) - 1),
                'removed': removed[0] if removed else None,
                'count': len(self.beats)}

    def move_beat(self, src: int, dst: int) -> dict:
        """
        把第 `src` 拍移到扁平位置 `dst`（移动后的下标）。

        结构由 `ChapterDocument.tree` 决定：**块头不许搬**（会把块体留在原地）；
        跨块移动会重写这一拍的结构位（`nest_path`）与条件，不会留下"路径还是
        旧块"的拍。移动操作进结构撤销栈，可撤。
        """
        if not self.has(src):
            return {'ok': False, 'error': '这一拍不存在', 'index': src}
        beat = self.beats[src]
        old_path = getattr(beat, 'nest_path', None)
        old_cond = str(getattr(beat, 'condition', '') or '')
        doc = self._chapter_doc()
        try:
            new_index = doc.move_beat(src, dst)
        except ChapterTreeError as e:
            return {'ok': False, 'error': str(e), 'index': src,
                    'blocked': e.kind}
        self.beats = doc.beats
        self._push_struct_undo(('move', src, new_index, beat, old_path,
                                old_cond))
        self._renumber()
        self.forget_undo()
        return {'ok': True, 'error': '', 'index': new_index,
                'count': len(self.beats)}

    def restore_last_removed(self) -> dict:
        """把**上一次删掉的那张卡片**插回原位（单级撤销）。"""
        if self._last_removed is None:
            return {'ok': False, 'error': '没有可恢复的卡片', 'index': -1}
        idx, removed = self._last_removed
        beats = (list(removed) if isinstance(removed, (list, tuple))
                 else [removed])
        if not beats:
            return {'ok': False, 'error': '没有可恢复的卡片', 'index': -1}
        at = max(0, min(int(idx), len(self.beats)))
        self.beats[at:at] = beats
        doc = self._chapter_doc()
        doc.adopt(self.beats)                  # 按拍上原有结构位重建树
        self.beats = doc.beats
        self._sync_chapter()                  # 恢复的拍不许把顶层块带重
        self._last_removed = None
        self._push_struct_undo(('insert', at))
        self._renumber()
        self.forget_undo()
        return {'ok': True, 'error': '', 'index': at, 'count': len(self.beats)}

    #: 结构撤销栈最多留多少步（交互退路，不是版本控制）
    STRUCT_UNDO_LIMIT = 50

    def _push_struct_undo(self, op) -> None:
        stack = getattr(self, '_struct_undo', None)
        if stack is None:
            self._struct_undo = stack = []
        stack.append(op)
        del stack[:-self.STRUCT_UNDO_LIMIT]     # 只留最近的

    def can_undo_structural(self) -> bool:
        return bool(getattr(self, '_struct_undo', None))

    def struct_undo_depth(self) -> int:
        return len(getattr(self, '_struct_undo', None) or ())

    def undo_structural(self) -> dict:
        """
        撤销**最近一次结构编辑**（插卡或删卡），可连按多级。

        与内容撤销（`undo()`）分开：
          · `undo()` 回到"这一拍上一次保存"的内容；
          · `undo_structural()` 退回"上一次增删卡片"这个**结构动作**。
        互不影响：内容编辑不动结构栈；结构编辑清的是**内容**栈
        （拍号变了，内容撤销记录对不上）。
        """
        stack = getattr(self, '_struct_undo', None)
        if not stack:
            return {'ok': False, 'error': '没有可撤销的结构操作', 'index': -1}
        op = stack.pop()
        kind = op[0]
        doc = self._chapter_doc()
        if kind == 'insert':
            at = int(op[1])
            if not 0 <= at < len(self.beats):
                return {'ok': False,
                        'error': '落点已经不在了（可能被别的操作改过）',
                        'index': -1}
            try:
                doc.remove_beat_at(at)
            except ChapterTreeError as e:
                return {'ok': False, 'error': str(e), 'index': -1}
            self.beats = doc.beats
            note = '撤销插卡'
            index = min(at, max(0, len(self.beats) - 1))
        elif kind == 'delete':
            at = max(0, min(int(op[1]), len(self.beats)))
            removed = list(op[2]) if isinstance(op[2], (list, tuple)) else [op[2]]
            self.beats[at:at] = removed
            doc.adopt(self.beats)
            self.beats = doc.beats
            note = '恢复删除'
            index = at
            if self._last_removed is not None and self._last_removed[0] == at:
                self._last_removed = None
        elif kind == 'move':
            src, new_index = int(op[1]), int(op[2])
            beat, old_path, old_cond = op[3], op[4], op[5]
            try:
                doc.move_beat(new_index, src)
            except ChapterTreeError as e:
                return {'ok': False, 'error': str(e), 'index': -1}
            # 跨块移动会重写结构位/条件；撤销要连它们一起还原，再按 beat
            # 上的旧值重建树（节点 path 也跟着回来）。
            beat.nest_path = old_path
            beat.condition = old_cond
            doc._rebuild_tree()
            self.beats = doc.beats
            note = '撤销移动'
            index = src
        else:
            return {'ok': False, 'error': f'不认识的结构操作：{kind!r}',
                    'index': -1}
        self._sync_chapter()                # 结构撤销同样不许把顶层块弄丢
        self._renumber()
        self.forget_undo()
        return {'ok': True, 'error': '', 'index': index, 'note': note,
                'count': len(self.beats), 'depth': len(stack)}

    def has_removed(self) -> bool:
        return self._last_removed is not None

    def fields(self, idx: int) -> dict:
        """这一拍的主区+高级区字段（供表单显示）。"""
        if not self.has(idx):
            return {}
        return B.beat_to_fields(self.beats[idx], self.symbols)

    def cards(self, idx: int) -> list:
        """
        这一拍的**立绘卡**（供网格显示）。

        卡的数据契约（与两套前端一致）：
            {image, expression, position, at, speaking, order_index, extra_rows,
             effective_at, inherited_at}

        `effective_at` / `inherited_at` 是**显示用**的补充（用户 2026-10-03）：

        > 我说的位置不显示，是这个立绘下面的位置没有显示……这是bug，
        > 明明说了在i11……

        根因：`at` 在 Ren'Py 里是**持续状态** —— 这一拍只写
        `show ye base_handdown care`（没有 `at`），位置是更早那一拍
        `show ye care at i11` 留下的。舞台快照（累积推导）知道它现在在 `i11`，
        而卡片只读"这一拍自己写的"，于是显示成 `—`。

        所以这里把**当前生效**的位置也算出来给前端显示（带"继承"标记）；
        写回仍然只写 `position`/`at` —— 用户没碰那一格就不会把继承值
        物化到这一拍（那是"改了才写"的底线）。
        """
        if not self.has(idx):
            return []
        rows = B.beat_sprite_rows(self.beats[idx], self.symbols)
        effective = self._effective_positions(idx)
        items = []
        for card in B.group_sprites_by_tag(rows):
            it = dict(card.get('active') or {})
            it['extra_rows'] = list(card.get('extras') or [])
            it['order_index'] = card.get('first_index', 0)
            own = (str(it.get('at') or '').strip()
                   or str(it.get('position') or '').strip())
            eff = effective.get(str(it.get('image') or '').strip(), '')
            it['effective_at'] = eff
            it['inherited_at'] = '' if own else (eff or '')
            items.append(it)
        return items

    def _effective_positions(self, idx: int) -> dict:
        """
        这一拍**屏幕上真正生效**的立绘位置：`{image: at}`。

        来自 `snapshot_rows`（与预览、与 Excel 的舞台快照表同源）——
        那里按 Ren'Py 的语义累积：`at` 不写就沿用上一次。
        """
        try:
            from storygraph.story.stage_snapshot import snapshot_rows
            rows = snapshot_rows(self.beats)
            if idx >= len(rows):
                return {}
            actors = (rows[idx].get('state') or {}).get('actors') or []
            return {str(img): str(at or '') for img, at in actors}
        except Exception as e:                     # noqa: BLE001
            swallow('app.session_table._effective_positions', e)
            return {}
    def list_entry(self, idx: int) -> dict:
        """
        章节列表要用的一行摘要（前缀图标 / 说话人 / 台词预览）。

        **语句拍**（自定义语句 / `camera` / 循环头）从前一律显示成
        「（无台词）」—— 一条 `play_music …`、一条 `sfx_mix …`、一条
        `styled …` 在左边列表里**长得一模一样**，人要点进去才知道哪条是哪条
        （2026-10-18 用户点名"不是每个写法都跟 show 一样与图片相关"）。
        现在把那几个键一起带出去（`statement` / `statement_label` /
        `statement_head`），由前端决定怎么排版；`text` 仍然是**台词**
        （不是台词拍就是空），老调用方一个都不用改。
        """
        b = self.beats[idx]
        # 控制流拍（`call` / `jump`）：列表里不能显示成「（无台词）」——
        # 那会让所有跳转卡长得一模一样（2026-10-22，P0-4 第一阶段）。
        # **只在真是控制流拍时加键**：普通拍的摘要 dict 形状不变，老快照/
        # 老调用方不受影响。
        from storygraph.story.flow import parse_flow
        flow = parse_flow(b)
        flow_fields = ({'flow_type': flow['type'],
                        'flow_target': flow['target']} if flow else {})
        prefix = {'narration': '☁', 'menu': '⚑', 'menu_option': '➜'}.get(
            getattr(b, 'kind', ''), '')
        statement = str(getattr(b, 'spec_name', '') or '').strip()
        head = str(getattr(b, 'spec_head', '') or '').strip()
        if not statement:
            head = str(getattr(b, 'camera_header', '') or '').strip()
            statement = 'camera' if head else ''
        if not statement:
            head = str(getattr(b, 'loop_header', '') or '').strip()
            statement = head.split()[0] if head.split() else ''
        return {
            'index': idx,
            'who': (getattr(b, 'speaker', '') or '').strip(),
            'prefix': prefix,
            'text': (getattr(b, 'text', '') or '').strip() or '（无台词）',
            'line_id': getattr(b, 'line_id', '') or '',
            'statement': statement,
            'statement_head': head,
            'statement_label': self._statement_label(statement),
            **flow_fields,
        }

    def _statement_label(self, name: str) -> str:
        """语句名 → 项目给它起的人话标签（读不到就空串，不猜）。"""
        if not name:
            return ''
        try:
            from storygraph.story.statement_registry import StatementRegistry
            root = getattr(self, 'project_root', None)
            if not root:
                return ''
            spec = StatementRegistry.for_project(str(root)).match(name)
            return str(getattr(spec, 'label', '') or '')
        except Exception as e:                     # noqa: BLE001 - 标签只是锦上添花
            swallow('app.session_table._statement_label', e)
            return ''
    def inherited_hint(self, idx: int) -> str:
        """继承提示（背景/音乐沿用上一拍时给策划的提示）。"""
        if not self.has(idx):
            return ''
        return B.inherited_state_hint(self.beats, idx)

    def stage_digest_text(self, idx: int) -> str:
        """
        这一拍**生效的舞台状态**（变化 / 背景 / 立绘 / CG / 音乐 / 音效 /
        语音 / 条件 / 备注），多行文本。

        用户 2026-10-03：

        > 为何不写明现在的舞台情况是什么样的，旧版明明都有……

        旧版的预览面板用的是 `story.stage_snapshot.stage_digest`，卡片搬 Qt
        时**没把它接上**（函数一直在，只是没人调用）。这里复用同一份
        `stage_snapshots` 推导 —— 与预览、与 Excel 的「舞台快照」表同源，
        不另写一套"当前舞台"的算法。
        """
        if not self.has(idx):
            return ''
        try:
            from storygraph.story.stage_snapshot import stage_digest
            return stage_digest(self.beats, idx)
        except Exception as e:                     # noqa: BLE001
            swallow('app.session_table.stage_digest_text', e)
            return ''
    def speaking_card_index(self, idx: int) -> int:
        """当前说话人对应哪张立绘卡（-1 = 没有）。"""
        if not self.has(idx):
            return -1
        rows = B.beat_sprite_rows(self.beats[idx], self.symbols)
        return B.speaking_sprite_index(self.beats[idx], rows, self.symbols)

    def apply(self, idx: int, fields: dict, cards: list = None,
              advanced_open: bool = False,
              stage_loaded: str = None) -> Optional[Beat]:
        """
        把表单内容写回第 idx 拍。返回那一拍（越界返回 None）。

        语义（与 tkinter 版 `collect_beat` 一字不差，现在只有这一份实现）：
          · 主区**始终**写回；
          · 高级区**只在展开时**写回（收起 ≠ 清空）；
          · `show` 只有拼得出内容时才覆盖（**空网格不清空立绘**）；
          · `at` 只在"所有立绘位置一致"时才整拍（否则留空，防错位）；
          · `Stage` **每次重算**（它是投影，不是输入），**除非用户真的手改了
            那一格**（见下）；
          · 最后过一遍 `hydrate_from_dsl`，保证与 .rpy 同源。

        `stage_loaded`：表单**载入时**那一格里的原文（Q 前端从
            `BeatCardEditor.load_fields` 里记下来回传）。
            用来判断"用户手改了 Stage 没有" —— 判据是
            `typed != loaded`，而不是"格子非空"：`beat_to_fields` 会把
            结构化列投影进这一格显示，所以非空**不代表**人改过。
            传 None 时退回用 `beat.stage`（老调用方的行为）。
        """
        if not self.has(idx):
            return None
        b = self.beats[idx]
        fields = dict(fields or {})

        # **控制流卡的目标 / 类型**（2026-10-22，P0-4 第一阶段）。
        #
        # 它改的是 `Beat.raw` 里那一行（`call side`），不是任何一个 Excel
        # 列。所以必须在下面的 `CodeBlock` 处理**之前**做：先让 raw 变成
        # 新目标，后面的 `CodeBlock != compose_code(b)` 才会是 False（前端
        # 同时提交了同步后的代码格），不会把 raw 误降级成 code。
        flow_keys = 'FlowType' in fields or 'FlowTarget' in fields
        if flow_keys:
            from storygraph.story.flow import update_beat_flow
            update_beat_flow(
                b,
                fields.get('FlowType') if 'FlowType' in fields else None,
                fields.get('FlowTarget') if 'FlowTarget' in fields else None)

        # **键不存在 = 不动这一格**（2026-10-05 修，与 `fields_to_beat` 同源）。
        #
        # 以前一律 `b.scene = str(fields.get('Scene') or '').strip()`，于是
        # **局部表单会静默清空其它列** —— 实测
        #
        #     s.apply(0, {'Speaker': 'e', 'Text': '只改台词'})
        #
        # 之后 `beat.scene` 从 `'bg classroom'` 变成 `''`：只改了台词，
        # **背景被抹掉了**。前端（Qt / tkinter）交回的永远是完整表单，
        # 所以界面上的行为不变；但任何"只交回改过那几格"的调用方
        # （脚本、测试、将来的增量编辑器）都会踩到。
        #
        # `condition` 早就是这个判据（见下面那段注释），这里把同一套
        # 推广到全部列：**区分"没给我"与"给我空串"**。
        if 'Speaker' in fields:
            b.speaker = str(fields.get('Speaker') or '').strip()
        if 'Text' in fields:
            b.text = str(fields.get('Text') or '').strip()
        # 无标题菜单的占位符要补回来：表单里那一格是空的（`beat_to_fields`
        # 把占位符显示成空串），但"空"在这里的意思是"菜单没有标题"，
        # 不是"这一拍什么都没有"——补不回占位符，`menu:` 块就会消失。
        if b.kind == KIND_MENU and not b.text:
            b.text = MENU_NO_CAPTION
        if 'Scene' in fields:
            b.scene = str(fields.get('Scene') or '').strip()
        if 'BGM' in fields:
            b.bgm = str(fields.get('BGM') or '').strip()
        if 'Voice' in fields:
            b.voice = str(fields.get('Voice') or '').strip()
        if 'Comment' in fields:
            b.comment = str(fields.get('Comment') or '').strip()

        # **「每参数一列」的键**（`mycue·intensity` / `camera·layer@0` /
        # `pause·seconds@0`）：卡片与 Excel 走**同一条折叠路径**
        # （`beat_model.fold_spec_arg_columns`）—— 一份实现两处用，
        # 卡片上改参数与在表里改参数的结果必然一致。
        #
        # ⚠⚠ **折的时机被挪到了这个方法的最末尾**（2026-10-13 实测修）：
        #   放在这里会被后面的 `cards_to_show` / `hydrate_from_dsl` 盖回去
        #   （实测：`show·人物@0` 改成 `ling`，保存后又变回 `lan`）。
        #   真正的调用点在 `return b` 之前，见那里的长注释。
        #
        # 为什么不受 `advanced_open` 影响：这一组控件是跟着这一拍的语句走的
        # （有参数才出现），不像高级区那样会被折叠 —— 折叠起来的东西不写回
        # 是契约（见上面那段），而它一直可见。

        if advanced_open:
            if 'SFX' in fields:
                b.sfx = str(fields.get('SFX') or '').strip()
            if 'CodeBlock' in fields:
                # ⚠⚠ **没改就别动**（2026-10-18 修）。
                #
                # 《代码》那一格是 `raw` + `code` 两份内容**拼出来给人看的**，
                # 而 `raw` 里那些行还带着"它是不是原生语句"的分类（`ordered`）。
                # 无条件写回（并把 `raw` 清空）等于**把分类丢掉**：
                # `code_lines_with_kinds()` 于是对不上 → `unified_plan()` 返回
                # `None` → 渲染退回"按 verb 分组"那条老路 →
                #
                #     play music / play ambience / play sound / stop ambience
                #     保存一次卡片之后变成
                #     play music / play sound / play ambience / stop ambience
                #
                # **顺序被改**，而用户只是打开了卡片、什么都没改。
                # 判据：只有这一格**真的和显示值不同**时才写回。
                new_code = str(fields.get('CodeBlock') or '').strip()
                if new_code != str(B.compose_code(b) or '').strip():
                    b.code = new_code
                    b.raw = ''
            if 'Label' in fields:
                b.label = str(fields.get('Label') or '').strip() or b.label
            if 'Kind' in fields:
                b.kind = str(fields.get('Kind') or '').strip() or b.kind
            if 'Notes' in fields:
                b.notes = str(fields.get('Notes') or '').strip()
            # 台词后缀（2026-10-18）：`(what_color="#f00")` / `with` / `id`。
            # 它在高级区里，所以**只有展开时才写回**（收起 ≠ 清空）。
            if 'SayTail' in fields:
                b.say_tail = str(fields.get('SayTail') or '').strip()
            # 菜单头（2026-10-19）：`menu 名(args):` 的名与参数表。
            # 同样在高级区里 —— 收起时不动（收起 ≠ 清空）。
            if 'MenuName' in fields:
                b.menu_name = str(fields.get('MenuName') or '').strip()
            if 'MenuArgs' in fields:
                b.menu_args = str(fields.get('MenuArgs') or '').strip()
        # **类型是说话人的投影**（`say` / `narration` 这一对，见
        # `app.beats.kind_from_speaker`）：用户只填说话人时，高级区那一格
        # 也必须跟着变成 `say` —— 否则它一直显示 `narration`，用户会以为
        # "我填的说话人没生效"（2026-10-05 实测报的）。
        b.kind = B.kind_from_speaker(b.kind, b.speaker)

        if cards is not None:
            show = B.cards_to_show(cards, self.symbols)
            if show:
                b.show = show
            b.at = uniform_position_of(cards)

        # 条件显示（卡片上的「＋ 条件」）。**只在调用方真的给了这个键时才写**：
        # 用 `fields.get(..., b.condition)` 会让"没给"和"给了空串"分不开，
        # 而这两件事的语义完全相反（不改 vs 清空条件）。
        if 'condition' in fields:
            b.condition = str(fields.get('condition') or '').strip()

        self._apply_stage(b, fields, stage_loaded)

        try:
            hydrate_from_dsl([b], self.symbols)
        except Exception:                    # noqa: BLE001 - 与旧实现一致的兜底
            swallow('editor.apply')
        # **舞台 verb 顺序跟着结构化列走**（2026-10-05 用户实测报："我已经保存
        # 该卡片了，可是当前舞台变化没有生效，并且舞台演出预览没有变化"）：
        # 卡片上加一张立绘只写 `beat.show`，而 `stage_order` 还停在 `'scene'`
        # —— 舞台快照/预览从前只回放列出来的 verb → 看不到这个人（产物其实
        # 是对的，渲染器会补齐）。读的时候两边现在都补齐（`Beat.stage_verbs`），
        # 这里再把**随表走的列**写实，免得下一次从表回来的人又看到"这一拍
        # 没有立绘"。
        try:
            b.sync_stage_order()
        except Exception as e:                # noqa: BLE001 - 顺序列坏了不该拦住保存
            swallow('editor.apply.sync_stage_order', e)
        # ⚠⚠ **语句参数必须在这一切之后再折**（2026-10-13 实测修）。
        #
        # 现场（用户要求"连 show 这种内置语句也能在卡片上长出参数格"）：
        # 卡片上把 `show·人物@0` 从 `lan` 改成 `ling`，保存后 `Beat.show` 又变回
        # `lan swim_suit happy` —— **一个字都没生效**。
        #
        # 根因是**顺序**：动态列在上面（第 480 行那一支）就折好了，可紧接着
        #   · `cards_to_show(cards)` 会用**立绘卡**重建整个 `Beat.show`；
        #   · `hydrate_from_dsl` 会再从 `show` 反推一遍结构化字段；
        # 两件事都把刚折进去的参数**盖回原样**。而 `show` 的参数格恰恰就是
        # `Beat.show` 这一格 —— 于是"卡片上能改、但改了不算"。
        #
        # 动态列是**用户刚在这一格上亲手改的字**，它比"从立绘卡/DSL 推出来的"
        # 更权威，所以放在最后折。（放在这里也顺带让 `sanitized_cells` 的留痕
        # 与"最终内容"一致。）
        self._fold_dynamic_columns(b, fields)
        return b

    @staticmethod
    def _fold_dynamic_columns(b: Beat, fields: dict):
        """把「每参数一列」的那些键折进这一拍（见上面那段顺序说明）。"""
        _dyn = {k: v for k, v in (fields or {}).items()
                if isinstance(k, str) and is_dynamic_column(k)}
        if not _dyn:
            return
        from storygraph.story.beat_model import fold_spec_arg_columns
        fold_spec_arg_columns(b, _dyn)

    def _apply_stage(self, b: Beat, fields: dict, stage_loaded):
        """
        `Stage` 那一格的写回规则（**这是"自己吃自己"事故的正解**）。

        背景：`Stage` 是结构化列的**投影**（`beat_to_fields` 把它算出来显示），
        所以把显示值原样写回再 hydrate 就是自己吃自己 —— 实测
        `hide cg03` 会静默变成 `cg03; cg03`（305 拍里 11 拍被改写）。

        两条分支：
          · **用户真的改了这一格**（`typed` 非空且 ≠ 载入时的原文）：
            以人写的为准，并且**先清掉结构化舞台字段再 hydrate**，
            否则"旧投影 + 新文本"会叠加成两份指令。
          · **没改**：重新投影，并把"结构化列永远表达不了"的那部分
            （`[停顿] 1.0` 之类）原样留下 —— 否则打开卡片点一下下一句，
            作者手写的东西就没了。

        为什么"空值"不算手改：把格子清空是很容易的误操作，而它的后果是
        抹掉这一拍的 scene/show/hide。语料里"我在 Stage 框里删光"不是
        一种需求，所以空值一律走重新投影（**永远不因为空 Stage 丢数据**）。

        ⚠ **还得知道"载入时那一格是什么"才敢判手改**（2026-10-05 实测修）：
        `stage_loaded=None` 时旧代码拿 `b.stage` 当基线，而界面显示的那一格
        往往是**投影**（`beat.stage` 为空 → 显示 `compose_stage(...)`）——
        两者不相等，于是"用户手改了 Stage"被误判，**这一拍刚加上的立绘
        （`b.show`）会被整段清掉**。一个"没告诉过我们基线"的调用方，
        没资格说这是人改的：那就老老实实走重新投影。
        """
        typed = None
        if 'Stage' in fields:                # 表单真的给了这一格（高级区展开）
            typed = str(fields.get('Stage') or '').strip()
        loaded = (b.stage if stage_loaded is None
                  else str(stage_loaded or '')).strip()

        if typed and stage_loaded is not None and typed != loaded:
            b.stage = typed
            for attr in ('scene', 'scene_at', 'show', 'hide', 'at',
                         'with_', 'transform', 'stage_order'):
                setattr(b, attr, '')
            return

        # 没手改：结构化列是唯一真源 → 重新投影；
        # 表达不了的部分（pause / 认不出的原生语句）从原文里捞回来。
        parts = [t for t in (compose_stage_extra(b, self.symbols),
                             stage_only_ops(loaded, self.symbols)) if t]
        b.stage = '; '.join(parts)
        # 投影已经在结构化字段里了 → 标记成"已吸过"，
        # 免得下面那次 hydrate 又把它吸第二遍（见 `hydrate_from_dsl`）。
        try:
            b._hydrated_stage = b.stage
        except Exception:                    # noqa: BLE001 - Beat 不该是槽位
            swallow('editor._apply_stage')
    def set_speaker(self, idx: int, cid: str) -> bool:
        """只改说话人（左栏双击角色时用）。"""
        if not self.has(idx):
            return False
        self.beats[idx].speaker = str(cid or '')
        return True

    def remove_sprite(self, idx: int, image_spec: str) -> Optional[Beat]:
        """从这一拍删掉某个立绘（按 image spec 精确匹配）。"""
        if not self.has(idx):
            return None
        b = self.beats[idx]
        rows = [r for r in B.beat_sprite_rows(b, self.symbols)
                if B.image_spec_of_row(r) != image_spec]
        new_show = B.apply_sprite_rows(getattr(b, 'show', '') or '', rows)
        b.show = new_show
        b.at = B.uniform_sprite_position(rows)
        b.stage = compose_stage_extra(b, self.symbols)
        return b

    def snapshot(self, idx: int) -> Optional[BeatSnapshot]:
        """
        给第 idx 拍拍一张快照（撤销/比较用）。越界返回 None。

        装**所有会进渲染 / 会持久化的字段**（`_SNAPSHOT_FIELDS`，由
        `story.beat_fields` 单表派生），其中 `ordered` / `code_blocks` 是
        列表、按深拷贝存（`$ f()` 与 `show screen` 的交替顺序丢了会把演出
        排乱）。其它状态（`src_first` 等增量导出用的行号、`_from_script`
        标记）刻意**不进快照**：撤销一次编辑不该改掉它们在源文件里的位置。
        """
        if not self.has(idx):
            return None
        b = self.beats[idx]
        # `copy.deepcopy`：`code_blocks` / `ordered` 是**可变列表**。只做浅拷贝的话，
        # 快照和拍会共享同一个列表 —— 之后拍上原地 append 一次，快照也跟着变，
        # 撤销就撤不回去了（`ordered` 从前单独 deepcopy，别的列表没有）。
        data = {name: copy.deepcopy(getattr(b, name, ''))
                for name in _SNAPSHOT_FIELDS}
        # `StageOrder` 是**派生列**（"结构化列里有的 verb 一定在里面"，
        # 见 `Beat.stage_verbs`）。快照记它的**归一化**形式：保存时会对齐它
        # （卡片加了立绘 → 顺序里补上 `show`），而"对齐"不是用户改动 ——
        # 否则"什么都没改、点一下保存"会被判成改过（撤销按钮亮起来、
        # 状态栏说"已保存"）。实测 `test_save_without_change_does_not_enable_undo`
        # 当场红，正是这条。
        try:
            data['stage_order'] = b.merged_stage_order()
        except Exception as e:                # noqa: BLE001 - 顺序列坏不该拦住快照
            swallow('editor.snapshot.stage_order', e)
        data['ordered'] = copy.deepcopy(list(getattr(b, 'ordered', None) or []))
        return BeatSnapshot(idx=idx, data=data)

    def restore(self, snap: BeatSnapshot) -> Optional[Beat]:
        """把快照摊回它对应的那一拍（越界返回 None）。"""
        if snap is None or not self.has(snap.idx):
            return None
        b = self.beats[snap.idx]
        for name, value in snap.data.items():
            if name == 'ordered':
                b.ordered = copy.deepcopy(list(value or []))
            elif isinstance(value, list):
                # `code_blocks` 等列表字段：还原的是**副本**，不是快照里那份
                setattr(b, name, copy.deepcopy(value))
            else:
                setattr(b, name, value)
        # 还原出来的 stage 已经和结构化字段一致 → 标记"吸过"，
        # 免得下次 hydrate 把同一段原文再吸一遍（翻倍）。
        try:
            b._hydrated_stage = b.stage
        except Exception:                    # noqa: BLE001
            swallow('editor.restore')
        return b

    def save(self, idx: int, fields: dict, cards: list = None,
             advanced_open: bool = False, stage_loaded: str = None) -> dict:
        """
        **保存这张卡片的修改** —— 前端两个按钮里的那一个（另一个是撤销）。

        与 `apply` 的区别不是语义（内部就是调它），而是**问了"改没改"**：

          · 没改动 → 不压撤销栈（`changed=False`），撤销按钮不必亮起来；
          · 有改动 → 把**保存前**的那一拍压进撤销栈（单级：只记最近一次），
            所以「撤销修改」能把这张卡恢复成上一次保存时的样子。

        返回：
            {ok, beat, changed, line_id, index}
            · `ok=False` 表示没有载入任何一拍；
            · `changed=False` 表示内容一个字节都没变（含"翻页顺带提交"）。

        判"改没改"用的是**保存前后两份快照的指纹**（见
        `BeatSnapshot.fingerprint`），而不是 `Beat.beat_fingerprint` ——
        后者是持久化字段，编辑后还是旧值，拿它当判据会把"改了"判成"没变"。
        """
        if not self.has(idx):
            return {'ok': False, 'beat': None, 'changed': False,
                    'line_id': '', 'index': idx}
        before = self.snapshot(idx)
        beat = self.apply(idx, fields, cards=cards,
                          advanced_open=advanced_open,
                          stage_loaded=stage_loaded)
        changed = (self.snapshot(idx).fingerprint() != before.fingerprint())
        if changed:
            self._last_saved[idx] = before
        return {
            'ok': True,
            'beat': beat,
            'changed': changed,
            'line_id': getattr(beat, 'line_id', '') or f'第 {idx + 1} 拍',
            'index': idx,
        }
    def can_undo(self, idx: int = None) -> bool:
        """这一拍能不能撤销（没保存过、或没变过就是 False）。"""
        if idx is None:
            idx = self._last_idx
        return idx is not None and idx in self._last_saved

    def undo(self, idx: int = None) -> Optional[Beat]:
        """
        **撤销这张卡片的修改** —— 把它恢复成上一次保存时的样子。

        单级撤销（每个 Beat 只留最近一次保存前快照）：撤销一次即回到
        "上一次保存"；再撤就没了（返回 None 且不谎报成功）。
        前端据此决定按钮是否可用、以及要不要提示"没有可撤销的修改"。

        作用域刻意是**逐拍**而不是整章：用户按的是"这张卡片"的撤销，
        回退整章会把别的拍的改动一起抹掉（那才是灾难）。
        """
        if idx is None:
            idx = self._last_idx
        snap = self._last_saved.pop(idx, None) if idx is not None else None
        if snap is None:
            return None
        return self.restore(snap)

    def has_undo(self, idx: int) -> bool:
        """第 idx 拍有没有可撤销的修改（撤销按钮的 enabled 就用它）。"""
        return idx in self._last_saved

    def mark_loaded(self, idx: int) -> None:
        """
        告诉会话"前端现在显示的是第 idx 拍"。

        会话自己不跟踪用户在翻哪一页（那是前端的状态），但撤销需要一个
        **默认目标**：`undo()` 不带参数时要撤哪一拍。前端每次载入后调一下。
        """
        self._last_idx = idx if self.has(idx) else None
    @property
    def loaded_index(self):
        """最近一次 `mark_loaded` 的拍号（没载入过是 None）。"""
        return self._last_idx
