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
from storygraph.story.beat_model import (Beat, KIND_MENU, MENU_NO_CAPTION,
                                         compose_stage_extra)


__all__ = ['TableSessionMixin', 'BeatSnapshot', 'uniform_position_of']


#: 撤销快照要装的字段。
#:
#: 选取原则：**所有会进渲染 / 进 Excel 的标量**（少一个，撤销就会留下
#: 半新半旧的状态），加上 `ordered`（在 `snapshot()` 里单独深拷贝）。
#: 刻意不含 `src_first` / `src_last`（增量导出的源文件行号）与
#: `_from_script`：撤销一次字段编辑不该改动"这一拍在源文件的哪几行"。
_SNAPSHOT_FIELDS = (
    'seq', 'label', 'line_id', 'content_hash', 'condition', 'comment',
    'kind', 'speaker', 'text', 'scene', 'stage', 'bgm', 'sfx',
    'show', 'hide', 'at', 'scene_at', 'with_', 'transform', 'stage_order',
    'op_order', 'raw', 'code', 'music', 'music_params', 'music_action',
    'sound', 'sound_params', 'sound_action', 'voice', 'notes',
)


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

        **不能拿 `Beat.content_hash` 当判据**：那是**持久化**字段
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
                '_last_saved', '_last_idx', '_last_removed')

    def load(self, path) -> int:
        """
        载入一个 .rpy，解析成 Beat 列表。返回拍数。

        与旧路径**同一条语义**：ParseResult → beats → 补 Stage → hydrate DSL。
        """
        from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                     RenPyParser)
        path = Path(path)
        pr = ParseResultBuilder().build(RenPyParser().parse(str(path)))
        raw = beats_from_parse_result(pr)
        symbols = self.refresh_symbols()
        self.beats = list(raw)
        for b in self.beats:
            b.stage = compose_stage_extra(b, symbols)
        hydrate_from_dsl(self.beats, symbols)
        self.script_path = path
        # 换了文件，旧的撤销记录就没有意义了（拍号对不上别的章节）。
        self.forget_undo()
        return len(self.beats)

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
        self.beats.insert(at, beat)
        self._renumber()
        self.forget_undo()                    # 拍号变了，旧的撤销记录对不上
        return {'ok': True, 'error': '', 'index': at,
                'label': beat.label, 'count': len(self.beats)}
    def delete_beat(self, idx: int) -> dict:
        """
        删掉第 idx 张卡片，返回 `{ok, index, removed, count}`。

        **只改内存**：落盘仍要按「⤓ 导出」，所以误删的退路是
        `restore_last_removed()`（单级）或重新载入这一章。
        """
        if not self.has(idx):
            return {'ok': False, 'error': '这一拍不存在', 'removed': None}
        if len(self.beats) <= 1:
            return {'ok': False, 'error': '一章至少要留一拍', 'removed': None}
        removed = self.beats.pop(idx)
        self._last_removed = (idx, removed)
        self._renumber()
        self.forget_undo()
        return {'ok': True, 'error': '', 'index': min(idx, len(self.beats) - 1),
                'removed': removed, 'count': len(self.beats)}
    def restore_last_removed(self) -> dict:
        """把**上一次删掉的那张卡片**插回原位（单级撤销）。"""
        if self._last_removed is None:
            return {'ok': False, 'error': '没有可恢复的卡片', 'index': -1}
        idx, beat = self._last_removed
        at = max(0, min(int(idx), len(self.beats)))
        self.beats.insert(at, beat)
        self._last_removed = None
        self._renumber()
        self.forget_undo()
        return {'ok': True, 'error': '', 'index': at, 'count': len(self.beats)}

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
        except Exception:                     # noqa: BLE001
            return {}
    def list_entry(self, idx: int) -> dict:
        """章节列表要用的一行摘要（前缀图标 / 说话人 / 台词预览）。"""
        b = self.beats[idx]
        prefix = {'narration': '☁', 'menu': '⚑', 'menu_option': '➜'}.get(
            getattr(b, 'kind', ''), '')
        return {
            'index': idx,
            'who': (getattr(b, 'speaker', '') or '').strip(),
            'prefix': prefix,
            'text': (getattr(b, 'text', '') or '').strip() or '（无台词）',
            'line_id': getattr(b, 'line_id', '') or '',
        }
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
        except Exception:                     # noqa: BLE001
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

        if advanced_open:
            if 'SFX' in fields:
                b.sfx = str(fields.get('SFX') or '').strip()
            if 'CodeBlock' in fields:
                b.code = str(fields.get('CodeBlock') or '').strip()
                b.raw = ''
            if 'Label' in fields:
                b.label = str(fields.get('Label') or '').strip() or b.label
            if 'Kind' in fields:
                b.kind = str(fields.get('Kind') or '').strip() or b.kind
            if 'Notes' in fields:
                b.notes = str(fields.get('Notes') or '').strip()

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
        return b

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
        """
        typed = None
        if 'Stage' in fields:                # 表单真的给了这一格（高级区展开）
            typed = str(fields.get('Stage') or '').strip()
        loaded = (b.stage if stage_loaded is None
                  else str(stage_loaded or '')).strip()

        if typed and typed != loaded:
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

        只装**标量字段** + `ordered`（后者是 raw/code 的交错顺序，
        丢了会把 `$ f()` 与 `show screen` 的交替演出排乱）。
        其它状态（`src_first` 等增量导出用的行号、`_from_script` 标记）
        刻意**不进快照**：撤销一次编辑不该改掉它们在源文件里的位置。
        """
        if not self.has(idx):
            return None
        b = self.beats[idx]
        data = {name: getattr(b, name, '') for name in _SNAPSHOT_FIELDS}
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
        `BeatSnapshot.fingerprint`），而不是 `Beat.content_hash` ——
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
