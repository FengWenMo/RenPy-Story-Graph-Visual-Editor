"""
storygraph.app.editor
=====================
**编辑器会话门面** —— 前端唯一需要认识的对象。

它是什么（深模块）
------------------
一个"打开了一章、正在编辑哪一拍、改完怎么落盘"的会话。接口很小：

    s = EditorSession(registry_provider=..., project_root=...)
    s.load('game/chapter/ch1.rpy')
    while s.has(i):
        fields = s.fields(i)          # → 直接塞进表单
        cards  = s.cards(i)           # → 直接塞进立绘网格
        ...用户改...
        s.apply(i, fields, cards)     # 表单 → Beat

    名字      一次导出          # 编译 + 增量补丁 + 安全门禁，**一个调用**
    体检      项目体检

实现里装着的全部业务不变量（前端再也碰不到，也就不可能各写一套）：

  · **`show` 是投影，不是输入**：表情/位置住在卡片 chip 上，
    写回时按出场顺序拼成 `show`（多属性不压平、extras 不丢、
    位置只有统一才整拍）；
  · **Stage 每次重算**（它是派生视图，不是用户输入）；
  · **空网格不清空立绘**（防误删）；
  · **高级区只在展开时才写回**（收起 ≠ 清空）；
  · 翻页是**纯导航**，不碰别的拍；
  · 落盘一律过生成门禁（`generation_guard`）。

为什么值得存在
--------------
拆分前，上面每一条都在 tkinter 的 `BeatEditorPanel` 里，而 Qt 版
`main_window.py` 只能**照抄一遍**并伸手去摸 `self._panel._speaker`
这种私有属性 —— 两套前端、两套语义，改一处必漏一处。

**禁止**在本模块里 import 任何 UI 框架。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

from storygraph.app import beats as B
from storygraph.app.stage import build_frames
from storygraph.story.beat_build import beats_from_parse_result, hydrate_from_dsl
from storygraph.story.beat_model import Beat, compose_stage_extra
from storygraph.story.beat_render import make_symbols
from storygraph.story.generation_guard import (evaluate_generation,
                                               labels_in_danger,
                                               write_generation)

#: 主区字段（每一拍真正常改的）—— 与两套前端共用同一份划分
PRIMARY_FIELDS = ['Speaker', 'Text', 'Scene', 'BGM', 'Voice', 'Comment']
#: 高级区字段（默认折叠）
ADVANCED_FIELDS = ['SFX', 'CodeBlock', 'Label', 'Kind', 'Notes', 'Stage']


class EditorSession:
    """
    一章的编辑会话。**前端只碰这个类**（不碰 Beat 内部字段名）。

    registry_provider: 返回资产 registry 的可调用对象（可为 None）。
        故意收一个函数而不是 engine 对象 —— 会话不需要知道"引擎"是什么。
    """

    def __init__(self, registry_provider: Optional[Callable] = None,
                 project_root=None):
        self._registry_provider = registry_provider
        self.project_root = (Path(project_root)
                             if project_root is not None else None)
        self.script_path: Optional[Path] = None
        self.beats: List[Beat] = []
        self.symbols = None

    # ------------------------------------------------------------------
    #  资产
    # ------------------------------------------------------------------

    def registry(self):
        """当前资产 registry（拿不到就 None —— 界面降级但不崩）。"""
        if self._registry_provider is None:
            return None
        try:
            return self._registry_provider()
        except Exception:                    # noqa: BLE001
            return None

    def source(self):
        """
        把 registry 适配成"看起来像 engine"的对象。

        `app.beats` 里那批检索函数（`participants` / `search_assets` /
        `asset_row` …）历史上收的都是 **engine**（用 `engine.get_registry()`
        取表）。会话手上只有 registry，于是统一过一次
        `RegistrySource` 适配 —— **不在会话里重写一遍检索逻辑**。
        """
        from storygraph.app.picker import RegistrySource
        return RegistrySource(self.registry(), self.project_root)

    def refresh_symbols(self):
        """重算符号表（脚本声明变了 / 换了项目时调用）。"""
        self.symbols = make_symbols(self.registry())
        return self.symbols

    def characters(self) -> list:
        """可用说话人 ID（排序）。"""
        reg = self.registry()
        try:
            return sorted((getattr(reg, 'characters', {}) or {}).keys())
        except Exception:                    # noqa: BLE001
            return []

    def participants(self, limit: int = 8) -> list:
        """本项目的参与角色（含脚本声明；见 `beats.participants`）。"""
        if self.registry() is None:
            return []
        try:
            return B.participants(self.source(), limit=limit)
        except Exception:                    # noqa: BLE001
            return []

    # ------------------------------------------------------------------
    #  载入
    # ------------------------------------------------------------------

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
        return len(self.beats)

    def has(self, idx: int) -> bool:
        return 0 <= idx < len(self.beats)

    def __len__(self) -> int:
        return len(self.beats)

    # ------------------------------------------------------------------
    #  读：Beat → 表单（前端直接塞控件）
    # ------------------------------------------------------------------

    def fields(self, idx: int) -> dict:
        """这一拍的主区+高级区字段（供表单显示）。"""
        if not self.has(idx):
            return {}
        return B.beat_to_fields(self.beats[idx], self.symbols)

    def cards(self, idx: int) -> list:
        """
        这一拍的**立绘卡**（供网格显示）。

        卡的数据契约（与两套前端一致）：
            {image, expression, position, at, speaking, order_index, extra_rows}
        """
        if not self.has(idx):
            return []
        rows = B.beat_sprite_rows(self.beats[idx], self.symbols)
        items = []
        for card in B.group_sprites_by_tag(rows):
            it = dict(card.get('active') or {})
            it['extra_rows'] = list(card.get('extras') or [])
            it['order_index'] = card.get('first_index', 0)
            items.append(it)
        return items

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

    def speaking_card_index(self, idx: int) -> int:
        """当前说话人对应哪张立绘卡（-1 = 没有）。"""
        if not self.has(idx):
            return -1
        rows = B.beat_sprite_rows(self.beats[idx], self.symbols)
        return B.speaking_sprite_index(self.beats[idx], rows, self.symbols)

    # ------------------------------------------------------------------
    #  写：表单 → Beat
    # ------------------------------------------------------------------

    def apply(self, idx: int, fields: dict, cards: list = None,
              advanced_open: bool = False) -> Optional[Beat]:
        """
        把表单内容写回第 idx 拍。返回那一拍（越界返回 None）。

        语义（与 tkinter 版 `collect_beat` 一字不差，现在只有这一份实现）：
          · 主区**始终**写回；
          · 高级区**只在展开时**写回（收起 ≠ 清空）；
          · `show` 只有拼得出内容时才覆盖（**空网格不清空立绘**）；
          · `at` 只在"所有立绘位置一致"时才整拍（否则留空，防错位）；
          · `Stage` **每次重算**（它是投影，不是输入）；
          · 最后过一遍 `hydrate_from_dsl`，保证与 .rpy 同源。
        """
        if not self.has(idx):
            return None
        b = self.beats[idx]
        fields = dict(fields or {})

        b.speaker = str(fields.get('Speaker') or '').strip()
        b.text = str(fields.get('Text') or '').strip()
        b.scene = str(fields.get('Scene') or '').strip()
        b.bgm = str(fields.get('BGM') or '').strip()
        b.voice = str(fields.get('Voice') or '').strip()
        b.comment = str(fields.get('Comment') or '').strip()

        if advanced_open:
            b.sfx = str(fields.get('SFX') or '').strip()
            b.code = str(fields.get('CodeBlock') or '').strip()
            b.raw = ''
            b.label = str(fields.get('Label') or '').strip() or b.label
            b.kind = str(fields.get('Kind') or '').strip() or b.kind
            b.notes = str(fields.get('Notes') or '').strip()

        if cards is not None:
            show = B.cards_to_show(cards, self.symbols)
            if show:
                b.show = show
            b.at = uniform_position_of(cards)

        b.stage = compose_stage_extra(b, self.symbols)
        try:
            hydrate_from_dsl([b], self.symbols)
        except Exception:                    # noqa: BLE001 - 与旧实现一致的兜底
            pass
        return b

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

    # ------------------------------------------------------------------
    #  演出预览
    # ------------------------------------------------------------------

    def frames(self) -> list:
        """整章的舞台帧（供预览窗口播放）。"""
        return build_frames(self.beats, self.registry())

    # ------------------------------------------------------------------
    #  导出（编译 + 增量补丁 + 安全门禁）
    # ------------------------------------------------------------------

    def compile(self):
        """编译当前 beats（返回编译结果对象，不落盘）。"""
        from storygraph.pipeline.compile import compile_beats
        return compile_beats(self.beats, registry=self.registry())

    def default_output_path(self) -> Path:
        stem = self.script_path.stem if self.script_path else 'out'
        base = (self.project_root or Path('.')) / 'build' / 'renpy'
        return base / f'{stem}.generated.rpy'

    def export(self, target) -> dict:
        """
        落盘导出。**所有安全检查都在这里**，前端只管挑路径。

        返回：{ok, path, note, todos, blocking}
          · ok=False 时 `blocking` 是拦截原因（前端展示即可）；
          · note 说明用了增量补丁还是全量重生成。
        """
        target = Path(target)
        result = self.compile()
        if getattr(result, 'blocking', None):
            return {'ok': False, 'path': target, 'blocking': list(result.blocking),
                    'note': '', 'todos': 0}

        text = result.rpy_text
        note = self._patch_note(text)
        if note['used_patch']:
            text = note['text']

        occupied = labels_in_danger(target, self.project_root,
                                   exclude={target, self.script_path})
        issues = evaluate_generation(text, target,
                                     project_root=self.project_root,
                                     occupied=occupied)
        blocking = [i for i in issues if i.is_blocking]
        if blocking:
            return {'ok': False, 'path': target,
                    'blocking': [getattr(i, 'message', str(i)) for i in blocking],
                    'note': note['text_note'], 'todos': 0}

        write_generation(text, target, project_root=self.project_root)
        return {'ok': True, 'path': target, 'blocking': [],
                'note': note['text_note'],
                'todos': getattr(result, 'todo_count', 0)}

    def _patch_note(self, text: str) -> dict:
        """试着走增量补丁；不行就全量重生成，并把原因写成一句人话。"""
        from storygraph.story.patcher import patch_export
        try:
            src = (self.script_path
                   if self.script_path and Path(self.script_path).exists()
                   else None)
            res = patch_export(self.beats, src, text, registry=self.registry())
            if res.ok:
                return {'used_patch': True, 'text': res.text,
                        'text_note': f'增量补丁：对位 {res.matched_beats}/'
                                     f'{res.total_beats} 拍，'
                                     f'改动 {res.changed_lines} 行'}
            reason = ('；'.join(res.reasons)
                      or f'对位率不足（{res.coverage:.0%}）')
            return {'used_patch': False, 'text': text,
                    'text_note': f'全量重生成（增量未采用：{reason}）'}
        except Exception as e:               # noqa: BLE001
            return {'used_patch': False, 'text': text,
                    'text_note': f'全量重生成（增量不可用：{e}）'}

    def path_of_image(self, image_spec: str):
        """
        立绘 image spec（`ling happy`）→ 磁盘上的真实图片路径（找不到 None）。

        **这是立绘缩略图的真源**：路径只在资源表的 `Path` 列里，
        而 `symbols` 里没有资源表 —— 早期前端拿 `symbols.registry` 去取，
        恒为 None，于是缩略图静默地永远加载不出来。
        """
        from storygraph.app.picker import thumbnail_path
        spec = str(image_spec or '').strip()
        if not spec or self.registry() is None:
            return None
        try:
            from storygraph.app.beats import lookup_image_id
            images = getattr(self.registry(), 'images', {}) or {}
            parts = spec.split()
            if not parts:
                return None
            tag, rest = parts[0], parts[1:]
            if not rest:
                return None
            # `ling happy` → `ling_happy` 这类 ID，先在表里找准
            aid = lookup_image_id(images, tag, rest)
            if not aid:
                aid = '_'.join([tag] + rest)
            return thumbnail_path(self.registry(), 'image', aid,
                                  self.project_root)
        except Exception:                    # noqa: BLE001
            return None

    def health(self, limit: int = 30) -> dict:
        """项目体检（**只读，不写盘**）。返回 {'issues': [...], 'total': n}。"""
        if not self.project_root:
            return {'issues': [], 'total': 0,
                    'error': '还没有打开项目'}
        from storygraph.pipeline.build import collect_report
        r = collect_report(self.project_root)
        issues = list(getattr(r, 'issues', []) or [])
        return {
            'issues': [str(getattr(i, 'message', i)) for i in issues[:limit]],
            'total': len(issues),
        }

    # ------------------------------------------------------------------
    #  结构分析（变量区间 / 可达性）
    # ------------------------------------------------------------------

    def analyze_graph(self, graph) -> str:
        """
        变量区间 + 可达性分析，渲染成**人看的文本**（只读，不改图）。

        `visualize_analysis_results` 的入参口径比较绕（要
        `states` / `entry_id` / `independent` 三个不同的 key，而且
        `independent` 是 label 名、`stranded` 是节点 id）。以前是前端各自
        拼一遍，拼错就把整个工程显示成一堆"不可达"。会话负责拼对一次，
        前端只给一个图、拿一段文字。
        """
        if graph is None or not getattr(graph, 'nodes', None):
            return '（还没有可分析的图 —— 先扫描项目）'
        from storygraph.graph.analysis import (analyze_reachability_full,
                                               visualize_analysis_results)
        res = analyze_reachability_full(graph)
        return visualize_analysis_results(
            graph,
            res.get('states') or {},
            res.get('warnings') or [],
            entry_id=res.get('entry_id'),
            independent=res.get('independent') or [],
        )


def uniform_position_of(cards: list) -> str:
    """
    卡数组 → 整拍位置。**只有所有立绘位置一致才返回它**，否则留空。

    留空很关键：位置是"整拍"的属性，若不一致还硬写一个，
    会兜底套到别的立绘头上 → 立绘错位（实测踩过）。
    """
    rows = [{'at': (str(c.get('position') or '').strip()
                    or str(c.get('at') or '').strip())} for c in (cards or [])]
    return B.uniform_sprite_position(rows)


__all__ = ['EditorSession', 'PRIMARY_FIELDS', 'ADVANCED_FIELDS',
           'uniform_position_of']
