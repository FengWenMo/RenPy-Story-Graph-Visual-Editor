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
from storygraph.story.flow import FLOW_FIELD_KEYS
from storygraph.story.beat_build import (beats_from_parse_result,
                                         hydrate_from_dsl, stage_only_ops)
from storygraph.story.beat_render import make_symbols
from storygraph.story.chapter import ChapterDocument
from storygraph.story.chapter_tree import ChapterTreeError
from storygraph.story.beat_model import (Beat, KIND_MENU, MENU_NO_CAPTION,
                                         SNAPSHOT_FIELDS,
                                         compose_stage_extra)
from storygraph.story.spec_args import is_dynamic_column


__all__ = ['TableSessionMixin', 'BeatSnapshot', 'uniform_position_of',
           'beat_edits_print']


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


def beat_edits_print(beat, fallback=None) -> str:
    """
    一拍"**会不会改变产物**"的指纹（`_beat_edits_print` 与崩溃恢复共用）。

    比的是**渲染出来的行**（与导出同一条路），再补 `label` / `notes`
    两个不进单拍渲染、但确实要保存的字段。`line_id` / `seq` 刻意不比
    （见 `TableSessionMixin._beat_edits_print` 的说明）。

    `fallback`：渲染抛异常时的退路（会话那边传 `rendered_lines_of`，
    崩溃恢复那边可以不传 —— 恢复出来的拍还没进会话，没有退路可用）。
    """
    import hashlib
    try:
        from storygraph.story.beat_render import render_beat_to_rpy
        # **不 deepcopy**：`rendered_lines_of` 里那份拷贝是给"预览会删几行"
        # 用的（它怕 hydrate 改到用户手里那一拍）；这里只读不改，实测
        # 渲染一整个 1793 拍的章节，直接渲染 68ms / 拷贝再渲染 183ms。
        lines = list(render_beat_to_rpy(beat))
    except Exception as e:                      # noqa: BLE001 - 渲染不了就退回退路
        swallow('app.session_table.beat_edits_print', e)
        lines = list(fallback(beat) or ()) if callable(fallback) else []
    tail = [str(getattr(beat, 'label', '') or ''),
            str(getattr(beat, 'notes', '') or '')]
    blob = '\x1f'.join([*lines, '', *tail])
    return hashlib.md5(blob.encode('utf-8', 'replace')).hexdigest()[:12]


class TableSessionMixin:
    """
    表格 / 拍编辑那一半（见模块 docstring 的职责边界）。

    它**要求宿主提供**会话级基建：`beats` / `symbols` / `script_path` /
    `registry()` / `refresh_symbols()` / `has()` / `_renumber()` /
    `forget_undo()` / `_last_saved` / `_last_idx` / `_last_removed` ——
    见 `REQUIRES`（**实测**出来的，不是凭印象）。
    """

    #: 本 mixin 要求宿主（或其它 mixin）提供的成员。
    #: `screen_size` / `transform_index`（2026-10-31）：`stage_size_rows` 要算
    #: 这一拍的尺寸行（项目分辨率 + transform 表），两者都在 `ExportMixin` 里。
    REQUIRES = ('beats', 'symbols', 'script_path', 'registry',
                'refresh_symbols', 'has', '_renumber', 'forget_undo',
                '_last_saved', '_last_idx', '_last_removed', 'chapter',
                'set_node_variables', 'screen_size', 'transform_index')

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

    def block_head_line_ids(self) -> set:
        """
        哪些拍是**块头**（`menu` / `loop` / `camera` / 选项头）。

        接线选「第 N 拍」时要靠它挡住"插在这一拍后面" —— 块头的 `after`
        会掉进块体（`ChapterTree._slot_for` 的既定语义），线看起来接上了，
        实际只在那个分支/循环里执行。

        只返回**有真身拍**的块头：`if` 在树里没有独立拍（条件挂在第一张
        块体拍上），所以"if 前面那拍"仍然可以正常接 —— 那是顶层。
        """
        try:
            tree = self._chapter_doc().tree
        except Exception as e:                    # noqa: BLE001 - 查询失败按"没有块头"
            swallow('app.session_table.block_head_line_ids', e)
            return set()
        out = set()
        for node in tree.root.walk():
            header = getattr(node, 'header', None)
            if header is None or not getattr(node, 'children', None):
                continue
            line_id = str(getattr(header, 'line_id', '') or '')
            if line_id:
                out.add(line_id)
        return out

    # ------------------------------------------------------------------
    #  "还没导出的改动"（2026-10-22：关窗询问 / 状态栏共用一份判据）
    # ------------------------------------------------------------------

    def take_edits_baseline(self) -> None:
        """
        「内存里的拍 == 刚读进来 / 刚写出去的那一份」—— 记下这条基线。

        在 **载入之后**（`load`）与 **导出成功之后**（前端）调用。之后任何改动
        只是把 `_edits_dirty` 立起来；"到底改了几拍"到关窗时才现算
        （见 `unsaved_edits`），免得每次翻页都全章比一遍。
        """
        self._saved_prints = [self._beat_edits_print(i)
                              for i in range(len(self.beats))]
        self._edits_dirty = False

    def mark_edits_dirty(self) -> None:
        """标记"拍被改过、还没导出"（所有会改拍的路径都要调它）。"""
        self._edits_dirty = True

    def refresh_edits_flag(self) -> bool:
        """
        按基线**重算**"脏不脏"，返回结果。

        撤销那条路专用：用户把改动撤回去之后，内存里可能已经与磁盘一致了
        （例如刚插了一张卡又撤销插卡）—— 那时状态栏不该再喊「● 未导出」。
        会全章算一遍指纹，所以只在"撤销 / 恢复"这种点击频率上调用。
        """
        try:
            self._edits_dirty = bool(self.unsaved_edits().get('dirty'))
        except Exception as e:                  # noqa: BLE001 - 算不出来就保持原样
            swallow('app.session_table.refresh_edits_flag', e)
        return bool(getattr(self, '_edits_dirty', False))

    def has_unsaved_edits(self) -> bool:
        """内存里有没有还没导出的改动（**便宜**：一个布尔，翻页也能问）。"""
        return bool(getattr(self, '_edits_dirty', False))

    def _beat_edits_print(self, idx: int) -> str:
        """
        一拍"**会不会改变产物**"的指纹（实现见模块级 `beat_edits_print`）。

        为什么不用字段快照：`apply()`（翻页时的"顺带提交"）会把 `At` 这类
        **投影列**规范化 —— 内存里 `Beat.at` 从空变成 `i11`，而产物一个字都
        不变。拿字段快照比，用户只是翻了几页，关窗时就会凭空问"你改了 3 拍"。
        该问的是"导出去会不一样吗"，所以这里比**渲染出来的行**
        （与导出同一条路，见 `rendered_lines_of`），再补两个不进单拍渲染、
        但确实要保存的字段：`label`（换了 label = 这一拍要挪到别的章节）与
        `notes`（只在表里，渲染不出**来**，丢了也是丢用户写的东西）。

        `line_id` / `seq` 是身份编号，**刻意不比**：插一张卡会让后面每一拍
        整体后移，比它们就会把"插了 1 拍"说成"改了 200 拍"。

        2026-10-23：指纹算法搬成模块级函数（`beat_edits_print`）——
        **崩溃恢复要拿它比"快照里的拍"与"磁盘上的拍"**，而那份拍还没进
        会话（不属于任何 idx）。搬出去而不是在界面里抄一遍：
        "两处各写一份指纹"正是这条判据最怕的事（抄漏一个字段就永远
        判成"没变"，恢复出来的东西被当成已保存）。
        """
        return beat_edits_print(self.beats[idx], self.rendered_lines_of)

    def unsaved_edits(self) -> dict:
        """
        「改了哪几拍、还没导出」：`{dirty, changed, added, removed, total}`。

        `changed` 是**内容真的变了**的拍数：用 `difflib` 对齐两份指纹序列，
        所以"中间插了一张卡"只算 1 拍新增，不会把后面每一拍都报成改过。

        ⚠ **这个调用会把全章算一遍指纹**（1800 拍约 80ms）—— 只在关窗 /
        弹"要不要保存"时用。滚动、翻页、状态栏那些地方用
        `has_unsaved_edits()`。
        """
        cur = [self._beat_edits_print(i) for i in range(len(self.beats))]
        base = getattr(self, '_saved_prints', None)
        total = len(cur)
        if base is None:
            # 没有基线（测试直接塞 `beats`、会话没走 `load`）：只能信那个布尔。
            return {'dirty': self.has_unsaved_edits(), 'changed': 0,
                    'added': 0, 'removed': 0, 'total': total}
        if list(base) == cur:
            return {'dirty': False, 'changed': 0, 'added': 0, 'removed': 0,
                    'total': total}
        import difflib
        changed = added = removed = 0
        matcher = difflib.SequenceMatcher(None, list(base), cur, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                continue
            if tag == 'replace':
                changed += min(i2 - i1, j2 - j1)
                added += max(0, (j2 - j1) - (i2 - i1))
                removed += max(0, (i2 - i1) - (j2 - j1))
            elif tag == 'insert':
                added += j2 - j1
            elif tag == 'delete':
                removed += i2 - i1
        return {'dirty': (changed + added + removed) > 0, 'changed': changed,
                'added': added, 'removed': removed, 'total': total}

    def load(self, path, label=None) -> int:
        """
        载入一个 .rpy，解析成 Beat 列表。返回拍数。

        与旧路径**同一条语义**：ParseResult → beats → 补 Stage → hydrate DSL。

        `label` 只决定界面**先落到哪一拍**（同一个 .rpy 里可以有多个 label）。
        它不改变"载入整个文件"这件事 —— 导出、保存、变量分析仍然面对整个文件。
        找不到时由前端明确提示，并退回第 1 拍（`index_of_label()` 返回 -1）。

        **不是 UTF-8 的文件直接报错**（2026-10-25，审计 P1-2）：从前解析器
        解码失败 `return []` → 载入"成功"、拍数 0 → 界面一张空列表，
        用户以为这一章是空的（探针实测：GBK 文件 0 拍、无任何提示）。
        Ren'Py 只认 UTF-8，这件事必须说出口 —— 抛出去的异常前端会弹
        「载入失败」并显示这句话（`window_card.load_script`）。
        """
        from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                     RenPyParser)
        path = Path(path)
        try:
            path.read_bytes().decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            self.beats = []
            self.script_path = path
            raise ValueError(
                f'这个文件不是 UTF-8（Ren\'Py 只认 UTF-8）：{path.name}'
                f'（第 {exc.start} 字节起）。'
                f'请用编辑器另存为「UTF-8」（不要 ANSI / GBK）再载入。'
            ) from exc
        except OSError as e:
            # 读不了（权限 / 刚被删）→ 记一笔，交给下面按老路报
            swallow('app.session_table.load.decode_check', e)
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
        # 换了文件，上一章那份入口状态就不再属于这次载入（跨章继承按文件算）。
        self.set_stage_entry(None)
        # 换了文件，旧的撤销记录就没有意义了（拍号对不上别的章节）。
        self.forget_undo()
        self._struct_undo = []          # 结构撤销栈同理：索引全变了
        # 刚读进来的这一份 = 干净状态（关窗 / 状态栏靠这条基线判"改过没有"）
        self.take_edits_baseline()
        return len(self.beats)

    def apply_node_variable_edits(self, graph, nid, variables,
                                  insert_beat=None, anchors=None) -> dict:
        """
        把"变量工作台 / 旧变量对话框"的改动**落进当前章节的拍**。

        为什么需要这一步（2026-10-22，P1-2 的真正缺口）：
        `set_node_variables()` 只改**图上节点**，而导出读的是 `self.beats` ——
        于是用户改了变量、导出后 `.rpy` 一个字都没变。变量的真源必须回到
        拍上，图上的节点只是索引/分析用的投影。

        做法：按每条语句的 `source` 行号找到那一拍，替换/删除 `Beat.code`
        里的同一行；新增的操作追加到"用户选中那条所在的拍"，找不到就放到
        最后一拍（被调 label 的入口处）。找不到拍（手工节点 / 没有行号）时
        **不猜**，在返回值里报 `unplaced`。

        `anchors`（2026-10-22，块内插入）：`{新操作下标: 拍下标}`。工作台可能
        一次往**不同的块**里各插一条（`if` 里一条、`for` 里一条），只传一个
        `insert_beat` 时后者会被前者覆盖 —— 全落到同一拍里。没有 anchor 的
        新操作退回 `insert_beat`，两者都没有就走"本章最后一拍"。
        `Beat.code` 存的是**块体本身**（缩进由块头在渲染时补），所以把新行
        追加到那条赋值所在的拍，就自动落在同一个 `if` / `for` 里面。
        """
        node = (getattr(graph, 'nodes', {}) or {}).get(nid)
        if node is None:
            return {'ok': False, 'error': '这个模块不在了', 'applied': 0,
                    'unplaced': 0, 'removed': 0}
        path = str(getattr(node, 'file_path', '') or '')
        label = str(getattr(node, 'label_name', '') or '')
        if not path or not Path(path).exists():
            return {'ok': False, 'error': '这个模块没有可写的源文件',
                    'applied': 0, 'unplaced': 0, 'removed': 0}
        # 只写"当前载入的这一章"的拍；别的章节先载入（不会切界面页）。
        same = (self.script_path is not None
                and Path(str(self.script_path)).resolve() == Path(path).resolve())
        if not same:
            self.load(path, label=label)
        from storygraph.core.statement import operation_source
        from storygraph.story import story_ops
        old_stmts = story_ops.narrative_statements(node)
        old_ops = [operation_source(s) for s in old_stmts]
        new_ops = [str(v) for v in (variables or ()) if str(v).strip()]
        beats = [b for b in (self.beats or ())
                 if str(getattr(b, 'label', '') or '') == label]
        if not beats:
            return {'ok': False, 'error': f'文件里找不到 label {label} 的拍',
                    'applied': 0, 'unplaced': 0, 'removed': 0}

        def beat_for_line(line):
            for b in beats:
                first = int(getattr(b, 'src_first', 0) or 0)
                last = int(getattr(b, 'src_last', 0) or 0)
                if first and last and first <= line <= last:
                    return b
            return None

        def line_of(stmt):
            try:
                return int((getattr(stmt, 'source', None) or ('', 0))[1] or 0)
            except (TypeError, ValueError, IndexError):
                return 0

        #: `拍号 -> 改动前快照`：工作台的改动**同时**落在图节点与拍上，
        #: 而图级撤销（Ctrl+Z）只认得节点。没有这份快照，撤销之后
        #: "工作台说这条没了、导出里却还在"（2026-10-22 实测）。
        before_snaps: dict = {}

        def remember(beat):
            """动这一拍之前先把它的原样记下来（同一拍只记一次 = 最早那份）。"""
            # 按**身份**找下标：`Beat` 是 dataclass（`__eq__` 比字段），
            # `list.index()` 会把内容相同的两拍认成同一拍，快照就记错了对象。
            idx = next((i for i, b in enumerate(self.beats) if b is beat), None)
            if idx is None or idx in before_snaps:
                return
            try:
                snap = self.snapshot(idx)
            except Exception as e:              # noqa: BLE001 - 记不上快照不拦保存
                swallow('app.session_table.apply_node_variable_edits.snap', e)
                return
            if snap is not None:
                before_snaps[idx] = snap

        def edit_line(beat, old, new):
            """在 `beat.code` / `beat.raw` 里替换（或删掉）一行。"""
            for attr in ('code', 'raw'):
                text = str(getattr(beat, attr, '') or '')
                if not text:
                    continue
                lines = text.split('\n')
                for i, line in enumerate(lines):
                    if line.strip() == old.strip():
                        if new:
                            lines[i] = new
                        else:
                            del lines[i]
                        setattr(beat, attr, '\n'.join(lines))
                        return True
            return False

        def anchor_of(op_index):
            """
            这一条新操作该落在**哪一拍、块体的哪一头**。

            返回 `(beat, where)`：`where` 是 `'end'`（块体末尾，默认）或
            `'head'`（块体最前面 = "块首插入"，2026-10-22）。两种落点共用
            下面那段"循环头往后挪一拍"的判据 —— 块首就是循环体的第一行。

            指到"循环头"那一拍（`for …:` 自己占一拍、块体在下一拍）时
            往后挪一拍 —— 头拍的 `code` 是空的，写进去会落在循环**外面**。
            """
            want = None
            where = 'end'
            if isinstance(anchors, dict):
                want = anchors.get(op_index)
            elif anchors:
                try:
                    want = anchors[op_index]
                except (IndexError, TypeError):
                    want = None
            if want is None:
                want = insert_beat
            if isinstance(want, (tuple, list)) and want:
                # `(拍号, 'head')`：工作台选了"块首"时就这么递过来。
                # 只给一个整数的是老契约（= 块体末尾），必须继续认。
                where = str(want[1] if len(want) > 1 else 'end') or 'end'
                want = want[0]
            try:
                want = int(want)
            except (TypeError, ValueError):
                return None, 'end'
            if not 0 <= want < len(self.beats):
                return None, 'end'
            cand = self.beats[want]
            if str(getattr(cand, 'label', '') or '') != label:
                return None, 'end'
            if (not str(getattr(cand, 'code', '') or '').strip()
                    and str(getattr(cand, 'loop_header', '') or '').strip()
                    and want + 1 < len(self.beats)):
                nxt = self.beats[want + 1]
                if str(getattr(nxt, 'label', '') or '') == label:
                    cand = nxt
            return cand, ('head' if where == 'head' else 'end')

        applied = unplaced = removed = 0
        inserted: list = []
        #: `id(beat) -> (beat, [新行…])`：块首那几条**收齐了再插**
        #: （边收边插会让后加的跑到前面：工作台里"先加 A 再加 B"，
        #: 产物里变成 B 在 A 上面）。
        heads: dict = {}
        for i, new in enumerate(new_ops):
            if i < len(old_ops):
                old = old_ops[i]
                line = line_of(old_stmts[i])
                beat = beat_for_line(line) if line else None
                if beat is None:
                    unplaced += 1
                    continue
                if new == old:
                    applied += 1
                    continue
                remember(beat)
                if edit_line(beat, old, new):
                    applied += 1
                else:
                    unplaced += 1
            else:
                # 新增：优先放进用户选中那条所在的拍（同一个块体 → 同一个
                # `if` / `for` 里面）；没选/对不上就放到本章最后一拍。
                beat, where = anchor_of(i)
                if beat is None:
                    beat, where = beats[-1], 'end'
                if where == 'head':
                    entry = heads.get(id(beat))
                    if entry is None:
                        heads[id(beat)] = (beat, [new])
                    else:
                        entry[1].append(new)
                else:
                    remember(beat)
                    beat.code = ((str(beat.code or '') + '\n')
                                 if str(beat.code or '').strip() else '') + new
                try:
                    inserted.append(next(
                        n for n, b in enumerate(self.beats) if b is beat))
                except StopIteration:
                    inserted.append(-1)
                applied += 1
        for beat, rows in heads.values():
            remember(beat)
            self._prepend_beat_code(beat, rows)
        # 被删掉的多余旧操作（从后往前删，避免同一拍多行时下标跑掉）
        for i in range(len(new_ops), len(old_ops)):
            line = line_of(old_stmts[i])
            beat = beat_for_line(line) if line else None
            if beat is not None:
                remember(beat)
            if beat is not None and edit_line(beat, old_ops[i], ''):
                removed += 1
                applied += 1
            else:
                unplaced += 1
        # 图上的节点也跟着更新（两处保持一致）
        merged = self.set_node_variables(graph, nid, new_ops)
        if applied or removed:
            # 拍的 `code` / `raw` 被改过 → 内存与磁盘不再一致（关窗要问）。
            self.mark_edits_dirty()
        return {'ok': True, 'error': '', 'applied': applied,
                'unplaced': unplaced, 'removed': removed,
                'inserted': inserted,
                # 改动落在哪几拍、以及**改之前**的原样：图级撤销（Ctrl+Z）
                # 要连拍一起退回去，见 `graph.commands.UpdateNodeOperationsCommand`。
                'beats': sorted(before_snaps),
                'beat_snapshots': before_snaps,
                'variables': merged.get('variables'),
                'kept_tech': merged.get('kept_tech')}

    def _prepend_beat_code(self, beat, lines) -> None:
        """
        把几行代码插到**这一拍拍内容的最前面**（变量工作台的"块首插入"）。

        为什么不是一句 `beat.code = 新行 + beat.code`：

          · `ordered` 是 raw / code 在源码里的**交错顺序**。不把新行记进去，
            `_ordered_code_lines` 会判"code 池里多出来一行"→ 整份退回老顺序
            （raw 全部在前、code 全部在后），同一拍里原有的 `$` 语句会被挪到
            `show screen` **之后** —— 只是插一条赋值，演出顺序却变了；
          · `code_blocks` 是 `python:` 块的行区间（1-based，按"raw 行在前、
            code 行在后"编号）。前面插了 n 行，所有区间整体后移 n ——
            不挪的话块标记会落在别的行上（产物语法错）。

        `Beat.code` 存的是**块体本身**，缩进由块头在渲染时补 —— 所以"插到
        `code` 最前面"就等于"块体的第一行"，不需要自己算空格。
        """
        rows = [str(ln) for ln in (lines or ()) if str(ln or '').strip()]
        if not rows:
            return
        old = str(getattr(beat, 'code', '') or '')
        beat.code = '\n'.join(rows) + (('\n' + old) if old.strip() else '')
        ordered = getattr(beat, 'ordered', None)
        if ordered:
            for row in reversed(rows):
                ordered.insert(0, ('code', row))
        blocks = getattr(beat, 'code_blocks', None)
        if blocks:
            beat.code_blocks = [(int(a) + len(rows), int(b) + len(rows))
                                for a, b in blocks]

    # ==================================================================
    #  `python:` 块 → `$` 行（2026-10-22：搬出来，而不是适应它）
    # ==================================================================

    def python_lift_report(self, graph, nid) -> dict:
        """
        一个 label 的 `python:` 块清单：每块能搬哪些行、哪些不能、为什么。

        用户 2026-10-22：

        > 真机那 26 条里 21 条是脚本层 `$` 就写得出来的赋值。它们待在 python
        > 块里不只是"工具管不着"，而是有害的 —— 存档粒度是整块。
        > ……正确的方向不是"工具去适应 python 块"，是"工具帮用户把语句从
        > python 块里搬出来"。

        判定全在 `story.python_lift.analyze_block()`（纯函数），这里只负责
        把"拍 / 块 / 图节点语句"三者对上号：

            {'ok', 'error', 'label', 'file',
             'blocks': [{id, beat, beat_no, start, end, stmt_index,
                         header_line, text, plan}],
             'stats': {'blocks', 'liftable', 'safe_lines', 'unsafe_lines'},
             'warning'}

        **只读**：报告不碰会话状态（可能是在看一个还没载入的章节）。
        """
        from storygraph.core.statement import operation_source, strip_marker
        from storygraph.story.python_lift import analyze_block

        node = (getattr(graph, 'nodes', {}) or {}).get(nid)
        blank = {'ok': False, 'error': '这个模块不在了', 'label': '',
                 'file': '', 'blocks': [],
                 'stats': {'blocks': 0, 'liftable': 0, 'safe_lines': 0,
                           'unsafe_lines': 0}, 'warning': ''}
        if node is None:
            return blank
        label = str(getattr(node, 'label_name', '') or '')
        path = str(getattr(node, 'file_path', '') or '')
        if not path or not Path(path).exists():
            return dict(blank, error='这个模块没有可读的源文件',
                        label=label, file=path)
        beats, warning = self._python_lift_beats(path)
        if not beats:
            return dict(blank, error=f'解析不出 {Path(path).name} 的拍',
                        label=label, file=path, warning=warning)
        stmts = list(getattr(node, 'statements', None) or [])
        used: set = set()
        blocks: list = []
        for bi, beat in enumerate(beats):
            if str(getattr(beat, 'label', '') or '') != label:
                continue
            lines = str(getattr(beat, 'code', '') or '').split('\n')
            for start, end in (getattr(beat, 'code_blocks', None) or []):
                try:
                    start, end = int(start), int(end)
                except (TypeError, ValueError):
                    continue
                if start < 1 or end < start or end > len(lines):
                    continue
                text = '\n'.join(lines[start - 1:end])
                stmt_index = self._match_block_statement(
                    stmts, used, text, operation_source, strip_marker)
                blocks.append({
                    'id': f'{bi}:{start}-{end}',
                    'beat': bi, 'beat_no': bi + 1,
                    'start': start, 'end': end,
                    'stmt_index': stmt_index,
                    'header_line': self._statement_line(stmts, stmt_index),
                    'text': text,
                    'plan': analyze_block(lines[start - 1:end]),
                })
        liftable = sum(1 for b in blocks if b['plan'].get('ok'))
        safe = sum(len(b['plan'].get('lift') or ()) for b in blocks)
        unsafe = sum(len(b['plan'].get('skipped') or ()) for b in blocks)
        return {'ok': True, 'error': '', 'label': label, 'file': path,
                'blocks': blocks,
                'stats': {'blocks': len(blocks), 'liftable': liftable,
                          'safe_lines': safe, 'unsafe_lines': unsafe},
                'warning': warning}

    def _python_lift_beats(self, path):
        """
        把一份 `.rpy` 解析成拍（**不改会话状态** —— 报告必须是只读的）。

        为什么不直接用 `self.beats`：报告可能针对"还没载入的章节"（用户在图上
        点了另一个模块）；为了看一眼清单就把当前编辑的拍换掉，正在填的表单
        会被覆盖。与 `variable_workbench` 走同一条局部解析路。
        """
        try:
            from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                         RenPyParser)
            from storygraph.story.beat_build import beats_from_parse_result
            pr = ParseResultBuilder().build(RenPyParser().parse(str(path)))
            return beats_from_parse_result(pr), ''
        except Exception as e:                  # noqa: BLE001 - 报告失败不拦人
            swallow('app.session_table.python_lift.parse', e)
            return [], f'解析失败：{e}'

    @staticmethod
    def _match_block_statement(stmts, used, text, operation_source,
                               strip_marker):
        """块的文本 → 它在 `node.statements` 里的下标（对不上返回 -1）。"""
        want = str(text or '').strip('\n')
        for i, st in enumerate(stmts):
            if i in used:
                continue
            got = strip_marker(operation_source(st)).strip('\n')
            if got == want:
                used.add(i)
                return i
        return -1

    @staticmethod
    def _statement_line(stmts, index) -> int:
        """块语句的 `source` 行号（`python:` 那一行）；取不到就是 0。"""
        try:
            st = stmts[int(index)]
            return int((getattr(st, 'source', None) or ('', 0))[1] or 0)
        except (TypeError, ValueError, IndexError, KeyError):
            # 只可能"下标对不上 / source 不是 (文件, 行号)"这两种形状问题；
            # 行号只用于显示，取不到就写 0，不吞别的异常。
            return 0

    def apply_python_lift(self, graph, nid, picks) -> dict:
        """
        把选中的行搬出 `python:` 块（**原地**：行文本不动，只改块标记与语句）。

        `picks`：`[{'beat': 拍下标, 'start': 块首行, 'end': 块尾行,
                    'lift': [块内行下标, …]}]`（0-based，相对块体）。

        返回 `{ok, error, lifted, blocks, beats, beat_snapshots,
        old_statements, new_statements}`；`beat_snapshots` 是**动手之前**的
        逐拍快照（图级撤销要连拍一起退）。

        ⚠ 三处必须一起改，少一处就是"界面说搬了、导出里还在"：

          1. `Beat.code_blocks`（导出真相 —— 渲染层按标记出 `python:` / `$`）；
          2. `Node.statements`（工作台 / 分析 / 摘要的真相：一条多行语句
             拆成"每条 `$` 一句"）；
          3. 撤销历史（`LiftPythonStatementsCommand` 用整份语句表 + 拍快照）。

        `Beat.code` 本身**一个字符都不改**：行数没变，`ordered` / `op_order` /
        `src_first..src_last` 也就都不用动（这是这条路的立足点）。
        """
        node = (getattr(graph, 'nodes', {}) or {}).get(nid)
        if node is None:
            return {'ok': False, 'error': '这个模块不在了', 'lifted': 0}
        label = str(getattr(node, 'label_name', '') or '')
        path = str(getattr(node, 'file_path', '') or '')
        if not path or not Path(path).exists():
            return {'ok': False, 'error': '这个模块没有可写的源文件',
                    'lifted': 0}
        same = (self.script_path is not None
                and Path(str(self.script_path)).resolve() == Path(path).resolve())
        if not same:
            try:
                self.load(path, label=label)
            except Exception as e:              # noqa: BLE001
                return {'ok': False, 'error': f'载入失败：{e}', 'lifted': 0}

        wanted, error = self._lift_targets(picks)
        if error:
            return {'ok': False, 'error': error, 'lifted': 0}

        stmts = list(getattr(node, 'statements', None) or [])
        replace: dict = {}           # 语句下标 → 新语句序列
        used_idx: set = set()        # 已经配过块的语句（两个块文本相同时别抢同一条）
        plan_beats: dict = {}        # 拍下标 → [(start, end, keep_ranges)]
        lifted = 0
        for (bi, start, end), offsets in sorted(wanted.items()):
            plan = self._plan_lift_block(bi, start, end, offsets, label,
                                         stmts, used_idx, path)
            if not plan.get('ok'):
                return {'ok': False, 'error': plan.get('error') or '不能搬',
                        'lifted': 0}
            used_idx.add(plan['si'])
            replace[plan['si']] = plan['new_stmts']
            plan_beats.setdefault(bi, []).append((start, end, plan['keep']))
            lifted += len(plan['lift_rows'])

        if not replace:
            return {'ok': False, 'error': '没有可落地的行', 'lifted': 0}

        before_snaps: dict = {}
        for bi in sorted(plan_beats):
            try:
                snap = self.snapshot(bi)
            except Exception as e:              # noqa: BLE001 - 记不上快照不拦保存
                swallow('app.session_table.python_lift.snapshot', e)
                snap = None
            if snap is not None:
                before_snaps[bi] = snap
        for bi, items in plan_beats.items():
            beat = self.beats[bi]
            blocks = [(int(a), int(b))
                      for a, b in (getattr(beat, 'code_blocks', None) or [])]
            for start, end, keep in items:
                blocks = [(a, b) for a, b in blocks if (a, b) != (start, end)]
                blocks.extend(keep)
            beat.code_blocks = sorted(blocks)

        merged: list = []
        for i, st in enumerate(stmts):
            if i in replace:
                merged.extend(replace[i])
            else:
                merged.append(st)
        node.statements = merged
        if lifted:
            self.mark_edits_dirty()
        return {'ok': True, 'error': '', 'lifted': lifted,
                'blocks': len(replace), 'beats': sorted(before_snaps),
                'beat_snapshots': before_snaps,
                'old_statements': stmts, 'new_statements': merged}

    @staticmethod
    def _lift_targets(picks):
        """
        `picks` → `({(拍, 块首行, 块尾行): {块内行偏移, …}}, 错误)`。

        同一个块被选了两次（界面按理不会，但接口可能）→ **合并**而不是各算一遍：
        各算一遍会让第二条覆盖第一条，最后只搬了一半。
        """
        wanted: dict = {}
        for pick in (picks or ()):
            try:
                bi = int(pick.get('beat'))
                start = int(pick.get('start'))
                end = int(pick.get('end'))
            except (AttributeError, TypeError, ValueError):
                continue
            rows = {int(x) for x in (pick.get('lift') or ())
                    if str(x).strip() != ''}
            if rows:
                wanted.setdefault((bi, start, end), set()).update(rows)
        if not wanted:
            return {}, '没有选中要搬的行'
        return wanted, ''

    def _plan_lift_block(self, bi, start, end, offsets, label, stmts,
                         used_idx, path) -> dict:
        """
        **一个块**的搬迁计划（校验 + 留下的连续段 + 语句拆分）。

        返回 `{ok, error, si, keep, lift_rows, new_stmts}`；`ok=False` 时
        `error` 说的是"为什么这一块不能搬"（人话，会直接显示在对话框里）。
        """
        from storygraph.story.python_lift import analyze_block

        if not 0 <= bi < len(self.beats):
            return {'ok': False,
                    'error': f'第 {bi + 1} 拍不在了（文件改过了？）'}
        beat = self.beats[bi]
        if str(getattr(beat, 'label', '') or '') != label:
            return {'ok': False,
                    'error': f'第 {bi + 1} 拍不属于 {label}（文件改过了？）'}
        lines = str(getattr(beat, 'code', '') or '').split('\n')
        if not (1 <= start <= end <= len(lines)):
            return {'ok': False, 'error': '块的行区间越界（文件改过了？）'}
        lift_rows = {start + o for o in offsets if 0 <= o <= (end - start)}
        if not lift_rows:
            return {'ok': False, 'error': '这个块没有可搬的行'}
        # 权威校验：只接受"计划里说可搬"的那些行。界面本来只让勾这些，
        # 但接口也可能被脚本 / 别的入口调用 —— 搬一条嵌套语句出来，
        # 它上面的 `if` 就成了没有块体的 `if`（语法错）。
        allowed = set(analyze_block(lines[start - 1:end]).get('lift') or ())
        bad = sorted(set(offsets) - allowed)
        if bad:
            return {'ok': False,
                    'error': f'第 {start}-{end} 行这个块里，第 {bad[0] + 1} 行'
                             f'不能搬（嵌套 / 注释 / 多行 / 不是单行赋值）'}
        if (start, end) not in [(int(a), int(b)) for a, b in
                                (getattr(beat, 'code_blocks', None) or [])]:
            return {'ok': False, 'error': '这个块已经被改过了（先刷新一下）'}
        keep, error = self._keep_ranges(lines, start, end, lift_rows)
        if error:
            return {'ok': False, 'error': error}
        from storygraph.core.statement import operation_source, strip_marker
        text = '\n'.join(lines[start - 1:end])
        si = self._match_block_statement(stmts, used_idx, text,
                                         operation_source, strip_marker)
        if si < 0:
            return {'ok': False,
                    'error': '块的文本对不上图节点（先点「🔁 刷新已导入'
                             '章节」再试）'}
        cond = str(getattr(stmts[si], 'condition', '') or '')
        head = self._statement_line(stmts, si)
        return {'ok': True, 'error': '', 'si': si, 'keep': keep,
                'lift_rows': lift_rows,
                'new_stmts': self._split_block_statements(
                    lines, start, end, lift_rows, cond, path, head)}

    @staticmethod
    def _keep_ranges(lines, start, end, lift_rows):
        """
        "块里还留下哪些连续段"（`[(首行, 尾行), …]`）；出错返回 `(keep, 原因)`。

        为什么"只剩注释的段"要报错而不是丢掉：`python:` 里只有注释是**语法错**，
        这种行必须退回去陪注释（见 `story/python_lift` 的同名规则）。
        只有空行的段可以丢（块外渲染成空行，合法）。
        """
        runs: list = []
        for r in (r for r in range(start, end + 1) if r not in lift_rows):
            if runs and r == runs[-1][-1] + 1:
                runs[-1].append(r)
            else:
                runs.append([r])
        keep: list = []
        for run in runs:
            has_stmt = any(TableSessionMixin._row_is_code(lines[r - 1])
                           for r in run)
            if not has_stmt:
                if any(str(lines[r - 1] or '').lstrip().startswith('#')
                       for r in run):
                    return [], ('有一行注释会只剩它自己待在块外 —— '
                                '把同一块里靠近它的那条一起留下')
                continue          # 只有空行：块外渲染成空行，合法
            keep.append((run[0], run[-1]))
        return keep, ''

    def _split_block_statements(self, lines, block_start, block_end,
                                lift_rows, cond, path, head) -> list:
        """
        块体内的行 → 语句序列（搬出去的行各自成一条，留下的是连续段）。

        顺序**原样保留**（块体前半 → `$` 行 → 块体后半），所以不需要依赖分析
        —— 见 `story/python_lift` 与契约 §26。
        """
        new_stmts: list = []
        cur: list = []
        for r in range(block_start, block_end + 1):
            if r in lift_rows:
                if cur:
                    new_stmts.append(self._lift_statement(
                        lines, cur[0], cur, cond, path, head, block_start))
                    cur = []
                new_stmts.append(self._lift_statement(
                    lines, r, [r], cond, path, head, block_start))
            else:
                cur.append(r)
        if cur:
            new_stmts.append(self._lift_statement(
                lines, cur[0], cur, cond, path, head, block_start))
        return new_stmts

    @staticmethod
    def _lift_statement(lines, row, rows, cond, path, head, block_start):
        """
        块里的若干行 → 一条 `Statement`（搬出去的那条就是一行）。

        `source` 按"`python:` 头 + 块内偏移"推算（只用于落到第几拍的对位）。
        """
        from storygraph.core.statement import Statement

        body = '\n'.join(str(lines[r - 1]) for r in rows)
        return Statement.make_python(
            body.strip() if len(rows) == 1 else body,
            condition=cond,
            source=(path, (head + (int(row) - int(block_start)) + 1)
                    if head else 0),
            dollar_space=True)

    @staticmethod
    def _row_is_code(text: str) -> bool:
        """这一行是"语句"吗（不是空行 / 注释）？决定它能不能撑起一个块。"""
        s = str(text or '').strip()
        return bool(s) and not s.startswith('#')

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

    def source_state(self, draft_text=None) -> str:
        """
        磁盘上这一章相对"载入时那一份"的**三态**（`story/write_gate.py`）。

        · `draft_text` 给了（真要写的那一刻）→ 完整三态：`ADOPT`（盘上已经是
          这份草稿）/ `REBASE`（内容没变，只是被 touch）/ `CONFLICT`（真分叉）；
        · 不给 → 只能回答后两态（没有草稿就没有 `ADOPT`）。

        ⚠ 冲突**只在写入那一刻**判定（§6.3b"不打断正在编辑的人"）：
        这个方法不被轮询，只在导出/落盘前问一次。
        """
        from storygraph.story import write_gate as W

        fp = getattr(self, '_source_fingerprint', None)
        if fp is None or not self.script_path:
            return W.CONFLICT
        try:
            disk = Path(self.script_path).read_bytes()
        except OSError:
            return W.CONFLICT
        if draft_text is None:
            return W.REBASE if W.sha1_16(disk) == fp[2] else W.CONFLICT
        return W.classify(disk, fp[2], draft_text).state

    def source_unchanged(self) -> bool:
        """
        磁盘上这一章还是"载入时那一份"吗？

        2026-10-31（三态）：判据从 `(大小, mtime, sha1)` 改成**只比内容**。
        `touch` / `git checkout` / 备份工具重写会改 mtime 而内容一个字没变，
        从前会因此 `False` → 退回保守的内容对位（平白丢掉可信行号）。
        内容真变了才是 `CONFLICT`，走保守判据（见 `patcher._range_looks_like`）。
        """
        from storygraph.story import write_gate as W

        return self.source_state() != W.CONFLICT

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
        from storygraph.story.flow import (is_valid_label, parse_flow_line,
                                           render_flow_line)
        flow_type = str(flow_type or 'call').strip()
        target = str(target or '').strip()
        if flow_type not in ('call', 'jump'):
            return {'ok': False, 'error': '控制流类型只能是 call / jump',
                    'index': -1}
        if not is_valid_label(target):
            return {'ok': False, 'error': '目标 label 不合法', 'index': -1}
        line = render_flow_line(flow_type, target, args, from_clause)
        # **渲染 → 再解析**当校验：`render_flow_line` 只拼字符串，
        # `call side(a` 这种没配平的写法会让整份脚本解析不了，而工具这边
        # 看不出异常 —— 那种"导出成功、游戏起不来"的错最难查（2026-10-23）。
        if parse_flow_line(line) is None:
            return {'ok': False, 'index': -1,
                    'error': '参数或 from 的写法不合法（括号要配平、'
                             'from 只能是标识符）'}
        pos = len(self.beats) if idx is None else int(idx)
        pos = max(0, min(pos, len(self.beats) - 1))
        at = pos + 1 if after else pos
        from storygraph.story.beat_model import Beat
        template = self.beats[pos]
        beat = Beat(seq=0, label=str(getattr(template, 'label', '') or ''),
                    kind='narration',
                    raw=line)
        doc = self._chapter_doc()
        at = doc.insert_beat(beat, at, before=not after)
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

    def clear_preview(self, idx: int, mode: str = 'text') -> dict:
        """
        清空这张卡**会让产物少哪几行、留哪几行** —— 不改任何状态。

        与"删整拍"的预览（`delete_preview`）是同一个思路：**先把话说清楚**。
        差别只在作用域 —— 这里清的是通道（文字 / 舞台），代码块留下。
        """
        from storygraph.app.beat_clear import plan_clear

        if not self.has(idx):
            return {'ok': False, 'blocking': '这一拍不存在',
                    'drop_lines': [], 'keep_lines': []}
        return plan_clear(self.beats[idx], mode).to_dict()

    def clear_beat(self, idx: int, mode: str = 'text') -> dict:
        """
        把第 idx 张卡片**清空成"只剩某些通道"**（用户 2026-10-25 的分级删除）。

        两档（见 `app.beat_clear`）：
          · `text`       —— 只清文字，**代码块与舞台都留**；
          · `text_stage` —— 连舞台/音频一起去掉，**代码块留**。

        整拍删除仍然走 `delete_beat`（那是第三档，也是现在的行为）。
        **只改内存**：落盘仍要「⤓ 导出」，撤销走结构撤销栈（`undo_structural`）。
        """
        from storygraph.app.beat_clear import (apply_clear, plan_clear,
                                               snapshot_fields)

        if not self.has(idx):
            return {'ok': False, 'error': '这一拍不存在', 'index': -1}
        beat = self.beats[idx]
        plan = plan_clear(beat, mode)
        if not plan.ok:
            return {'ok': False, 'error': plan.blocking or '这一拍清不了',
                    'index': idx, 'mode': plan.mode, 'blocked': 'clear',
                    'kept_lines': [], 'drop_lines': []}
        saved = snapshot_fields(beat, plan.fields)
        res = apply_clear(beat, mode)
        self._push_struct_undo(('clear', idx, saved, mode))
        # 内容变了 → 内容撤销栈（按拍号记的那些）对不上，清掉（与删/插同一规矩）
        self.forget_undo()
        return {'ok': True, 'error': '', 'index': idx, 'mode': res['mode'],
                'fields': res['fields'], 'drop_lines': res['drop_lines'],
                'kept_lines': res['keep_lines'], 'remains': res['remains'],
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
        # 撤销结构 = 可能已经回到磁盘那份（插卡又撤掉）→ 重算脏标记
        self.refresh_edits_flag()
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
        elif kind == 'clear':
            # 分级删除的"清空"：把清掉的字段原样放回去
            at = int(op[1])
            if not 0 <= at < len(self.beats):
                return {'ok': False,
                        'error': '那一拍已经不在了（可能被别的操作改过）',
                        'index': -1}
            from storygraph.app.beat_clear import restore_fields
            restore_fields(self.beats[at], op[2] or {})
            note = '撤销清空'
            index = at
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
        self.refresh_edits_flag()           # 撤到与磁盘一致时，别再喊"未导出"
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
        # **分级删除留下的"空拍"**（用户 2026-10-25）：
        # 文字清了、代码/舞台还在 —— 列表里要一眼看得出来，
        # 否则那一行会显示成「（无台词）」，跟"本来就是旁白"混在一起。
        try:
            from storygraph.app.beat_clear import empty_note
            empty = empty_note(b)
        except Exception as e:                     # noqa: BLE001 - 标记只是提示
            swallow('app.session_table.list_entry.empty_note', e)
            empty = ''
        return {
            'index': idx,
            'who': (getattr(b, 'speaker', '') or '').strip(),
            'prefix': prefix,
            'empty_note': empty,
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

    # ---------------- 跨章继承：本章第一拍之前台上是什么（2026-10-31） ----
    def stage_initial_state(self):
        """
        本章第一拍**之前**的舞台状态（`stage_snapshots(initial_state=)` 认的那种）。

        `None` = 没有 / 不知道（单文件模式、还没分析完、这个文件不是章节）。
        **不知道时也必须传 `None`**：绝不能拿一份"空舞台"顶上去 —— 那会把
        "上一章可能有背景"说成"这一拍没有背景"，正是要治的那个病。
        """
        return getattr(self, '_stage_entry', None)

    def stage_entry(self):
        """`pipeline.continuity.ChapterEntry`（带 `why` / `note`）或 `None`。"""
        return getattr(self, '_stage_entry_info', None)

    def stage_entry_note(self) -> str:
        """入口状态的一句话（"接上一章 chapter1_4 的收尾；背景 bg9_2 …"）。"""
        info = getattr(self, '_stage_entry_info', None)
        try:
            return str(info.describe() or '').strip() if info is not None else ''
        except Exception as e:                     # noqa: BLE001
            swallow('app.session_table.stage_entry_note', e)
            return ''

    def stage_entry_why_not(self) -> str:
        """现在为什么没有入口状态（人话；有的话返回空串）。"""
        if getattr(self, '_stage_entry_info', None) is not None:
            return ''
        try:
            from storygraph.app import stage_entry as SE
            return SE.why_not(getattr(self, 'project_root', None),
                              self.script_path)
        except Exception as e:                     # noqa: BLE001
            swallow('app.session_table.stage_entry_why_not', e)
            return ''

    def set_stage_entry(self, entry) -> bool:
        """
        界面把后台算好的入口状态塞进来（`ChapterEntry` 或 `None`）。

        返回**变了没有** —— 变了界面才需要重画派生面板（当前舞台 / 尺寸行 /
        小图）。用 `ChapterEntry.key()` 比：`unknown` 是集合、`dict` 顺序不定，
        直接比对象会每次都"变了"，然后每翻一拍重画一次。
        """
        info = entry
        state = None
        key = ()
        if info is not None:
            try:
                state = dict(info.state or {})
                key = tuple(info.key())
            except Exception as e:                 # noqa: BLE001 - 坏对象当"没有"
                swallow('app.session_table.set_stage_entry', e)
                info, state, key = None, None, ()
        before = tuple(getattr(self, '_stage_entry_key', ()) or ())
        self._stage_entry_info = info
        self._stage_entry = state
        self._stage_entry_key = key
        return bool(key != before)

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

        `initial_state`（2026-10-31）：把**上一章留下的**背景/立绘/CG 也算进来
        （`stage_initial_state()`）。不传时与历史行为逐字节一致。
        """
        if not self.has(idx):
            return ''
        try:
            from storygraph.story.stage_snapshot import stage_digest
            # 单文件模式（没有项目根）不传 `entry_why`：跨章本来不存在，
            # 每一拍都喊一句"判不了上一章"只是噪声（老行为逐字节不变）。
            why = (self.stage_entry_why_not()
                   if getattr(self, 'project_root', None) else '')
            return stage_digest(self.beats, idx,
                                initial_state=self.stage_initial_state(),
                                entry_note=self.stage_entry_note(),
                                entry_why=why)
        except Exception as e:                     # noqa: BLE001
            swallow('app.session_table.stage_digest_text', e)
            return ''
    def stage_size_rows(self, idx: int) -> list:
        """
        这一拍**可调尺寸**的对象（背景 + 各 CG/UI 图层）与预设匹配结果。

        语义全在 `app.stage_render.size_rows`（按几何匹配项目已有 transform，
        不按名字猜）；这里只负责凑齐它的输入：这一拍的帧（`build_frame_at`，
        O(idx)）、资源表、项目 transform 表与分辨率。

        **只读**：不改 Beat、不写盘。卡片每翻一拍调一次，所以不建整章的帧。
        """
        if not self.has(idx):
            return []
        try:
            from storygraph.app.stage import build_frame_at
            from storygraph.app.stage_render import size_rows
            registry = self.registry()
            transform_index = self.transform_index()
            screen = self.screen_size()
            frame = build_frame_at(self.beats, int(idx), registry=registry,
                                   transform_index=transform_index,
                                   screen=screen,
                                   # 本章第一拍之前台上是什么（跨章继承）：
                                   # 有了它，第 1 拍才看得见"上一章留下的背景"
                                   # （`source=-1`），「本章第一拍补一条」那个
                                   # 入口才有前提可判。
                                   initial_state=self.stage_initial_state())
            if frame is None:
                return []
            return size_rows(frame, registry=registry,
                             project_root=getattr(self, 'project_root', None),
                             screen=screen,
                             transform_index=transform_index,
                             # 本景"还有几处单独改写"（只读、一次扫描四行共用）：
                             # 不给这份拍表，面板就不知道改源会被谁盖回去 ——
                             # 而"改完没生效"正是尺寸面板最容易被骗的地方。
                             beats=self.beats)
        except Exception as e:                     # noqa: BLE001 - 尺寸格坏不拦卡片
            swallow('app.session_table.stage_size_rows', e)
            return []

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
        # **改没改？**（2026-10-22）：`apply` 会被"翻页顺带提交"反复调用，
        # 不能一调就把"未导出"立起来（否则状态栏永远在喊）。
        # 只比这一拍的前后指纹（~0.1ms），廉价到可以每次都做。
        _before_print = self._beat_edits_print(idx)
        b = self.beats[idx]
        fields = dict(fields or {})

        self._apply_flow_fields(b, fields)

        self._apply_main_columns(b, fields)

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

        self._apply_advanced_fields(b, fields, advanced_open)
        # **类型是说话人的投影**（`say` / `narration` 这一对，见
        # `app.beats.kind_from_speaker`）：用户只填说话人时，高级区那一格
        # 也必须跟着变成 `say` —— 否则它一直显示 `narration`，用户会以为
        # "我填的说话人没生效"（2026-10-05 实测报的）。
        b.kind = B.kind_from_speaker(b.kind, b.speaker)

        self._apply_sprite_cards(b, cards)

        self._apply_word_cells(b, fields, cards)

        self._apply_condition(b, fields)

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
        # 真的变了才把"未导出"立起来（翻页 / 空提交不算改动）。
        if self._beat_edits_print(idx) != _before_print:
            self.mark_edits_dirty()
        return b

    def _apply_flow_fields(self, b, fields) -> None:
        """控制流卡（call / jump）的目标与类型写回（拆自 `apply`，2026-11-01）。"""
        # **控制流卡的目标 / 类型**（2026-10-22，P0-4 第一阶段）。
        #
        # 它改的是 `Beat.raw` 里那一行（`call side`），不是任何一个 Excel
        # 列。所以必须在下面的 `CodeBlock` 处理**之前**做：先让 raw 变成
        # 新目标，后面的 `CodeBlock != compose_code(b)` 才会是 False（前端
        # 同时提交了同步后的代码格），不会把 raw 误降级成 code。
        flow_keys = any(k in fields for k in FLOW_FIELD_KEYS)
        if flow_keys:
            from storygraph.story.flow import (parse_flow, render_flow_line,
                                               update_beat_flow)
            _old_flow = parse_flow(b)
            update_beat_flow(
                b,
                fields.get('FlowType') if 'FlowType' in fields else None,
                fields.get('FlowTarget') if 'FlowTarget' in fields else None,
                fields.get('FlowArgs') if 'FlowArgs' in fields else None,
                fields.get('FlowFrom') if 'FlowFrom' in fields else None)
            # **代码格跟着控制流卡一起改**（2026-10-24，修审计 P0-6）。
            #
            # 从前这件事只在前端做（`main_window._flow_code_synced`）：UI 保存时
            # 会先把「代码」格改成新那行，于是 `compose_code(b)` 与它相等，
            # 下面那支不会把 raw 降级。可**任何别的调用方**（脚本 / 表格导入 /
            # AI agent / 测试）都只交回 `fields()`，那里面 `CodeBlock` 恒等于
            # **旧**行 —— 于是：raw 改成新目标 → `CodeBlock != compose_code(b)`
            # → raw 被清空、旧行写回 code → **改了等于没改，还 ok=True**。
            #
            # 下沉到这里的判据与前端那条一样窄：只有「代码格确实还是原来那行
            # 控制流」才同步；用户手写过别的代码，就交给下面那支去降级。
            if 'CodeBlock' in fields and _old_flow:
                if str(fields.get('CodeBlock') or '').strip() == _old_flow['line']:
                    _new_flow = parse_flow(b)
                    if _new_flow:
                        fields['CodeBlock'] = render_flow_line(
                            _new_flow['type'], _new_flow['target'],
                            _new_flow.get('args') or '',
                            _new_flow.get('from_clause') or '')

    def _apply_main_columns(self, b, fields) -> None:
        """主区那六列（Speaker / Text / Scene / BGM / Voice / Comment）（拆自 `apply`，2026-11-01）。"""
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

    def _apply_advanced_fields(self, b, fields, advanced_open) -> None:
        """高级区：**收起 ≠ 清空**（拆自 `apply`，2026-11-01）。"""
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
                    # **写的是这一拍自己那条自定义语句 → 逐字写回头行**（P2）。
                    #
                    # 现场（审计第四轮 P2）：声明过 `mycue` 的拍，「代码」格里
                    # 写 `mycue intensity=5` → 走下面那条 python 通道 →
                    # 渲染成 `$ mycue intensity=5`（`render_code_block` 会给
                    # 认不出的行补 `$`）→ 产物连 lint 都过不了，而拒因只说
                    # "`$` 语句里的 Python 语法错误"，人猜不到"该填参数格"。
                    if not self._write_statement_head(b, new_code):
                        b.code = new_code
                        b.raw = ''
            if 'Label' in fields:
                self._apply_label(b, str(fields.get('Label') or '').strip())
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

    def _apply_sprite_cards(self, b, cards) -> None:
        """立绘卡 → `show` / `at`（**空网格不清空**）（拆自 `apply`，2026-11-01）。"""
        if cards is not None:
            # **立绘写回的唯一出口**（2026-10-24 修 P0，见 `write_back_show`）。
            #
            # 从前这里直接 `b.show = cards_to_show(cards)` —— **整体替换**。
            # 而卡片数组来自 `beat_sprite_rows`，它跳过"结构化列表达不了"的
            # 项（`behind` / `as` / `with` / `onlayer` / `screen` /
            # `show expression`）。两件事一夹：那些指令**整条消失**，
            # 而它们的 `with` 还会被拍级兜底套给别的立绘（真机 8 组中招）。
            #
            # `write_back_show` 把表达不了的项按原锚点插回去；没有这类项时
            # 它的输出与 `cards_to_show` **逐字节相同**（老语义不变）。
            old_show = str(getattr(b, 'show', '') or '').strip()
            new_show = B.write_back_show(old_show, cards, self.symbols)
            # 卡片空 → 保留原值（"空网格不清空立绘"）。这一条是防误删，
            # 不是保真：用户真要清空立绘，得用卡片上的删除按钮（会走
            # `remove_sprite`，那条路是逐条精确匹配的）。
            if new_show or not old_show:
                b.show = new_show
            # 整拍位置只在真有立绘卡时才重算：空网格连位置也不许动
            # （同一条规矩；以前这里无条件清空 `b.at`）。
            if cards:
                b.at = uniform_position_of(cards)

    def _apply_word_cells(self, b, fields, cards) -> None:
        """「表情 / 位置」两格（**只在没有立绘卡时**写）（拆自 `apply`，2026-11-01）。"""
        # **「表情 / 位置」两格**（2026-10-24，第三轮扫描 `p0_scan_struct.py`
        # 的"改 show 的位置"用例暴露）：`fields()` 会算这两格
        # （`beat_to_fields` 的投影），而 **`apply` 从前根本不读它们** ——
        # 它们只活在旧的 `fields_to_beat`（tkinter 那条路）里。
        # 于是任何"拿到 fields()、改 Position/Expression、再 apply"的调用方
        # （脚本 / 表格导入 / AI agent）都会得到 **改了等于没改、还 ok=True**。
        #
        # 三条限制（一条都不放宽）：
        #   · **只在新值与当前投影不同、且非空时写** —— 空串在这里的意思是
        #     "多张立绘位置不一致、这一格没有统一值"（`beat_position_word` 的
        #     约定），拿它去写会把所有位置抹掉；
        #   · **只在没有立绘卡时**（`cards is None`）：卡片是真源，两处同时
        #     写会互相覆盖；
        #   · 只改 `show` 里的那一项，别的属性/参数不动（`apply_*` 本身是
        #     纯函数）。
        if cards is None:
            if 'Expression' in fields:
                _want = (fields.get('Expression') or '').strip()
                _cur = B.beat_expression_word(b, self.symbols)
                if _want and _want != _cur:
                    b.show = B.apply_expression(b.show, _want)
            if 'Position' in fields:
                _want = (fields.get('Position') or '').strip()
                _cur = B.beat_position_word(b, self.symbols)
                if _want and _want != _cur:
                    # 位置有两种存法，**这一拍用的是哪种就改哪种**：
                    #   · 拍级 `b.at`（渲染成 `show … at X`，作者最常见的写法）
                    #     —— `beat_position_word` 正是在 `item.at` 为空时退回它；
                    #   · 立绘项里的 `@X`（`e happy@left`）—— 由 `apply_position` 改。
                    # 只改已经在用的那一个，避免两种写法叠出来
                    # `e happy@right at right`。
                    if str(getattr(b, 'at', '') or '').strip():
                        b.at = _want
                    else:
                        b.show = B.apply_position(b.show, _want)

    def _apply_condition(self, b, fields) -> None:
        """条件写回 + 链头守卫重算（拆自 `apply`，2026-11-01）。"""
        # 条件显示（卡片上的「＋ 条件」）。**只在调用方真的给了这个键时才写**：
        # 用 `fields.get(..., b.condition)` 会让"没给"和"给了空串"分不开，
        # 而这两件事的语义完全相反（不改 vs 清空条件）。
        if 'condition' in fields:
            _old_cond = str(getattr(b, 'condition', '') or '').strip()
            _new_cond = str(fields.get('condition') or '').strip()
            b.condition = _new_cond
            # **链头一改，后面那几支的守卫要跟着重算**（P1，2026-10-25）。
            #
            # `elif` / `else` 在表里存的是**累积形式**（`(b) and not (a)`）。
            # 只改链头那一行（`if a == 1:` → `if a == 9:`）之后，文件里
            # 后面那几支的 `elif` 一个字节没动 —— 解析出来的否定项就变成了
            # `not (a == 9)`，与表里存的 `not (a == 1)` 对不上：
            #   · 增量补丁判"条件与要写的拍对不上"→ 整份拒绝；
            #   · 就算放过去，**全量重写**又按表里的旧条件写 —— 两条路的
            #     语义分家（同一章导两次得到两种剧情）。
            # 所以在模型侧就把它们改对：否定项里出现"旧的那支条件"就换成新的
            # （`rechain_condition`，与补丁同一个判据）。
            self._rechain_siblings(b, _old_cond, _new_cond)


    @staticmethod
    def _write_statement_head(b: Beat, text: str) -> bool:
        """
        「代码」格里写的是**这一拍自己那条自定义语句** → 写回头行（P2，2026-10-25）。

        `mycue intensity=5` 是 Ren'Py 语句，不是 Python。走代码通道会被
        `render_code_block` 补上 `$`（`$ mycue intensity=5`）—— 产物连加载
        都过不了，而拒因只会说"Python 语法错误"，看不出"参数该填参数格"。

        判据只有一条：**首词就是这一拍自己那条语句的名字**。那就不可能是
        "改成一段 python 代码"，只能是"改这条语句本身"：

          · 项目声明过的语句（`spec_name`）→ 写 `spec_head`（渲染时替换头行，
            见 `beat_render._emit_opaque`）；
          · 工具不认识的块（`opaque`）→ 换掉它的首行；
          · 普通原生行（`raw`）→ 换掉 `raw`。

        三条都不匹配（首词不是自己那条语句 / 多行）→ 返回 False，
        调用方照旧走 python 通道（那才是"我就要写代码"）。
        """
        line = (text or '').strip()
        if not line or '\n' in line:
            return False
        verb = line.split()[0]
        name = str(getattr(b, 'spec_name', '') or '').strip()
        if name and verb == name:
            b.spec_head = line
            opaque = str(getattr(b, 'opaque', '') or '')
            if opaque.strip():
                rows = opaque.replace('\r\n', '\n').split('\n')
                b.opaque = '\n'.join([line] + rows[1:])
            return True
        opaque = str(getattr(b, 'opaque', '') or '')
        if opaque.strip():
            rows = opaque.replace('\r\n', '\n').split('\n')
            head = rows[0].strip()
            if head.split() and head.split()[0] == verb:
                b.opaque = '\n'.join([line] + rows[1:])
                if name:
                    b.spec_head = line
                return True
        raw = str(getattr(b, 'raw', '') or '')
        if raw.strip() and '\n' not in raw and raw.split()[0] == verb:
            b.raw = line
            return True
        return False

    def _rechain_siblings(self, b: Beat, old_cond: str, new_cond: str) -> None:
        """
        改了**链头**那一支的条件之后，把同一链里后面几支的守卫跟着重算。

        判据（一条不满足就什么都不做 —— 让补丁那边去拒绝并说清楚）：

          · 这一拍是链头：`nest_path` 的最内层是 `if:1`（第 1 支就是 `if`）；
          · 它原来那条条件**就是自己那一支的**（没有 `and not (…)` 前缀）——
            只有链头是这样（`elif` 存的都是累积形式）；
          · 新条件非空（清空 = 拆掉这一层 `if`，那是结构改动，不在这里做）。

        后面每一支的否定项里找到"旧的那支条件"就换成新的（`rechain_condition`）。
        换不动（形状认不出）就整体放弃 —— 宁可让补丁拒绝，也不写一半。
        """
        if not old_cond or not new_cond or old_cond == new_cond:
            return
        path = str(getattr(b, 'nest_path', '') or '')
        if not path:
            return
        head, _, last = path.rpartition('>') if '>' in path else ('', '', path)
        if not last.startswith('if:') or last != 'if:1':
            return                             # 不是链头
        prefix_path = (head + '>') if head else ''
        try:
            from storygraph.story.patcher import (is_flattened_condition,
                                                  rechain_condition)
        except Exception as e:                 # noqa: BLE001
            swallow('app.session_table._rechain_siblings', e)
            return
        if is_flattened_condition(old_cond):
            return                             # 自己那条也是累积形式 → 不是链头
        # 找出同一链、排在它后面的兄弟分支
        later = []
        started = False
        for o in self.beats:
            if o is b:
                started = True
                continue
            if not started:
                continue
            opath = str(getattr(o, 'nest_path', '') or '')
            if prefix_path:
                if not opath.startswith(prefix_path):
                    break
                tail = opath[len(prefix_path):]
            else:
                tail = opath
            if '>' in tail:
                continue                       # 更深一层，不是兄弟
            if not tail.startswith('if:'):
                break                          # 链结束了
            later.append((o, tail))
        if not later:
            return
        plans = []
        for o, tail in later:
            cur = str(getattr(o, 'condition', '') or '').strip()
            if not cur:
                continue
            nxt = rechain_condition(cur, old_cond, new_cond)
            if nxt is None:
                return                         # 认不出 → 整体放弃（不写一半）
            plans.append((o, nxt))
        for o, nxt in plans:
            o.condition = nxt

    def _apply_label(self, b: Beat, new_label: str) -> None:
        """
        `Label` 那一格：**改一格 = 给整章那个 label 改名**（2026-10-24，P0-8）。

        为什么不能只改这一拍：`label 名:` 是**块头**，它下面所有拍共用同一个
        名字。只把一拍的名字改掉，内存里就出现"同一个块里两拍说自己属于两个
        不同 label"的状态 —— 导出时块头只能写一个名字，另一半的拍在产物里
        找不到自己的 label，整体校验判"台词找不到"，**整章导出直接失败**。

        所以这里按"改名"处理：凡是旧名字的拍都跟着改。**新名字已经被别的
        label 占了**时不改（那更像是"把这一拍挪到另一个 label"，属于结构改动，
        交给导出的校验去拒绝，绝不悄悄合并两个 label）。
        """
        if not new_label or new_label == b.label:
            return
        old_label = b.label
        taken = any(o is not b and str(getattr(o, 'label', '') or '') == new_label
                    for o in self.beats)
        if taken:
            return
        for o in self.beats:
            if str(getattr(o, 'label', '') or '') == old_label:
                o.label = new_label

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
            # **立即可重建**（2026-10-24 审计 P1-2）：这一格现在是**完整**
            # 舞台投影（见 `app.beats.stage_cell`），能认出来的场景/立绘/
            # 退场/转场全部重建；认不出的（`[停顿]` / 原生语句）由
            # `stage_only_ops` 原样留在 `b.stage` 里带走。
            #
            # 从前这里只清空、等 `apply` 末尾那次 hydrate —— 那条路本身没坏，
            # 坏的是格子当时只显示"结构化列表达不了的那一条"，于是"清空 +
            # 从一条重建"必然把其余舞台指令丢掉。现在先重建再把 stage
            # 归一成"表达不了的部分"，与没手改那条分支同一个出口。
            try:
                hydrate_from_dsl([b], self.symbols)
            except Exception as e:          # noqa: BLE001 - 解析失败也别拦住保存
                swallow('editor._apply_stage.rehydrate', e)
            parts = [t for t in (compose_stage_extra(b, self.symbols),
                                 stage_only_ops(typed, self.symbols)) if t]
            b.stage = '; '.join(parts)
            try:
                b._hydrated_stage = b.stage
            except Exception:               # noqa: BLE001 - Beat 不该是槽位
                swallow('editor._apply_stage')
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
        self.mark_edits_dirty()          # 绕过 `apply()` 直接改拍，标记要补
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
        self.mark_edits_dirty()
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
        line_id = str(getattr(beat, 'line_id', '') or '').strip()
        return {
            'ok': True,
            'beat': beat,
            'changed': changed,
            # `line_id` 的字段名只有一个含义：**合法 LineId**。没有就返回 None，
            # 不许再塞一个 `'第 3 拍'` 进这个字段（清单 §二十）。
            # 给人看的文字放 `display_name`（前端显示用）。
            'line_id': line_id or None,
            'display_name': line_id or f'第 {idx + 1} 拍',
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
        beat = self.restore(snap)
        # 撤销把内容改回去了 —— 但"现在内存里这份"仍可能与磁盘不同
        # （例如撤到"改之前"，而改之前本身也没导出过）。所以重算，不猜。
        self.refresh_edits_flag()
        return beat

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
