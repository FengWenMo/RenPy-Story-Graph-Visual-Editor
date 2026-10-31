"""
storygraph.app.stage
====================
**舞台演出推导层**（框架无关 / 无 UI 依赖）。

把 Beat 序列推导成"一帧帧可绘制的舞台画面"：背景 / 立绘 / 图层 /
站位 / 说话人高亮。用户 2026-09-28 的原话：

> 我希望你能够写一个舞台演出核心，让这一章能够实时根据卡片效果跑起来

难点不在画，在**语义同源**：预览必须和生成的 .rpy 说同一件事，
否则"预览在骗人"（比没有预览更糟）。所以这里**不重写舞台推导** ——
舞台状态全部来自 `story.stage_snapshot.snapshot_rows`（与「舞台快照」表、
`render_beat_to_rpy` 同一套语义）。本层只多做两件事：

  1. `transform_x()`：把 transform 名 / 位置词换算成屏幕 x（**位置建议**，
     不是引擎真相；拿不准就居中，不猜）；
  2. 把每拍的最终状态组装成 `StageFrame`，并把素材名解析成真实图片路径。

契约（勿破）：
  · 本模块**禁止** import 任何 UI 框架，也禁止 import `storygraph.ui_qt*`；
  · `ui/stage_preview.py` 作为兼容门面 re-export 这里的一切，
    tkinter 的 `StagePreviewWindow` 仍留在那边。
  · 纯函数可无头测试：`build_frames` / `resolve_frame_assets` 等起 tkinter 也不需要。

分层：
    snapshot_rows(beats)   →  [{...}]            （story 层，已有）
    build_frames(beats)    →  [StageFrame]       （本模块）
    resolve_frame_assets() →  {bg, sprites, layers}（本模块）
    <前端>                 →  画出来 + 播放控制
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow
from storygraph.core.vocabulary import same_layer_tag
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.app.beats import lookup_image_id
from storygraph.story.stage_snapshot import snapshot_rows
from storygraph.story.transition_catalog import effect_of, scan_project_transitions
from storygraph.story.transition_expr import beat_transitions

#: 归一化坐标：0.0 = 屏幕最左，1.0 = 最右（与 Ren'Py 的 xalign 同向）
CENTER_X = 0.5


def transform_x(name: str, registry=None) -> float:
    """
    transform 名 / 位置词 → 归一化 x（0..1）。

    ⚠ 2026-10-23（清单 §五）：这里从前有**第二份**位置表 `_POSITION_X`
    （left=0.2 / right=0.8），而真正跑在预览里的是 `story.atl.placement_of`
    （left=0.0 / right=1.0，且能跟项目 transform 的调用链）。两份值不一样，
    而用户能看见的那一套（`build_frames`）走的是 `placement_of` —— 这个函数
    只有测试在调，测试因此给了"位置算法有覆盖"的假安全感。
    现在它是**纯委托**：一个概念只有一处实现，两边谁改都不会漂移。
    """
    from storygraph.story.atl import placement_of

    place = placement_of(str(name or '').strip(), registry=registry)
    return max(0.0, min(1.0, float(place.x)))


@dataclass
class SpriteLayout:
    """一帧里的一个立绘（画面示意用）。"""

    image: str          # `ling happy`
    tag: str            # `ling`
    at: str             # `i11` / `left` / ''
    x: float            # 0..1（归一化中心；没有真实几何时用它）
    speaking: bool = False
    #: 项目 transform 算出来的**真实舞台几何**（见 `story.atl`）。
    #: `placement_of` **永远返回一个 Placement**（最差是"按名字猜的居中"），
    #: 所以这里不会是 `None`；判"是不是真算出来的"看 `from_atl`。
    place: object = None
    #: 在 `snapshot_rows` 里的**原始显示顺序**（先 show 的在下面）。
    #: 预览按它叠，不能按"谁在说话"重排 —— 那会改变遮挡关系。
    order_index: int = 0

    @property
    def zoom(self) -> float:
        return float(getattr(self.place, 'zoom', 1.0) or 1.0)

    @property
    def from_atl(self) -> bool:
        """这个位置是从项目 transform 里真算出来的吗？（决定要不要按真实几何画）"""
        return bool(getattr(self.place, 'from_atl', False))

    @property
    def label(self) -> str:
        """画在方块上的字（只用 tag + 表情，别太长）。"""
        parts = self.image.split()
        if len(parts) >= 2:
            return f'{parts[0]}\n{" ".join(parts[1:])}'
        return self.image


@dataclass
class StageFrame:
    """
    一拍 = 一帧舞台画面。

    这些字段和 `snapshot_rows` 一一对应，**不新增语义**——
    预览只是把它画出来。
    """

    index: int
    line_id: str
    label: str
    speaker: str
    text: str
    scene: str
    #: 显示名（注册表 `Name`；空 = 旁白，不画名条）。`speaker` 是角色变量。
    speaker_name: str = ''
    #: 角色名颜色（`who_color`）与台词颜色（`what_color`，空 = 用 gui 默认）。
    speaker_color: str = ''
    what_color: str = ''
    #: 背景自己的变换（`scene bg room at bgfit` 的 `bgfit`）。
    #: **持续状态**：来自快照 state（`scene` 之后每一拍都带着），
    #: 不是"这一拍写了才有"（2026-10-30，⑦ P0）。
    scene_at: str = ''
    sprites: list = field(default_factory=list)
    layers: str = ''            # CG/UI 等非角色图层
    #: 图层 tag → 它的 `at`（`show cg cg01 at cg38402160` 的变换）。
    #: 来自快照 state['layer_at']；缺键默认空 dict，老调用方不受影响。
    layers_at: dict = field(default_factory=dict)
    #: **出处**（2026-10-31）：这一帧的背景是第几拍写的、每个图层 tag 是第几拍
    #: show 的（0 基；-1 = 不知道）。背景/图层变换是持续状态，改尺寸必须回到
    #: "写它的那一行"——只认本拍字段的话，继承来的 96% 的拍都改不了。
    scene_src: int = -1
    layer_src: dict = field(default_factory=dict)
    music: str = ''
    sound: str = ''
    voice: str = ''
    changes: str = ''           # 本拍变化（和快照表同一份文字）
    #: 这一拍的**转场**（`with dissolve` 里的 `dissolve`；空 = 硬切）。
    #: 2026-10-18（审计 P1-8"无转场"）：预览据此做交叉淡入。
    with_: str = ''
    #: 这一拍的**停顿秒数**（`pause 1.5` / `pause 1.5 hard`；0 = 没写）。
    #: 预览据此决定自动播放停多久 —— 从前固定 900ms，长台词一闪而过。
    pause: float = 0.0
    #: 这一拍派生出的**过场调用**（`transition_expr.beat_transitions(beat)`）。
    #: 三路都算：`Beat.with_` 子句 / `Beat.raw` 里独立一行的 `with X` /
    #: `Beat.code` 里的 `$ renpy.transition(...)`。默认空元组 ——
    #: 手工构造的老调用方不用改（缺省 = "这一拍没有过场"）。
    transitions: tuple = ()
    #: `transitions` 翻成的**效果描述**（`transition_catalog.effect_of`）。
    #: 只在构造时拿得到 `project_root` 时自定义名字才解析得准（`define 名字 =
    #: Fade(…)`）；拿不到时裸构造器照常、自定义名是 `unknown`。消费方可以
    #: 用 `story.transition_catalog.effect_of` + `project_transitions()` 重解析
    #: （`app.stage_render.transition_notes` 就是这么做的）。
    effects: tuple = ()

    @property
    def duration_ms(self) -> int:
        """
        自动播放时这一拍该停多久（毫秒）。

        判据顺序（都是"脚本里写得出来的东西"，不猜节奏）：
          ① 有 `pause N` → N 秒（那是作者明确写的停顿）；
          ② 有台词 → 按字数估（中文按字、西文按词的速度都落在
             每字 60~100ms 这个量级），夹在 [700, 6000] 之间；
          ③ 纯舞台变化（没有台词）→ 600ms（够看清切了什么）。
        """
        if self.pause and self.pause > 0:
            return max(200, int(self.pause * 1000))
        text = self.text or ''
        if text:
            return max(700, min(6000, 500 + len(text) * 70))
        return 600

    @property
    def fades(self) -> bool:
        """这一拍是不是**渐变**类转场（`dissolve` / `fade` / `Fade(…)`）。"""
        name = str(self.with_ or '').strip().lower()
        if not name:
            return False
        return any(k in name for k in ('dissolve', 'fade', 'dissolve',
                                       'flash', 'pixellate'))

    @property
    def dialogue(self) -> str:
        """这一帧对话框里显示什么。"""
        who = self.display_speaker
        body = self.text or ''
        if who and body:
            return f'{who}：「{body}」'
        return body or '（没有台词）'

    @property
    def display_speaker(self) -> str:
        """名条上写的字（注册表显示名优先；没有就退回变量名）。"""
        return str(self.speaker_name or self.speaker or '')

    @property
    def shows_window(self) -> bool:
        """这一拍要不要画 say 窗口（Ren'Py：有 say 语句就有对话框）。"""
        return bool(str(self.text or '').strip())


# ---------------------------------------------------------------------------
#  项目转场目录（按项目根 + `.rpy` 修改时间缓存）
# ---------------------------------------------------------------------------
#
# 为什么要缓存：`scan_project_transitions` 读一遍项目里的 `.rpy`（真机 96 个
# 文件约 0.1s）；而卡片每次刷新、预览每翻一拍都要问"这一拍的过场长什么样"。
# 每拍扫一遍 = 把 0.1s × 1022 拍摊在播放上，不能接受。这里按项目根缓存，
# 并用"路径 + mtime + 大小"的签名兜底：项目里改了 `.rpy` 就会重扫。

#: `{项目根: (签名, 取签名时刻, {名字: Effect})}`
_TRANSITION_CACHE: dict = {}
#: 目录签名的复核间隔（秒）：改完 `.rpy` 最多这么久就会生效。
_TRANSITION_REFRESH_SECONDS = 2.0


def _transition_signature(project_root) -> tuple:
    """项目 `.rpy` 的"修改指纹"（路径 + mtime + 大小）—— 变了就重扫。"""
    try:
        from storygraph.story.cds_registry import find_scripts
        scripts = list(find_scripts(project_root))
    except Exception as e:                    # noqa: BLE001 - 目录坏了不该崩
        swallow('app.stage.transition_scripts', e)
        return ()
    out = []
    for path in scripts:
        try:
            stat = os.stat(path)
        except OSError:
            continue
        out.append((str(path), stat.st_mtime_ns, stat.st_size))
    out.sort()
    return tuple(out)


def project_transitions(project_root, refresh_seconds: float = None) -> dict:
    """
    项目里 `define` 的转场 → `{名字: Effect}`（**按项目根 + mtime 缓存**）。

    扫描口径与 `transition_catalog.scan_project_transitions` 完全一样，只多
    两层缓存：
      1. 模块级缓存：0.1s 的扫描只落在"第一次 / 项目真的改了"上；
      2. 签名复核（路径 + mtime + 大小）最多每 `refresh_seconds` 秒一次，
         改完 `.rpy` 不会一直拿旧效果演。

    没有 `project_root` → 空表（调用方按"项目里没有 define 过"处理）。
    """
    if not project_root:
        return {}
    try:
        root = str(Path(str(project_root)))
    except (TypeError, ValueError):
        return {}
    try:
        ttl = float(refresh_seconds if refresh_seconds is not None
                    else _TRANSITION_REFRESH_SECONDS)
    except (TypeError, ValueError):
        ttl = _TRANSITION_REFRESH_SECONDS
    now = time.monotonic()
    cached = _TRANSITION_CACHE.get(root)
    # 用 `<` 而不是 `<=`：`refresh_seconds=0` 要**每次都复核签名**（测试/探针），
    # 而 Windows 的 monotonic 分辨率较粗，背靠背两次调用可能差 0.0。
    if cached is not None and ttl > 0 and (now - cached[1]) < ttl:
        return cached[2]
    signature = _transition_signature(project_root)
    if cached is not None and cached[0] == signature:
        _TRANSITION_CACHE[root] = (signature, now, cached[2])
        return cached[2]
    try:
        catalog = scan_project_transitions(project_root)
    except Exception as e:                    # noqa: BLE001 - 扫不动就当没有
        swallow('app.stage.project_transitions', e)
        catalog = {}
    _TRANSITION_CACHE[root] = (signature, now, catalog)
    return catalog


def clear_transition_cache() -> None:
    """清空转场目录缓存（测试 / 换项目时用；正常使用不需要调）。"""
    _TRANSITION_CACHE.clear()


def _beat_transitions_of(beat, project_root=None) -> tuple:
    """一拍 → 过场调用元组（派生失败按"没有过场"处理，不崩舞台）。"""
    try:
        calls = list(beat_transitions(beat))
    except Exception as e:                    # noqa: BLE001 - 派生失败不该拦预览
        swallow('app.stage.beat_transitions', e)
        calls = []
    # ⭐ **项目声明过的复杂子句**（2026-10-30）：`mycue … with Dissolve(0.3)`
    # 里的 `with` 不是 `Beat.with_`（自定义语句的头行在 `spec_head`），
    # 所以 `beat_transitions` 看不到它。声明里 `kind='transition'` 的子句
    # 由这里补一路 —— 认不出/没声明就一条都不加（不猜）。
    if project_root and getattr(beat, 'spec_head', ''):
        try:
            calls.extend(_declared_transition_calls(beat, project_root))
        except Exception as e:                # noqa: BLE001 - 声明坏了不该拦预览
            swallow('app.stage.declared_transition_clauses', e)
    if project_root and getattr(beat, 'code', ''):
        try:
            calls.extend(_declared_call_transitions(beat, project_root))
        except Exception as e:                # noqa: BLE001 - 同上
            swallow('app.stage.declared_call_transitions', e)
    return tuple(calls)


def _declared_transition_calls(beat, project_root) -> list:
    """`spec_head` + 项目声明 → `[TransitionCall, ...]`（只读、不猜）。"""
    from dataclasses import replace

    from storygraph.core.vocabulary import statement_head_token
    from storygraph.story.spec_clauses import (clause_value,
                                               declared_transition_clauses)
    from storygraph.story.statement_registry import StatementRegistry
    from storygraph.story.transition_expr import (SOURCE_CLAUSE,
                                                  parse_with_clause)

    head = statement_head_token(str(getattr(beat, 'spec_head', '') or ''))
    if not head:
        return []
    spec = StatementRegistry.for_project(project_root).match(head)
    if spec is None:
        return []
    calls = []
    for clause in declared_transition_clauses(getattr(spec, 'clauses', None)):
        value = clause_value(str(getattr(beat, 'spec_head', '') or ''), clause)
        if not value.strip():
            continue
        for call in parse_with_clause(value):
            calls.append(replace(call, source=SOURCE_CLAUSE, line=0))
    return calls


def _declared_call_transitions(beat, project_root) -> list:
    """
    项目声明的**技术调用** → `[TransitionCall, ...]`（只读、不猜）。

    包装函数本身不是转场。项目在 `rules.json` 里写：

        {"calls": [{"head": "my_wipe", "effect": "Dissolve",
                    "seconds_arg": 1, "columns": [...]}]}

    才按 `Dissolve(<第 1 个参数>)` 演；**没写 effect 的**仍然列出来（卡片
    看得到"这里有一次调用"），但效果由 `effect_of` 如实标"认不出" ——
    绝不假装淡入。
    """
    from storygraph.story.call_expr import find_calls
    from storygraph.story.statement_registry import StatementRegistry
    from storygraph.story.transition_expr import (SOURCE_CALL,
                                                  TransitionCall)

    code = str(getattr(beat, 'code', '') or '')
    if not code.strip():
        return []
    reg = StatementRegistry.for_project(project_root)
    calls = []
    for spec in (getattr(reg, 'calls', None) or ()):
        head = str(spec.get('head') or '')
        hits = find_calls(code, head)
        if len(hits) != 1:
            continue                         # 多条/没有：不猜改哪条
        hit = hits[0]
        effect = str(spec.get('effect') or '').strip()
        expr = hit.expr
        name = hit.head
        if effect:
            name = effect
            seconds = spec.get('seconds_arg')
            expr = effect
            if seconds is not None:
                arg = hit.arg(position=seconds)
                if arg is not None and str(arg.value).strip():
                    value = str(arg.value).strip()
                    if value[0].isdigit() or value[0] == '.':
                        expr = f'{effect}({value})'
        calls.append(TransitionCall(
            expr=expr, name=name, args=(), kwargs={}, layer='', line=0,
            source=SOURCE_CALL))
    return calls


def build_frames(beats: list, registry=None, transform_index=None,
                 screen=None, project_root=None) -> list:
    """
    Beat 序列 → `StageFrame` 列表（**纯函数，不碰 tkinter**）。

    舞台状态全部来自 `snapshot_rows`（与生成/快照表同源）；
    这里只把 transform 换成屏幕位置、并把"谁在说话"标出来。

    `transform_index` / `screen`（用户 2026-10-05："transform 未对应到舞台真实
    位置"）：项目 `transform.rpy` 里解析出来的定义表 + 游戏分辨率，交给
    `story.atl.placement_of` 算出**真实像素位置与缩放**（`SpriteLayout.place`）。
    不传时 `placement_of` 仍会按内置 transform / 位置词 / 名字首字母给一个
    合理值（**永远不返回 None**），行为与以前完全一致。

    `project_root`（2026-10-30，③ 过场接入）：项目根。传了才查项目
    `define` 的转场目录（`project_transitions`，带缓存），自定义转场名
    （`with close_eyes`）才能翻成"怎么演"；不传时裸构造器照常，自定义名是
    `unknown` —— 与"没载入项目"的语义一致，不猜。
    """
    if not beats:
        return []
    from storygraph.story.atl import DEFAULT_SCREEN

    size = tuple(screen) if screen else DEFAULT_SCREEN
    rows = snapshot_rows(beats)
    return [_frame_of(beat, row, i, registry, transform_index, size,
                      project_root=project_root)
            for i, (beat, row) in enumerate(zip(beats, rows))]


def build_frame_at(beats: list, index: int, registry=None,
                   transform_index=None, screen=None,
                   project_root=None):
    """
    **只算第 `index` 拍那一帧**（卡片上的「这一拍的舞台图」用）。

    与 `build_frames(beats)[index]` 逐字段相同，只是不必把整章算完：
    快照是**前缀递推**（第 i 拍的状态 = 前 i-1 拍累加 + 这一拍的变化，见
    `stage_snapshots`），所以只喂 `beats[:index + 1]` 的最后一行就够了。

    为什么值得单独开一个入口（而不是让卡片调 `build_frames` 再取一格）：
    卡片每翻一拍 / 每存一次就要这张图，而 `build_frames` 会把**整章**的
    transform 位置都算一遍（真机 300 拍 ≈ 40ms）。O(index) 已经够快，
    再省掉后面几百拍的位置计算。

    参数非法（越界 / 空章）返回 `None` —— 调用方据此显示"没有画面"，
    **不编一帧假的**。

    `project_root`：同 `build_frames`（查项目转场目录，自定义转场名才解析得准；
    卡片那张缩略图不需要过场，不传也不影响画面）。
    """
    if not beats:
        return None
    from storygraph.story.atl import DEFAULT_SCREEN

    i = int(index)
    if i < 0 or i >= len(beats):
        return None
    size = tuple(screen) if screen else DEFAULT_SCREEN
    rows = snapshot_rows(beats[:i + 1])
    if not rows:
        return None
    return _frame_of(beats[i], rows[-1], i, registry, transform_index, size,
                     project_root=project_root)


def _frame_of(beat, row, index: int, registry, transform_index,
              size, project_root=None) -> 'StageFrame':
    """一行快照 + 一拍 → 一帧（`build_frames` / `build_frame_at` 共用）。"""
    from storygraph.story.atl import placement_of

    speaker = (getattr(beat, 'speaker', '') or '').strip()
    ch = {}
    if speaker:
        try:
            ch = (getattr(registry, 'characters', {}) or {}).get(speaker) or {}
        except Exception as e:                # noqa: BLE001
            swallow('app.stage.build_frames.character', e)
            ch = {}
    sprites = []
    for order, (img, at) in enumerate(
            (row.get('state', {}).get('actors') or [])):
        tag = (img or '').split()[0] if img else ''
        place = placement_of(at, transform_index, registry, size)
        sprites.append(SpriteLayout(
            image=img or '', tag=tag, at=at or '',
            x=place.x, speaking=False, place=place,
            order_index=order))
    # 谁在说话：优先角色变量的 ImageTag，其次说话人本身就是 tag。
    # **不猜**（匹配不上就都不高亮）——高亮打错人比不高亮更糟。
    if speaker:
        spk_tag = str(ch.get('ImageTag') or '').strip()
        hit = spk_tag or speaker
        for s in sprites:
            if s.tag and s.tag == hit:
                s.speaking = True
    # 正在说话的排前面（与卡片一致）。`order_index` 保留了原始叠放顺序，
    # 真实渲染按它画（说话人高亮不能改变遮挡关系）。
    sprites.sort(key=lambda s: (not s.speaking, s.x))

    # 舞台状态全部来自快照 state（与产物同源）：
    #   · `scene_at` 是**持续状态**（`scene bg X at bgfit` 之后一直带着），
    #     回退到当前拍的 `beat.scene_at` 只为了兼容"单拍手工构造"的老调用方；
    #   · `layers_at` 是 2026-10-30 ⑦ P0 新增的图层变换表（tag → at）。
    state = row.get('state') or {}
    scene_at = str(state.get('scene_at')
                   or getattr(beat, 'scene_at', '') or '')
    layers_at = dict(state.get('layer_at') or {})
    # 出处（2026-10-31）：-1 / 缺键 = 不知道（跨章继承、手工构造的帧）。
    # 0 是合法值（第 0 拍写的）—— 别用 `or -1` 把它吞成"不知道"。
    def _src(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return -1

    scene_src = _src(state.get('scene_src'))
    layer_src = {str(k): _src(v)
                 for k, v in (state.get('layer_src') or {}).items()}

    # 过场（2026-10-30，③）：三条来路（inline with / 独立 with /
    # `$ renpy.transition(...)`）都派生，再翻成"怎么演"。
    # 目录只在有项目根时查；没有项目的空目录也能解析裸构造器（不猜名字）。
    transitions = _beat_transitions_of(beat, project_root)
    effects = ()
    if transitions:
        try:
            catalog = project_transitions(project_root) if project_root else {}
            effects = tuple(effect_of(call, catalog) for call in transitions)
        except Exception as e:                # noqa: BLE001 - 解析失败不等于硬切
            swallow('app.stage.frame.transition_effects', e)
            effects = ()

    return StageFrame(
        index=index,
        line_id=(row.get('line_id', '')
                 or f'{getattr(beat, "label", "")}.{index + 1:04d}'),
        label=row.get('label', '') or '',
        speaker=speaker,
        speaker_name=str(ch.get('Name') or ''),
        speaker_color=str(ch.get('Color') or ''),
        what_color=str(ch.get('WhatColor') or ''),
        text=row.get('text', '') or '',
        scene=row.get('scene', '') or '',
        scene_at=scene_at,
        sprites=sprites,
        layers=row.get('layers', '') or '',
        layers_at=layers_at,
        scene_src=scene_src,
        layer_src=layer_src,
        music=row.get('music', '') or '',
        sound=row.get('sound', '') or '',
        voice=row.get('voice', '') or '',
        changes=row.get('display_changes', '') or '',
        with_=str(getattr(beat, 'with_', '') or ''),
        pause=_pause_seconds_of(beat),
        transitions=transitions,
        effects=effects)


def _pause_seconds_of(beat) -> float:
    """
    这一拍有没有 `pause N`（秒）。

    为什么从 `raw` 里读而不是加字段：`pause` 现在走的是"原生语句"通道
    （`raw` 里逐字存着），加一个 Beat 字段会动表契约（列/指纹/快照）。
    这里只取第一个 `pause` 的数字 —— 拍里写第二条 pause 的情况极少，
    而"停多久"只是预览节奏，不影响产物。
    """
    import re as _re
    text = str(getattr(beat, 'raw', '') or '')
    m = _re.search(r'(?m)^\s*pause\s+(\d+(?:\.\d+)?)', text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return 0.0
    return 0.0


def frame_summary(frame: StageFrame) -> str:
    """一帧的一句话摘要（给状态栏/日志用）。"""
    bits = [f'#{frame.index + 1}', frame.line_id]
    if frame.scene:
        bits.append(frame.scene)
    if frame.sprites:
        bits.append('、'.join(s.image for s in frame.sprites))
    if frame.music:
        bits.append(f'♪ {frame.music}')
    return ' · '.join(b for b in bits if b)


def placement_notes(frame: StageFrame) -> list:
    """
    这一拍里**位置是猜的**有哪些 → `['ling happy：这个 transform 含 parallel，位置是猜的']`。

    用户 2026-10-19（B1）：

    > 现在的问题不是"算不出"，是"算不出还不说"，那比算错更糟。

    只报"不可靠"的那些（`Placement.uncertain` 非空）；有把握时返回空表 ——
    到处标警告等于没警告。纯函数（不碰 Qt），横条与测试共用这一份文字。
    """
    out = []
    for s in list(getattr(frame, 'sprites', ()) or ()):
        note = str(getattr(getattr(s, 'place', None), 'uncertain', '') or '').strip()
        if not note:
            continue
        who = str(getattr(s, 'image', '') or getattr(s, 'tag', '') or '').strip()
        out.append(f'{who}：{note}' if who else note)
    return out


# ===================== 人工写回 at（P2，2026-10-30） =====================
#
# 用户拍板（二轮）：
#   · **工具不自动写 at**（预览必须真实：没有 at 就按引擎裁切 + 提示）；
#   · 但**经人手**可以选一个 transform 写回（diff 确认、可撤销）；
#   · 只允许引用**项目里已有**的 transform（不新增定义、不猜语义）。
#
# 这些函数只改**内存里的 `Beat`**：落盘/导出仍走既有 `patch_export` 门禁
# （diff 确认 + 撤销）。所以它们**绝不直接写 .rpy**，也不碰用户字节。


def at_transform_candidates(project_root=None, transform_index=None,
                            screen=None) -> list:
    """
    项目里**已有**、可以挂到背景/图层上的 transform → 给 UI 的候选表。

    返回 `[{'name', 'label', 'fit', 'zoom', 'changes_size'}, …]`：
      · `changes_size` = 这条 transform 真的会改尺寸（fit/xsize/ysize/zoom）；
      · 排序：会改尺寸的在前（用户要"调大小"），同组按名字。
    只读项目源码（`scan_transforms`），不写盘。
    """
    from storygraph.story.atl import (DEFAULT_SCREEN, placement_of,
                                      scan_transforms)
    index = transform_index if transform_index is not None \
        else (scan_transforms(project_root) if project_root else {})
    index = index or {}
    screen_pair = tuple(screen) if screen else DEFAULT_SCREEN
    out = []
    for name in sorted(index):
        try:
            place = placement_of(name, index, screen=screen_pair)
        except Exception as e:                 # noqa: BLE001 - 坏定义跳过
            swallow('app.stage.at_transform_candidates', e)
            continue
        fit = str(getattr(place, 'fit', '') or '')
        xz = float(getattr(place, 'xzoom', 1.0) or 1.0)
        yz = float(getattr(place, 'yzoom', 1.0) or 1.0)
        xs = getattr(place, 'xsize', None)
        ys = getattr(place, 'ysize', None)
        changes_size = bool(fit or xs is not None or ys is not None
                            or abs(xz - 1.0) > 1e-6
                            or abs(yz - 1.0) > 1e-6)
        bits = []
        if fit:
            bits.append(f'fit "{fit}"')
        if xs is not None:
            bits.append(f'xsize {float(xs):g}')
        if ys is not None:
            bits.append(f'ysize {float(ys):g}')
        if abs(xz - yz) < 1e-6:
            if abs(xz - 1.0) > 1e-6:
                bits.append(f'zoom {xz:g}')
        else:
            bits.append(f'xzoom {xz:g}，yzoom {yz:g}')
        if not bits and getattr(place, 'x_px', None) is not None:
            bits.append('位置')
        out.append({
            'name': name,
            'fit': fit,
            'zoom': xz,
            'changes_size': changes_size,
            'label': f'{name}（{"，".join(bits)}）' if bits else name,
        })
    out.sort(key=lambda e: (not e['changes_size'], e['name']))
    return out


@dataclass
class AtEdit:
    """
    一次"人工应用 transform"的**计划**（P2）。

    `apply(beat)` / `revert(beat)` 只改内存；写盘仍走既有补丁门禁。
    字段里的 `rpy_before/rpy_after` 是**生成产物**的逐行 diff（确认对话框用）。
    """

    target: str = 'scene'          # 'scene'（背景 scene_at）| 'layer'（show at）
    tag: str = ''                  # target='layer' 时的图层 tag（cg/ui…）
    transform: str = ''
    #: 这次是**去掉**变换（预设「原样」）：`transform` 为空、写回时清成 `''`
    #: 或删掉 show 条目里的 `@at`。与"选了一个空名 transform"不是一回事。
    clear: bool = False
    beat_index: int = -1
    #: 请求时选中的拍（用户当时看的那一拍）。与 `beat_index` 不同 = 这次改的是
    #: **更早那一行**（背景/图层是继承来的，尺寸挂在最初写它的那一拍上）。
    requested_index: int = -1
    #: 会被这次改动影响的拍区间 `(起, 止)`（0 基闭区间；空 = 不知道）。
    #: 给人看的"影响第 41–266 拍（共 226 拍）"就是它 —— Unity Prefab 的
    #: "override 必须能看到影响面"那一条。
    affects: tuple = ()
    _beat: object = field(default=None, repr=False, compare=False)
    field: str = 'scene_at'        # 'scene_at' | 'show'
    before: str = ''
    after: str = ''
    rpy_before: str = ''
    rpy_after: str = ''
    reason: str = ''               # 非空 = 拒绝（不写）
    #: 插入新 show 条目时要一并补的逐条令牌（`show#N`）与它所在的字段。
    #: 不补的话快照只认 `op_order` 点到的那几条 —— 真机实测：
    #: 脚本里多了 `show bg …`，卡片与预览纹丝不动（渲染器却会补出来）。
    extra_token: str = ''
    token_field: str = ''          # 'op_order' | 'stage_order'
    #: 本景"还有几处单独改写"（`story.override_advice.OverrideAdvice`，只读）。
    #: 确认框里"改源只到第 12 拍有效"就是它 —— 没有它，人看到 diff 里一行改好
    #: 就以为整景都改好了（真机最常见的"改完没生效"）。
    #: `None` = 没算/算不出（不显示，绝不冒充"0 处"）。
    advice: object = None

    @property
    def ok(self) -> bool:
        return not self.reason and self.before != self.after

    @property
    def target_label(self) -> str:
        if self.target == 'layer':
            if self.clear:
                return f'图层 {self.tag}（原样：去掉 at）'
            return f'图层 {self.tag}（show at）'
        if self.clear:
            return '背景（原样：去掉 at）'
        if self.field == 'show':
            # 「从这一拍起」= 只写 show：替换同 tag 的背景，**不清立绘**
            return '背景（show 改写，不清立绘）'
        return '背景（scene_at）'

    def _get_beat(self, beat=None):
        return beat if beat is not None else self._beat

    def advice_lines(self) -> list:
        """
        确认框里"本景还有 N 处单独改写"那几行（只读提示，`[]` = 不说）。

        **只说事实，不拦**：它不改变这个计划能不能写。这里的措辞与尺寸面板
        用的是**同一个** `OverrideAdvice.text`（`docs/超清CG与BG_零侵入导入_
        2026-10-31.md` §5.5：报的数字必须和实际改的数字同源）。

        算不出来（`unknown`）时也要说 —— "统计不出"和"没有"是两件事。
        """
        advice = self.advice
        if advice is None or self.reason:
            return []
        try:
            if self.target == 'layer':
                from storygraph.core.vocabulary import LAYER_CN_TAG
                label = LAYER_CN_TAG.get(str(self.tag or ''), str(self.tag or ''))
            else:
                label = '背景'
            text = str(advice.text(new_at=self.after, label=label) or '')
        except Exception as e:                 # noqa: BLE001 - 提示坏了不拦确认框
            swallow('app.stage.AtEdit.advice_lines', e)
            return []
        return [line for line in text.splitlines() if line.strip()]

    def apply(self, beat=None) -> bool:
        """写进内存里的 Beat；成功返回 True（不改 .rpy）。"""
        beat = self._get_beat(beat)
        if not self.ok or beat is None:
            return False
        setattr(beat, self.field, self.after)
        if self.extra_token and self.token_field:
            cur = str(getattr(beat, self.token_field, '') or '')
            setattr(beat, self.token_field,
                    f'{cur},{self.extra_token}' if cur else self.extra_token)
        return True

    def revert(self, beat=None) -> bool:
        """撤销：把字段还原成计划时的值（可重复调用）。"""
        beat = self._get_beat(beat)
        if beat is None:
            return False
        setattr(beat, self.field, self.before)
        if self.extra_token and self.token_field:
            cur = str(getattr(beat, self.token_field, '') or '')
            if cur == self.extra_token:
                setattr(beat, self.token_field, '')
            elif cur.endswith(',' + self.extra_token):
                setattr(beat, self.token_field,
                        cur[:-(len(self.extra_token) + 1)])
        return True

    def diff_text(self) -> str:
        """给人看的 diff（确认对话框正文）。"""
        what = ('原样：去掉 at' if self.clear
                else f'transform「{self.transform}」')
        lines = [f'第 {self.beat_index + 1} 拍 · {self.target_label} · {what}',
                 f'字段：{self.field}']
        if (self.requested_index >= 0
                and self.requested_index != self.beat_index):
            lines.insert(0, f'改的是第 {self.beat_index + 1} 拍'
                            f'（你在第 {self.requested_index + 1} 拍点的）')
        if self.affects:
            first, last = int(self.affects[0]), int(self.affects[1])
            lines.append(f'影响：第 {first + 1}–{last + 1} 拍'
                         f'（共 {last - first + 1} 拍）')
        if self.reason:
            lines.append(f'不能写：{self.reason}')
            return '\n'.join(lines)
        # 「本景还有 N 处单独改写」——**先说会被谁盖回去**，再说 diff。
        # 位置在 diff 之前是刻意的：diff 里只有一行，光看它看不出"只到第 12 拍"。
        lines.extend(self.advice_lines())
        try:
            import difflib
            diff = list(difflib.unified_diff(
                (self.rpy_before or '').splitlines(),
                (self.rpy_after or '').splitlines(),
                fromfile='现在', tofile='应用后', lineterm='', n=1))
        except Exception as e:                 # noqa: BLE001 - diff 失败退字段对比
            swallow('app.stage.AtEdit.diff_text', e)
            diff = []
        if diff:
            lines.extend(diff)
        else:
            lines.extend([f'- {self.before or "（空）"}',
                          f'+ {self.after or "（空）"}'])
        lines.append('只改内存里的 Beat；保存/导出仍走既有 diff 确认门禁，'
                     '可撤销。')
        return '\n'.join(lines)


@dataclass
class SceneEdit(AtEdit):
    """
    一次**换景**（`scene`）的计划（2026-10-31）—— `AtEdit` 的子类。

    为什么不是给 `AtEdit` 加一个 `mode`：换景要动的**不止一个字段**：

      · `beat.scene` / `beat.scene_at`（这一景 + 它的变换）；
      · 同一拍里那条**会把它盖掉**的 `show bg …`（留着的话，
        `show` 排在 `scene` 后面，产物里的背景是**旧那张**）；
      · 顺序列（`op_order` / `stage_order`）：本来没有 scene 的拍要补一个
        **排在最前面**的 `scene` 令牌，否则渲染器把缺的 verb 补在末尾
        （`show → scene` = 人刚上台又被清掉）。

    它还多记两件**必须让人看见**的事：这次会清掉台上哪些立绘、哪些图层
    （Ren'Py 的 `scene` 清的是**整个 master 层**）。确认框里那句话不是
    装饰 —— 用户 2026-10-31 的原话就是"确认框写清会清掉台上 N 个立绘"。
    """

    #: 新背景（`bg bg9_5`）
    image: str = ''
    scene_before: str = ''
    scene_at_before: str = ''
    scene_at_after: str = ''
    #: 同一拍里被去掉的 `show bg …`（写法原样，供确认框展示）
    show_before: str = ''
    show_after: str = ''
    removed_show: str = ''
    #: 补 scene 令牌时动的那一列（`op_order` / `stage_order` / `''`）
    order_field: str = ''
    order_before: str = ''
    order_after: str = ''
    #: 会被清掉的：立绘图名、图层图名（都来自**这一拍开始前**的舞台状态）
    cleared_sprites: tuple = ()
    cleared_layers: tuple = ()
    #: 这一拍本来没有 `scene` —— 这是一条**新写的**换景语句
    added: bool = False
    #: 挑的还是**同一张**背景（只是这一拍本来没写 scene）—— 那么这条 scene
    #: 唯一的作用就是清台；确认框必须这么说，别让人以为图变了。
    same_image: bool = False
    #: 这一拍自己还有 `show`/`hide` 排在 `scene` 之前（作者原来的写法，
    #: 本工具不动；只在确认框里说一声"它们会被一起清掉"）
    shows_before_scene: bool = False

    @property
    def target_label(self) -> str:
        # 别在这里再写一遍"换景"：台账/日志的前缀已经是 `[换景]` 了
        # （真机第一次跑就露出 `[换景] 已换景：… · 换景（scene，会清台）`）。
        return '新写一条 scene（会清台）' if self.added else '改这一行的 scene'

    def apply(self, beat=None) -> bool:
        """写进内存里的 Beat；成功返回 True（不改 .rpy）。"""
        beat = self._get_beat(beat)
        if not self.ok or beat is None:
            return False
        beat.scene = self.image
        beat.scene_at = self.scene_at_after
        if self.show_before != self.show_after:
            beat.show = self.show_after
        if self.order_field and self.order_before != self.order_after:
            setattr(beat, self.order_field, self.order_after)
        return True

    def revert(self, beat=None) -> bool:
        """撤销：三个字段逐字还原（可重复调用）。"""
        beat = self._get_beat(beat)
        if beat is None:
            return False
        beat.scene = self.scene_before
        beat.scene_at = self.scene_at_before
        if self.show_before != self.show_after:
            beat.show = self.show_before
        if self.order_field:
            setattr(beat, self.order_field, self.order_before)
        return True

    def diff_text(self) -> str:
        """给人看的 diff（确认对话框正文）—— 先写清**会清掉什么**。"""
        head = [f'第 {self.beat_index + 1} 拍 · {self.target_label}']
        if self.added:
            head.append('这一拍本来没有 scene —— 会**新写一条**换景语句。')
        head.append(f'换到：{self.image}'
                    + (f'（贴在 {self.scene_at_after} 上）'
                       if self.scene_at_after else ''))
        if self.cleared_sprites:
            names = '、'.join(self.cleared_sprites[:6])
            more = (f' 等 {len(self.cleared_sprites)} 个'
                    if len(self.cleared_sprites) > 6 else '')
            head.append(f'会清掉台上 {len(self.cleared_sprites)} 个立绘：'
                        f'{names}{more}')
        else:
            head.append('台上没有立绘（这一条不会带走人）')
        if self.cleared_layers:
            head.append(f'还会清掉 {len(self.cleared_layers)} 个 CG/UI 图层：'
                        f'{"、".join(self.cleared_layers[:6])}')
        if self.shows_before_scene:
            head.append('⚠ 这一拍自己的 show/hide 里有排在 scene 之前的 —— '
                        '它们仍会被这条 scene 清掉（写法没动）')
        if self.same_image:
            head.append('⚠ 背景还是**同一张**（「' + self.image + '」）—— 这条 '
                        'scene 唯一的作用就是**把台上清空**；要是只想改尺寸，'
                        '请用「改」。')
        if self.removed_show:
            head.append(f'同一拍里的 `show {self.removed_show}` 会去掉 —— '
                        '它排在 scene 之后，会把新背景盖回旧那张')
        head.append('要**保留**立绘请改用「尺寸」里的「改」（那写的是 show，'
                    '只替换同 tag 的背景，不清台）。')
        if self.affects:
            first, last = int(self.affects[0]), int(self.affects[1])
            head.append(f'影响：第 {first + 1}–{last + 1} 拍'
                        f'（共 {last - first + 1} 拍）')
        if self.reason:
            head.append(f'不能写：{self.reason}')
            return '\n'.join(head)
        try:
            import difflib
            diff = list(difflib.unified_diff(
                (self.rpy_before or '').splitlines(),
                (self.rpy_after or '').splitlines(),
                fromfile='现在', tofile='应用后', lineterm='', n=1))
        except Exception as e:                 # noqa: BLE001 - diff 失败退字段对比
            swallow('app.stage.SceneEdit.diff_text', e)
            diff = []
        if diff:
            head.extend(diff)
        else:
            head.extend([f'- scene {self.scene_before}'.rstrip(),
                         f'+ scene {self.image}'.rstrip()])
        head.append('只改内存里的 Beat；保存/导出仍走既有 diff 确认门禁，'
                    '可撤销。')
        return '\n'.join(head)


def _same_layer_tag(a: str, b: str) -> bool:
    """
    两个 tag 是不是**同一个图层**（背景的三种写法算同一个）。

    `bg` / `background` / `bg_` 在 Ren'Py 里是同一个背景图层的不同 tag 写法
    （词表 `NON_ACTOR_TAGS` 三种都收）。换景时判"同一拍里有没有会盖掉它的
    背景 show"，只比字符串会把 `show background old` 漏掉 —— 那条留在
    scene 后面，产物里的背景就是**旧那张**。

    ⚠ 判定本体已收敛到 `core.vocabulary.same_layer_tag`（2026-10-31）：
    `story.override_advice` 数"本景有几处单独改写"用的是**同一条**规矩 ——
    两份各写一遍，"数出来的处数"和"实际会盖回去的处数"迟早对不上。
    """
    return same_layer_tag(a, b)


def _show_part_info(part: str) -> dict:
    """
    一条 `show` 子句（`split_stage_items` 的一段）→ 简单判断。

    返回 `{'part','image','at','kind'}`；`kind` 是：
      · `simple` —— 紧凑 `图@变换` 或纯图名（**能安全改写**）；
      · `complex` —— 含 `behind/with/as/onlayer/zorder` 等修饰（拒绝改写）；
      · `expression` —— `show expression …`（图像名是表达式，拒绝改写）。

    ⚠ 刻意**不** import `story.beat_model._split_show_items`（跨包私有名，
    `test_package_layering` 不允许）：这里只需要"能不能安全改 @at"，用
    公开词表 `core.vocabulary` 的同一套关键字即可，不复制完整解析。
    """
    from storygraph.core.vocabulary import (SHOW_CLAUSE_KEYWORDS,
                                            SHOW_INTRO_KEYWORDS,
                                            classify_show_words)
    part = str(part or '').strip()
    if not part:
        return {}
    if part.startswith('expression') and (
            len(part) == len('expression')
            or part[len('expression')].isspace()):
        return {'part': part, 'image': '', 'at': '', 'kind': 'expression'}
    words = part.split()
    if any(w in SHOW_CLAUSE_KEYWORDS or w in SHOW_INTRO_KEYWORDS
           for w in words):
        try:
            target = classify_show_words(part, intros=()).target or ''
        except Exception as e:                 # noqa: BLE001 - 认不出就按复杂处理
            swallow('app.stage._show_part_info', e)
            target = ''
        return {'part': part, 'image': str(target), 'at': '',
                'kind': 'complex'}
    if '@' in part:
        image, _, at = part.partition('@')
        return {'part': part, 'image': image.strip(), 'at': at.strip(),
                'kind': 'simple'}
    return {'part': part, 'image': part, 'at': '', 'kind': 'simple'}


def _layer_show_edit(beat, tag: str, transform: str):
    """
    `target='layer'` 的改写：只动**简单** show 条目的 `@at`。

    复杂子句（`behind` / `with` / `expression`）**拒绝**：那是"不猜语义"，
    让用户去高级区 Stage 手改（与预览其它地方同一条规矩）。

    `transform=''` = 预设「原样」：**删掉这条的 `@at`**（不写 `@`）；
    本来就没有 at 时拒绝（"已经是原样了"），不生成一行没变化的 show。

    返回 `(new_show, before_at, reason)`；reason 非空 = 拒绝。
    """
    from storygraph.core.vocabulary import NON_ACTOR_TAGS, split_stage_items

    tag = str(tag or '').strip()
    if not tag:
        return '', '', '没有指定图层 tag'
    if tag not in NON_ACTOR_TAGS or tag in ('bg', 'background', 'bg_'):
        return '', '', f'「{tag}」不是非角色图层（立绘位置请走「位置」入口）'
    if not str(getattr(beat, 'show', '') or '').strip():
        return '', '', '这一拍没有 show 图层'
    parts = split_stage_items(beat.show)
    infos = [_show_part_info(p) for p in parts]
    hit = -1
    for i, info in enumerate(infos):
        if not info:
            continue
        image_parts = str(info.get('image') or '').split()
        image_tag = image_parts[0] if image_parts else ''
        if image_tag != tag:
            continue
        if info.get('kind') == 'expression':
            return '', '', 'expression 形态的 show 不能安全改写，请在高级区 Stage 改'
        if info.get('kind') == 'complex':
            return '', '', ('这条 show 是复杂子句（含 behind/with 等），'
                            '请在「高级区 · Stage」里手改；预览不猜')
        hit = i
    if hit < 0:
        return '', '', f'这一拍没有图层「{tag}」的 show'
    part = parts[hit]
    if '@' in part:
        new_part = part.rpartition('@')[0].rstrip()
    else:
        new_part = part.strip()
    if str(transform or '').strip():
        new_part = new_part + '@' + str(transform).strip()
    elif '@' not in part:
        return '', '', '这一行的图层已经是原样了（没有 at）'
    new_show = '; '.join([*parts[:hit], new_part, *parts[hit + 1:]])
    before_at = str(infos[hit].get('at') or '')
    return new_show, before_at, ''


def _bg_show_edit(beat, image: str, transform: str):
    """
    「从这一拍起改这一景」的**唯一写法：`show`，绝不是 `scene`**。

    Ren'Py 官方（`scene` / `show` 两节）：
      · `scene` = **移除该层全部 displayable**，再显示一张图 —— 台上立绘全没；
      · `show` = 只替换**同 tag** 的那张图，别的 displayable 不动。

    所以"把背景调大一点"只能写 `show bg room at sg_fit_cover`；写成
    `scene bg room at ...` 玩家会看到人突然消失。这一条 2026-10-31 由用户
    指出来（"上一轮我错了：scene 不是改属性，是清台"），并由本函数钉死：
    **这个函数不会生成 `scene` 这两个字**。

    返回 `(new_show, before_at, reason, token)`：`token` 是
    `(字段名, 令牌)`（插入新条目时要补的 `show#N`），替换原位时为 `('', '')`。

    `transform=''` = 预设「原样」：**去掉这条 show 的 `@at`**（写成裸图名）。
    已有这条 show 但本来就没有 at → 拒绝（"已经是原样了"）。
    """
    from storygraph.core.vocabulary import (SHOW_CLAUSE_KEYWORDS,
                                            SHOW_INTRO_KEYWORDS,
                                            split_stage_items)

    image = str(image or '').strip()
    transform = str(transform or '').strip()
    words = image.split()
    tag = words[0] if words else ''
    if not tag:
        return '', '', '没有背景图名，不能改写', ('', '')
    parts = list(split_stage_items(str(getattr(beat, 'show', '') or '')))
    hit = -1
    for i, part in enumerate(parts):
        pw = str(part or '').split()
        if not pw or pw[0] != tag:
            continue
        if any(w in SHOW_CLAUSE_KEYWORDS or w in SHOW_INTRO_KEYWORDS
               for w in pw):
            return '', '', ('这条背景 show 是复杂子句（含 behind/with 等），'
                            '请在「高级区 · Stage」里手改；预览不猜'), ('', '')
        hit = i
    if hit >= 0:
        part = parts[hit]
        before_at = part.rpartition('@')[2].strip() if '@' in part else ''
        if '@' in part:
            new_part = part.rpartition('@')[0].rstrip()
        else:
            new_part = part.strip()
        if transform:
            new_part = new_part + '@' + transform
        elif '@' not in part:
            return '', '', '这一拍已经是原样了（没有 at）', ('', '')
        new_show = '; '.join([*parts[:hit], new_part, *parts[hit + 1:]])
        return new_show, before_at, '', ('', '')
    before_at = ''
    tail = image if not transform else f'{image}@{transform}'
    new_show = '; '.join([*parts, tail])
    return new_show, before_at, '', _show_token_append(beat, len(parts))


def _show_token_append(beat, index: int):
    """
    插入**新的** show 条目时，把它的逐条令牌补上。

    为什么必须补：`op_order` 里有点到具体下标的令牌（`show#0,code#0`）时，
    快照只应用令牌点到的那几条；新加的条目没有令牌 → 快照看不见它，
    渲染器却会（`_emit_leftover_stage` 补在末尾）→ "看到的 ≠ 生成的"。
    真机实测那 134 拍就是：脚本多了 `show bg …`，卡片/预览纹丝不动。

    返回 `(字段名, 令牌)`；不需要补（没有逐条令牌 / 只有 verb 级令牌）时
    返回 `('', '')` —— verb 级令牌本来就是"这个 verb 的行全出"。
    """
    raw = str(getattr(beat, 'op_order', '') or '')
    stage = str(getattr(beat, 'stage_order', '') or '')
    token = f'show#{int(index)}'
    if raw:
        return ('op_order', token) if '#' in raw else ('', '')
    if stage and '#' in stage:
        return 'stage_order', token
    return '', ''


def _valid_beat_index(value, total: int):
    """`value` 转成合法拍下标；认不出/越界 → None（**0 是合法值**）。"""
    try:
        i = int(value)
    except (TypeError, ValueError):
        return None
    return i if 0 <= i < total else None


def _scene_token_insert(beat) -> tuple:
    """
    给一个**本来没有 `scene`** 的拍补 `scene` 令牌 —— 位置在**第一条舞台
    指令之前**（一拍里没有舞台指令时就放最前：换景是本拍的第一件事）。

    为什么非补不可（与 `_show_token_append` 同一族坑，方向相反）：
    `Beat.op_tokens_full()` 对"有内容却没被任何令牌点到"的 verb 是**补在
    末尾**的。卡片上给一拍新写 `scene` 时 `op_order` 还是 `show#0,code#0`
    → 令牌序列变成 `show → code → scene`，产物就是"先把人 show 出来、
    紧接着 scene 把人全清掉" —— 作者要的是换背景，玩家看到的是人突然消失。
    补一个排在最前面的 `scene` 令牌，才与"换景 = 这一拍的第一件事"同义。

    返回 `(字段名, 新值)`；不需要补（没有任何顺序信息 / 已经有 scene 令牌）
    返回 `('', '')` —— 没有顺序信息时渲染器本来就按 scene→show→hide 出。
    """
    for field_name in ('op_order', 'stage_order'):
        text = str(getattr(beat, field_name, '') or '')
        toks = [t.strip() for t in text.split(',') if t.strip()]
        if not toks:
            continue
        verbs = [t.split('#')[0] for t in toks]
        if 'scene' in verbs:
            return '', ''
        pos = next((i for i, v in enumerate(verbs)
                    if v in ('scene', 'show', 'hide', 'with')), 0)
        return field_name, ','.join(toks[:pos] + ['scene'] + toks[pos:])
    return '', ''


def stage_before(beats, index: int):
    """
    这一拍**开始之前**台上有什么 → `{'scene','sprites','layers'}`。

    `None` = 算不出来（快照推导炸了）—— 调用方必须**明说"不知道"**，
    绝不当成"台上没人"（否则确认框会告诉用户"不会带走人"，然后带走了）。
    用 `snapshot_rows(beats[:index])` 的最后一帧，与预览/卡片同一份推导。
    """
    if index <= 0:
        return {'scene': '', 'sprites': [], 'layers': []}
    try:
        rows = snapshot_rows(list(beats)[:int(index)])
    except Exception as e:                     # noqa: BLE001 - 算不出就返回 None
        swallow('app.stage.stage_before', e)
        return None
    if not rows:
        return {'scene': '', 'sprites': [], 'layers': []}
    state = rows[-1].get('state') or {}
    return {
        'scene': str(state.get('scene') or ''),
        'sprites': [str(img) for img, _at in (state.get('actors') or ())
                    if str(img or '').strip()],
        'layers': [str(v) for v in (state.get('layers') or {}).values()
                   if str(v or '').strip()],
    }


def _scene_image_known(image: str, project_root) -> bool:
    """
    项目里**认不认得**这张图（写进脚本会不会 NameError）。

    判据三条，任一命中就算认得：脚本里的 `image` 声明（`script_images`
    认得文件与纯色）/ 磁盘上的图片文件（`image_index`）/ 两条都为空
    （这个项目既没声明也没图片目录 → **判不了，不拦**）。
    最后那条是"不猜"：索引是空的时候说"项目里没有这张图"才是在撒谎。
    """
    if not project_root:
        return True
    try:
        if script_image(image, project_root):
            return True
    except Exception as e:                     # noqa: BLE001 - 读不到声明就退磁盘
        swallow('app.stage._scene_image_known.script', e)
    try:
        index = image_index(project_root)
    except Exception as e:                     # noqa: BLE001 - 建不出索引就不拦
        swallow('app.stage._scene_image_known.index', e)
        return True
    if not index:
        return True
    return bool(image_index_lookup(image, index))


def _beat_touches_tag(beat, tag: str) -> bool:
    """这一拍有没有自己写了 `tag`（show / hide 任一条）。"""
    from storygraph.core.vocabulary import split_stage_items

    tag = str(tag or '').strip()
    if not tag:
        return False
    for field_name in ('show', 'hide'):
        text = str(getattr(beat, field_name, '') or '')
        for part in split_stage_items(text) if text else ():
            words = str(part or '').split()
            if words and words[0] == tag:
                return True
    return False


def _beat_show_has_tag(beat, tag: str) -> bool:
    """这一拍的 `show` 里有没有 `tag`（只看 show，不看 hide）。"""
    from storygraph.core.vocabulary import split_stage_items

    tag = str(tag or '').strip()
    if not tag:
        return False
    text = str(getattr(beat, 'show', '') or '')
    for part in split_stage_items(text) if text else ():
        words = str(part or '').split()
        if words and words[0] == tag:
            return True
    return False


def _affected_range(beats, start: int, *, kind: str, tag: str = '') -> tuple:
    """
    这次改动会影响到哪一段（0 基闭区间）：从它写下的那一拍到"被下一次覆盖之前"。

    为什么必须算（用户 2026-10-31 的第 2 条要求，Unity Prefab 的同一课）：
    「改这一景」改的是**一行**，却会影响后面所有继承它的拍 —— 确认框里不写
    "影响第 41–266 拍（共 226 拍）"，人就是在盲改。

    停止条件（与快照语义一致，不猜）：
      · 任何一拍自己写了 `scene` → 清台重设，前后都到此为止；
      · `kind='layer'` 时，下一拍又 show / hide 了这个 tag → 到此为止。
    """
    n = len(beats or ())
    if not 0 <= int(start) < n:
        return ()
    last = n - 1
    for j in range(int(start) + 1, n):
        beat = beats[j]
        if str(getattr(beat, 'scene', '') or '').strip():
            return (int(start), j - 1)
        if kind == 'layer' and _beat_touches_tag(beat, tag):
            return (int(start), j - 1)
        if kind == 'scene' and tag and _beat_touches_tag(beat, tag):
            # `show bg X at …` 也是换背景（快照把它归成 scene）—— 到此为止
            return (int(start), j - 1)
    return (int(start), last)


def plan_at_edit(beats, index: int, *, target: str = 'scene', tag: str = '',
                 transform: str = '', project_root=None, transform_index=None,
                 screen=None, source_index=None, mode: str = 'source',
                 scene_image: str = '') -> AtEdit:
    """
    计划一次"人工写回 at"（**只算，不动 Beat**；确认后调 `AtEdit.apply`）。

    规则（契约 §1.6 P2 / 用户拍板）：
      · 只引用**项目已有**的 transform（`scan_transforms` 查不到就拒绝）；
      · `target='scene'` → 写 `beat.scene_at`；`target='layer'` → 改该 show
        条目的 `@at`（复杂子句 / expression 拒绝）；
      · **背景/图层是持续状态**（2026-10-31）：本拍没写 `scene` / 这条 `show`
        时，按 `source_index`（快照的 `scene_src` / `layer_src`）去改**最初写下
        它的那一拍** —— 只认本拍字段的话，真机 1013 个有背景的拍里 972 个
        （96%）都会报"这一拍没有背景"，那个入口等于不可用；
      · 计划里带**生成产物 diff**（`render_beat_to_rpy` 前后）与**影响区间**
        （`affects`），供确认；
      · `mode='override'` = "从这一拍起改这一景"：**只写 `show`，绝不写
        `scene`**（`scene` 会清台，立绘全没 —— Ren'Py 官方语义）。`scene_image`
        是当前背景图名（本拍自己没写 scene 时用）；
      · `mode='clear'` = 预设「原样」（2026-10-31）：**去掉 at**，不是套一条
        空 transform —— `scene_at` 清成 `''` / show 条目删掉 `@at`。
        本来就没有 at 时拒绝（不生成一行没变化的 show）；
      · 只改内存 Beat —— 落盘走既有 patch/导出门禁，可撤销。
    """
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return AtEdit(reason='拍索引不合法', beat_index=-1)
    beats = list(beats or ())
    if idx < 0 or idx >= len(beats):
        return AtEdit(reason='没有这一拍（索引越界）', beat_index=idx)
    beat = beats[idx]
    src = _valid_beat_index(source_index, len(beats))
    mode = str(mode or 'source')
    clear = mode == 'clear'
    transform = str(transform or '').strip()
    if clear and transform:
        return AtEdit(reason='「原样」不能同时带 transform', beat_index=idx,
                      _beat=beat, transform=transform)
    if not clear and not transform:
        return AtEdit(reason='没有选择 transform', beat_index=idx, _beat=beat)
    from storygraph.story.atl import (DEFAULT_SCREEN, placement_of,
                                      scan_transforms)
    if clear:
        transform_index = {}
    else:
        if transform_index is None and project_root:
            transform_index = scan_transforms(project_root)
        transform_index = transform_index or {}
        if transform not in transform_index:
            return AtEdit(
                reason=f'项目里没有 transform「{transform}」（只允许引用项目已有的）',
                beat_index=idx, _beat=beat, transform=transform)
    screen_pair = tuple(screen) if screen else DEFAULT_SCREEN
    if not clear:
        try:
            place = placement_of(transform, transform_index, screen=screen_pair)
        except Exception as e:                 # noqa: BLE001 - 坏定义拒绝
            swallow('app.stage.plan_at_edit', e)
            place = None
        if place is None or not str(getattr(place, 'source', '')).startswith(
                ('atl', 'builtin', 'word', 'letter', 'center')):
            return AtEdit(reason=f'transform「{transform}」解析不出几何，拒绝写',
                          beat_index=idx, _beat=beat, transform=transform)

    if target == 'layer':
        target_i = idx
        hit = beat
        new_show, before_at, reason = _layer_show_edit(hit, tag, transform)
        # 这一拍没写这条 show（图层是继承来的）→ 去写最初那一拍。
        # 只在"本拍根本没有这条"时回退；"复杂子句 / expression" 是那一行的
        # 性质，换个拍重试也不该放过它。
        if reason.startswith('这一拍没有') and src is not None and src != idx:
            alt_show, alt_at, alt_reason = _layer_show_edit(
                beats[src], tag, transform)
            if not alt_reason:
                target_i, hit = src, beats[src]
                new_show, before_at, reason = alt_show, alt_at, ''
            else:
                reason = alt_reason
        elif reason.startswith('这一拍没有') and src is None:
            # 跨章继承的图层：没有"最初 show 它的那一拍"，不猜。
            reason = (f'图层「{tag}」找不到最初 show 它的那一拍（多半是上一章'
                      '继承来的），不能在这里改尺寸')
        before = str(getattr(hit, 'show', '') or '')
        edit = AtEdit(target='layer', tag=str(tag or '').strip(),
                      transform=transform, clear=clear, field='show',
                      before=before, after=new_show or before,
                      reason=reason, beat_index=target_i,
                      requested_index=idx, _beat=hit,
                      affects=(_affected_range(beats, target_i, kind='layer',
                                               tag=tag)
                               if not reason else ()))
        if not reason and edit.before == edit.after:
            edit.reason = (f'这一拍的图层「{tag}」已经是原样了'
                           if clear else f'这一拍的图层「{tag}」已经是这个变换了')
        if not edit.reason and before_at == transform:
            edit.reason = '已经是原样了' if clear else '已经是这个变换了'
    else:
        target_i = idx
        hit = beat
        scene = str(getattr(beat, 'scene', '') or '').strip()
        if (mode == 'override' and not scene):
            # 「从这一拍起」：只写 `show`，**绝不写 scene**（scene 是清台）。
            image = str(scene_image or '').strip()
            if not image:
                return AtEdit(reason='这一拍看不到背景，不能“从这一拍起”改尺寸',
                              beat_index=idx, _beat=beat, transform=transform)
            new_show, before_at, reason, tok = _bg_show_edit(beat, image,
                                                             transform)
            before = str(getattr(beat, 'show', '') or '')
            edit = AtEdit(target='scene', transform=transform, clear=clear,
                          field='show',
                          before=before, after=new_show or before,
                          reason=reason, beat_index=idx, requested_index=idx,
                          _beat=beat,
                          affects=(_affected_range(beats, idx, kind='scene',
                                                   tag=image.split()[0])
                                   if not reason else ()))
            if not reason and tok and tok[0]:
                edit.token_field, edit.extra_token = tok[0], tok[1]
            if not edit.reason and before_at == transform:
                edit.reason = '已经是原样了' if clear else '已经是这个变换了'
        else:
            if not scene and src is not None and src != idx:
                src_scene = str(getattr(beats[src], 'scene', '') or '').strip()
                if src_scene:
                    target_i, hit, scene = src, beats[src], src_scene
            if not scene:
                # 背景也可能是 `show bg X` 换的（快照同样记 `scene_src`）——
                # 那种行没有 `scene_at` 可改，说清楚，别让人对着"没有背景"发愣。
                base_i = src if (src is not None and src != idx) else idx
                spec = str(scene_image or '').strip()
                tag = spec.split()[0] if spec.split() else ''
                if tag and _beat_show_has_tag(beats[base_i], tag):
                    return AtEdit(
                        reason=('这一行的背景是 `show bg …` 写的（不是 scene），'
                                '请在「高级区 · Stage」里改那一条的 at；预览不猜'),
                        beat_index=base_i, _beat=beats[base_i],
                        transform=transform)
                if src is None:
                    # 跨章继承：`scene_src = -1`，没有"最初写它的那一拍"可改。
                    return AtEdit(
                        reason=('这一拍的背景找不到最初写它的那一拍（多半是上一章'
                                '继承来的），不能在这里改尺寸；要改请用「换景」写一条'
                                '新的 scene'),
                        beat_index=idx, _beat=beat, transform=transform)
                return AtEdit(reason='这一拍没有背景（没有 scene），不能写背景变换',
                              beat_index=idx, _beat=beat, transform=transform)
            if '@' in scene:
                return AtEdit(reason='背景是紧凑写法 `图@变换`，请先在卡片里拆开',
                              beat_index=idx, _beat=beat, transform=transform)
            before = str(getattr(hit, 'scene_at', '') or '')
            after = '' if clear else transform
            edit = AtEdit(target='scene', transform=transform, clear=clear,
                          field='scene_at',
                          before=before, after=after, beat_index=target_i,
                          requested_index=idx, _beat=hit,
                          affects=_affected_range(beats, target_i, kind='scene',
                                                  tag=scene.split()[0]
                                                  if scene.split() else ''))
            if before == after:
                edit.reason = ('这一拍的背景已经是原样了（没有 at）' if clear
                               else '已经是这个变换了')

    # 本景"还有几处单独改写"（只读）：改的是**源**那一行，但本景后面只要有谁
    # 又 show 了一次同一个对象，这次改动就从那一刻起失效。确认框里那一句
    # "改源只到第 12 拍有效"就是它 —— 数字必须与实际写的是同一批行。
    if not edit.reason:
        try:
            from storygraph.story.override_advice import override_advice
            edit.advice = override_advice(
                beats, edit.beat_index,
                kind='layer' if edit.target == 'layer' else 'scene',
                tag=edit.tag)
        except Exception as e:                 # noqa: BLE001 - 算不出就不显示
            swallow('app.stage.plan_at_edit.advice', e)

    # 生成产物 diff（确认对话框的真相：不是"字段变了"，是"脚本会变成什么"）
    try:
        import copy
        from storygraph.story.beat_render import render_beat_to_rpy
        edit.rpy_before = '\n'.join(render_beat_to_rpy(hit) or [])
        clone = copy.copy(hit)
        setattr(clone, edit.field, edit.after)
        if edit.extra_token and edit.token_field:
            cur = str(getattr(clone, edit.token_field, '') or '')
            setattr(clone, edit.token_field,
                    f'{cur},{edit.extra_token}' if cur else edit.extra_token)
        edit.rpy_after = '\n'.join(render_beat_to_rpy(clone) or [])
    except Exception as e:                     # noqa: BLE001 - diff 拿不到不影响计划
        swallow('app.stage.plan_at_edit.diff', e)
    return edit


def plan_size_edit(beats, index: int, row: dict, *, key: str, at: str = '',
                   project_root=None, transform_index=None, screen=None):
    """
    尺寸面板的一次「改尺寸」→ `AtEdit`（**只算，不动 Beat**，2026-10-31）。

    `row` 是 `app.stage_render.size_rows` 给的那一行（带 `kind` / `tag` /
    `spec` / `source`），`key` 是下拉里选中的项：

      · `'as_is'`（原样）→ `mode='clear'`：去掉这一行的 at；
      · 其余（铺满 / 适应 / 50% / 自定义）→ `at` 是**项目里已有的** transform
        名（匹配在 `story.size_presets` 里按几何做完），写**最初写下这一行的
        那一拍**（`row['source']`）—— 尺寸是持续状态，在继承它的拍上写不进去。

    写回仍只改内存 Beat；落盘走既有 patch/导出门禁（可撤销）。
    """
    if not isinstance(row, dict) or not row:
        return AtEdit(reason='没有选中要改的对象', beat_index=-1)
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return AtEdit(reason='拍索引不合法', beat_index=-1)
    target = 'layer' if str(row.get('kind') or '') == 'layer' else 'scene'
    tag = str(row.get('tag') or '')
    source = row.get('source')
    spec = str(row.get('spec') or '')
    if str(key or '') == 'as_is':
        return plan_at_edit(beats, idx, target=target, tag=tag, transform='',
                            project_root=project_root,
                            transform_index=transform_index, screen=screen,
                            source_index=source, mode='clear',
                            scene_image=spec)
    name = str(at or '').strip()
    if not name:
        return AtEdit(reason='这一项没有可写的 transform（先重新选一次预设）',
                      beat_index=idx)
    return plan_at_edit(beats, idx, target=target, tag=tag, transform=name,
                        project_root=project_root,
                        transform_index=transform_index, screen=screen,
                        source_index=source, mode='source',
                        scene_image=spec)


def plan_scene_change(beats, index: int, image: str, *, at: str = '',
                      project_root=None, transform_index=None,
                      screen=None) -> SceneEdit:
    """
    计划一次**换景**（显式写 `scene`，2026-10-31）—— 只算，不动 Beat。

    用户拍板的规矩（这一条是"bg/cg 不能改像素"那三轮讨论的收口）：

      > 换景（`scene`）会清台。只有在人**明确选"换景"**时才写，
      > 确认框写清"会清掉台上 N 个立绘"。

    为什么它不能和「改尺寸」用同一个入口：Ren'Py 官方两句话 ——
    `scene` 移除 master 层**所有** displayable 再显示一张图；`show` 只替换
    同 tag。一个背景的"大小不对"用 `show bg X at …` 就够了（`_bg_show_edit`
    **不会**写出 `scene`）；而"这一拍换成另一张背景"是另一件事，代价是
    台上的人会没 —— 所以它必须是**人明确按下**的动作。

    与 `plan_at_edit` 的三处不同（都是换景特有的）：

      ① 写**请求的那一拍**（换景是事件，不是持续状态；尺寸才是持续状态）；
      ② 新补的 `scene` 要占**最前面**的令牌位（`_scene_token_insert`），
         否则渲染器把缺的 verb 补在末尾 = `show` 完立刻被清台；
      ③ 同一拍里那条会盖掉新背景的 `show bg …` 要去掉（留着的话，
         `show` 排在 `scene` 后面，产物里的背景是**旧那张**）。

    `at` 是可选的背景变换（尺寸面板里选中的那个预设）；只允许引用项目
    **已有**的 transform（与 `plan_at_edit` 同一条规矩，不替人新建）。

    返回 `SceneEdit`；`reason` 非空 = 拒绝（不写）。
    """
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return SceneEdit(reason='拍索引不合法', beat_index=-1)
    beats = list(beats or ())
    if idx < 0 or idx >= len(beats):
        return SceneEdit(reason='没有这一拍（索引越界）', beat_index=idx)
    beat = beats[idx]
    image = ' '.join(str(image or '').split())
    if not image:
        return SceneEdit(reason='没有选新的背景图', beat_index=idx, _beat=beat)
    if '@' in image:
        return SceneEdit(reason='背景图名里不能带 `@`（变换请用下拉里那一项）',
                         beat_index=idx, _beat=beat, image=image)
    tag = image.split()[0]
    at = str(at or '').strip()
    if '@' in at:
        return SceneEdit(reason=f'变换名「{at}」里不能带 `@`', beat_index=idx,
                         _beat=beat, image=image)
    if not _scene_image_known(image, project_root):
        return SceneEdit(
            reason=(f'项目里找不到这张图「{image}」——写进脚本引擎会报 '
                    f'NameError；请先在资源表/`image` 声明里登记它'),
            beat_index=idx, _beat=beat, image=image)

    from storygraph.story.atl import (DEFAULT_SCREEN, placement_of,
                                      scan_transforms)
    screen_pair = tuple(screen) if screen else DEFAULT_SCREEN
    if at:
        if transform_index is None and project_root:
            transform_index = scan_transforms(project_root)
        transform_index = transform_index or {}
        if at not in transform_index:
            return SceneEdit(
                reason=f'项目里没有 transform「{at}」（只允许引用项目已有的）',
                beat_index=idx, _beat=beat, image=image)
        try:
            place = placement_of(at, transform_index, screen=screen_pair)
        except Exception as e:                 # noqa: BLE001 - 坏定义拒绝
            swallow('app.stage.plan_scene_change.place', e)
            place = None
        if place is None or not str(getattr(place, 'source', '')).startswith(
                ('atl', 'builtin', 'word', 'letter', 'center')):
            return SceneEdit(reason=f'transform「{at}」解析不出几何，拒绝写',
                             beat_index=idx, _beat=beat, image=image)

    # —— 同一拍里的背景 show：简单子句去掉，复杂子句拒绝 ——
    from storygraph.core.vocabulary import split_stage_items
    show_before = str(getattr(beat, 'show', '') or '')
    parts = list(split_stage_items(show_before)) if show_before else []
    keep: list = []
    removed: list = []
    blocked = ''
    for part in parts:
        info = _show_part_info(part)
        words = str(info.get('image') or '').split()
        if not words or not _same_layer_tag(words[0], tag):
            keep.append(part)
            continue
        if info.get('kind') != 'simple':
            blocked = str(part).strip()
            keep.append(part)
            continue
        removed.append(str(part).strip())
    if blocked:
        return SceneEdit(
            reason=(f'这一拍还有一条背景 show 是复杂子句（`{blocked}`，'
                    '含 behind/with/expression）——换景会和它打架，'
                    '先去「高级区 · Stage」把那条改掉再换景'),
            beat_index=idx, _beat=beat, image=image)
    # 没去掉任何一条时**逐字保留原文**（别把用户写的 `;` 间距顺手重排）
    show_after = '; '.join(keep) if removed else show_before

    # —— 这一拍开始前台上有什么（确认框里"会清掉 N 个立绘"的唯一来源）——
    before_state = stage_before(beats, idx)
    if before_state is None:
        return SceneEdit(reason='算不出这一拍台上还有什么（舞台快照推导失败），'
                               '不猜 —— 换景没做',
                         beat_index=idx, _beat=beat, image=image)
    cleared_sprites = tuple(before_state['sprites'])
    cleared_layers = tuple(before_state['layers'])

    scene_before = str(getattr(beat, 'scene', '') or '').strip()
    scene_at_before = str(getattr(beat, 'scene_at', '') or '').strip()
    if '@' in scene_before:
        return SceneEdit(reason='这一拍的背景是紧凑写法 `图@变换`，'
                               '请先在卡片里拆开再换景',
                         beat_index=idx, _beat=beat, image=image)
    added = not scene_before
    if not added and scene_before == image and scene_at_before == at:
        return SceneEdit(reason='这一拍已经是这一景了（图和变换都没变）',
                         beat_index=idx, _beat=beat, image=image)
    same_bg = str(before_state['scene']).strip() == image
    if (added and same_bg and not cleared_sprites and not cleared_layers
            and not removed):
        return SceneEdit(reason='台上没人、背景也没变 —— 这条 scene 什么都不会改',
                         beat_index=idx, _beat=beat, image=image)

    # —— 顺序列：本来没有 scene 的拍要补一个排在最前面的令牌 ——
    order_field, order_after = _scene_token_insert(beat)
    order_before = str(getattr(beat, order_field, '') or '') if order_field else ''

    edit = SceneEdit(
        target='scene', field='scene', transform=at, added=added,
        image=image, scene_before=scene_before,
        scene_at_before=scene_at_before, scene_at_after=at,
        show_before=show_before, show_after=show_after,
        removed_show='; '.join(removed),
        order_field=order_field, order_before=order_before,
        order_after=order_after,
        cleared_sprites=cleared_sprites, cleared_layers=cleared_layers,
        same_image=bool(same_bg and added),
        beat_index=idx, requested_index=idx, _beat=beat,
        before=(f'{scene_before} at {scene_at_before}'.strip()),
        after=(f'{image} at {at}'.strip()),
        affects=_affected_range(beats, idx, kind='scene', tag=tag))
    # 生成产物 diff（确认框的真相：不是"字段变了"，是"脚本会变成什么"）
    try:
        import copy
        from storygraph.story.beat_render import render_beat_to_rpy
        edit.rpy_before = '\n'.join(render_beat_to_rpy(beat) or [])
        clone = copy.copy(beat)
        clone.scene = image
        clone.scene_at = at
        clone.show = show_after
        if order_field:
            setattr(clone, order_field, order_after)
        edit.rpy_after = '\n'.join(render_beat_to_rpy(clone) or [])
        # "作者自己的 show/hide 排在 scene 前面"要按**改完**的顺序算 ——
        # 按改之前算的话，刚被去掉的那条 `show bg …` 会被算进去，
        # 确认框就会吓人一跳（"它们会被清掉"，其实已经去掉了）。
        edit.shows_before_scene = _shows_precede_scene(clone)
    except Exception as e:                     # noqa: BLE001 - diff 拿不到不影响计划
        swallow('app.stage.plan_scene_change.diff', e)
        edit.shows_before_scene = _shows_precede_scene(beat)
    return edit


def _shows_precede_scene(beat) -> bool:
    """这一拍的令牌里，`show`/`hide` 有没有排在 `scene` **之前**（会被一起清掉）。"""
    try:
        tokens = list(beat.op_tokens_full() or ())
    except Exception as e:                     # noqa: BLE001 - 读不出顺序就别吓人
        swallow('app.stage._shows_precede_scene', e)
        return False
    seen_scene = False
    for verb, _idx in tokens:
        if verb == 'scene':
            seen_scene = True
            continue
        if seen_scene:
            continue
        if verb in ('show', 'hide'):
            return True
    return False


def has_stage_change(frame: StageFrame) -> bool:
    """
    这一拍的**舞台**真的变了什么吗（预览的「只看有变化的拍」判据）。

    ⚠ 2026-10-19（审计 P1-8 收尾）：判据从前是 `bool(frame_summary(f))`，
    而摘要**永远**至少含 `#N · line_id` —— 于是恒为 True，那个勾选框
    什么也没做（审计原话："仍是空实现"）。

    现在只看 `changes` —— 它和「舞台快照」表的「本拍变化」是**同一份文字**
    （`stage_snapshot._render_changes`），所以"预览里留下的拍"与"表里标了
    变化的拍"必然一致。

    刻意**不看台词**：一句纯对话没有改变舞台，它不是这一项要留下的东西；
    这也正是这张表存在的意义（几百拍里只看真正换景/换人/换声的那几拍）。
    """
    try:
        return bool(str(getattr(frame, 'changes', '') or '').strip())
    except Exception as e:                   # noqa: BLE001 - 判不了就别藏拍
        swallow('app.stage.has_stage_change', e)
        return True


# ===================== 真实素材解析（纯函数，可无头测） =====================
#
# 为什么要单独一层：预览要画真图，就必须回答"这个图像名对应磁盘上哪张图"。
# 这件事**必须和编辑器/Excel 那条路同源**（都走"按 tag 收窄"的解析），
# 否则预览会把 `ling oh` 画成 lan 的立绘 —— 用户 2026-09-30 实测的串图事故。


# —— 脚本里的 `image` 声明：**引擎的真源** ——
#
# 用户 2026-10-05：
# > 这个你确定 bgm 导入了吗？背景导入了吗？是否正确呢？
#
# 实测真实项目：预览里 `bg bg11_0` **一片黑**，而资源表里只有磁盘扫描出来的
# `bg11`（`images/bg/bg11.png`）。"`bg11_0` = `bg11.png`"这条对应关系**只写在
# 脚本里**：
#
#     definitions.rpy:146   image bg bg11_0 = "images/bg/bg11.png"
#
# Ren'Py 是照脚本认的（`show` / `scene` 里的名字查的就是这张声明表），所以预览
# 也必须照脚本认 —— 否则"表里没登记的写法"就永远画不出来（表是编辑器的东西，
# 脚本才是游戏的东西）。`image black = "#000000"` 这种纯色声明也一并收下。
#
# 只扫 `*.rpy` 的顶层 `image` 行（实测真实项目 41 个文件 / 104 条声明 / 70ms），
# 结果按项目缓存；**不做全量语法解析**（那是导入章节的活，这里只要声明）。

#: `image <名字> = <表达式>`（名字允许带空格：`bg bg1_1`）；与解析层同一套写法
_RE_IMAGE_DECL = re.compile(
    r'^image\s+(?P<name>[^\s=][^=]*?)\s*=\s*(?P<rhs>.+)$')
#: 扫描时跳过的目录（缓存/存档/翻译/构建产物里没有演出用的图）
_RPY_SKIP_DIRS = {'cache', 'saves', 'tl', '.git', 'build', '__pycache__'}

#: 脚本声明缓存：`{项目根: {'names':…, 'attrs':…}}`。**每项目只扫一次**。
_SCRIPT_IMAGE_CACHE: dict = {}


def script_images(project_root) -> dict:
    """
    扫项目脚本里的 `image` 声明 → `{'names': {名字: info}, 'attrs': {属性串: info}}`。

    `info = {'path': 脚本里的相对路径|'', 'solid': '#rrggbb'|''}`。
    同一张图会有两种写法（`bg bg11_0` 与只有属性段的 `bg11_0`），两份索引都给，
    免得调用方自己去切字符串。
    """
    empty = {'names': {}, 'attrs': {}}
    if not project_root:
        return empty
    key = str(project_root)
    got = _SCRIPT_IMAGE_CACHE.get(key)
    if got is not None:
        return got
    try:
        from storygraph.parsing.renpy_parser import (image_path_from_expr,
                                                     image_solid_from_expr)
    except Exception as e:                         # noqa: BLE001 - 桩环境
        swallow('app.stage.script_images', e)
        image_path_from_expr = image_solid_from_expr = lambda _e: None

    names: dict = {}
    attrs: dict = {}
    seen = set()
    roots = [Path(project_root), Path(project_root) / 'game']
    for root in roots:
        try:
            if not root.is_dir():
                continue
            files = sorted(root.rglob('*.rpy'))
        except OSError:
            continue
        for f in files:
            if _RPY_SKIP_DIRS & set(f.parts) or f in seen:
                continue
            seen.add(f)
            try:
                text = f.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            for line in text.splitlines():
                # 只认顶层（缩进的都块内语句）；`# image …` 是注释
                if not line.startswith('image '):
                    continue
                m = _RE_IMAGE_DECL.match(line)
                if not m:
                    continue
                name = ' '.join(m.group('name').split())
                if not name:
                    continue
                rhs = m.group('rhs').strip()
                info: dict = {'path': '', 'solid': ''}
                path = image_path_from_expr(rhs)
                if path:
                    info['path'] = path
                else:
                    solid = image_solid_from_expr(rhs)
                    if not solid:
                        continue      # 既不是文件也不是纯色（layeredimage…）
                    info['solid'] = solid
                names.setdefault(name, info)
                parts = name.split()
                if len(parts) > 1:
                    attrs.setdefault(' '.join(parts[1:]), info)
    out = {'names': names, 'attrs': attrs}
    _SCRIPT_IMAGE_CACHE[key] = out
    return out


def script_image(spec, project_root):
    """
    图像名 → 脚本里的声明 `{'path':…, 'solid':…}`；查不到返回 None。

    匹配顺序：整名 → 逐级去掉尾属性（`ye base_handdown sideeye` 落到
    `image ye base_handdown`）→ 只有属性段的写法（`bg11_0`）。长名优先，
    **不猜**（实在没有就返回 None，交给调用方的磁盘回退）。
    """
    decls = script_images(project_root)
    names, attrs = decls['names'], decls['attrs']
    if not names and not attrs:
        return None
    parts = str(spec or '').split()
    if not parts:
        return None
    for i in range(len(parts), 0, -1):
        cand = ' '.join(parts[:i])
        info = names.get(cand) or attrs.get(cand)
        if info:
            return info
    return None


def resolve_stage_asset(name: str, images: dict, project_root=None,
                        index: dict = None):
    """
    图像名（`bg bg6_2` / `ling disappointed`）→ 磁盘上的图片路径。

    找不到（没登记 / 文件不在）返回 `None` —— 调用方应当**降级画示意图**，
    绝不让画面空着（"图没找到 → 一片空白"是这个功能最容易踩的坑）。

    解析**先按 tag 收窄**（复用 `lookup_image_id`）：真实项目里
    `lan` 与 `ling` 都有 `oh` 这个表情，用裸属性名查必然张冠李戴。

    三级顺序（**越来越粗**，每级都要求文件真的存在）：
      ① 资源表（表里的 `Path`，先按项目根、再按 `game/` 解释）；
      ② 脚本里的 `image` 声明（引擎的真源 —— `image bg bg11_0 = "…"` 这种
         别名只写在脚本里，表里查不到；见 `script_images`）；
      ③ 磁盘按名字回退（`bg6_2` ↔ `bg6.2.png` 这种点/下划线差异）。
    """
    spec = str(name or '').strip()
    if not spec:
        return None
    parts = spec.split()
    tag, rest = parts[0], parts[1:]
    if images:
        # ⚠ **多属性时先试"表情件"**（2026-10-05 修"无头骑士"）：
        #
        # `show ye base_handdown happy` 里 `base_handdown` 是**身体件**
        # （只有身体没有头），`lookup_image_id` 只看第一个属性 → 画出无头身体。
        # 按"非身体件优先（从后往前）"逐个试，第一个查得到的才是完整立绘。
        try:
            from storygraph.app.beats import (lookup_image_id,
                                              preferred_image_attrs)
            wants = preferred_image_attrs(rest) if len(rest) > 1 else []
            for attr in wants:
                aid = lookup_image_id(images, tag, [attr])
                if not aid:
                    continue
                row = images.get(aid) or {}
                hit = _path_from_rel(str(row.get('Path') or '').strip(),
                                     project_root)
                if hit is not None:
                    return hit
        except Exception as e:                    # noqa: BLE001 - 桩环境
            swallow('app.stage.resolve_stage_asset.order', e)
        try:
            aid = lookup_image_id(images, tag, rest)
        except Exception as e:                    # noqa: BLE001 - 桩环境
            swallow('app.stage.resolve_stage_asset', e)
            aid = None
        if aid:
            row = images.get(aid) or {}
            rel = str(row.get('Path') or '').strip()
            hit = _path_from_rel(rel, project_root)
            if hit is not None:
                return hit
    # ② 脚本声明（用户 2026-10-05："背景导入了吗？" —— `bg bg11_0` 就是这一级
    #    救回来的：表里只有 `bg11`，别名 `bg11_0` 是脚本里的声明）
    decl = script_image(spec, project_root)
    if decl and decl.get('path'):
        hit = _path_from_rel(decl['path'], project_root)
        if hit is not None:
            return hit
    # ③ 有界磁盘回退（按名字找文件）。
    #    真实数据实测：脚本声明 `image bg bg6_2 = "images/bg/bg6.2.png"`
    #    的文件名带点、而脚本里写的是下划线，靠表查必然落空。
    return image_index_lookup(spec, index)


_SOLID_RE = re.compile(
    r'^\s*(?:Solid\s*\(\s*)?["\']?(#[0-9A-Fa-f]{3,8})\b')


def resolve_solid_color(name: str, images: dict, project_root=None):
    """
    图像名 → **纯色**（`image black = "#000000"` / `Solid("#000")`）。

    真实项目里 `scene black` 出现 50 次 —— 它是纯色、不是图片文件。
    不认这一档的话，预览会把它当"找不到图"（其实游戏里就是一块黑）。

    表里查不到时同样看**脚本声明**（`definitions.rpy` 里 `image black = "#000000"`
    根本没进表 —— 用户 2026-10-05 那次排查的背景别名是同一个成因）。
    """
    spec = str(name or '').strip()
    if not spec:
        return None
    parts = spec.split()
    aid = None
    if images:
        try:
            aid = lookup_image_id(images, parts[0], parts[1:])
        except Exception as e:                    # noqa: BLE001
            swallow('app.stage.resolve_solid_color', e)
            aid = None
    if aid:
        row = images.get(aid) or {}
        # 脚本声明 `image black = "#000000"` 时，合并出来的行把颜色放在
        # `Solid` 字段里（`Path` 是空的，因为没有文件）；旧版/手写的表可能
        # 直接写进 Path，所以两条都认。
        solid = str(row.get('Solid') or '').strip()
        if solid:
            m = _SOLID_RE.match(solid)
            if m:
                return m.group(1)
            if re.match(r'^#[0-9A-Fa-f]{3,8}$', solid):
                return solid
        m = _SOLID_RE.match(str(row.get('Path') or ''))
        if m:
            return m.group(1)
    decl = script_image(spec, project_root)
    return (decl or {}).get('solid') or None


def _path_from_rel(rel: str, project_root):
    """
    表里的 Path → 真实文件（不存在返回 None）。

    **两套基准都要试**：真实数据实测（2026-09-30）

      · 扫描出来的资产行：`game/images/characters/ling/oh.png`（相对项目根）
      · 脚本 `image` 声明合并进来的行：`images/bg/bg6.2.png`（相对 `game/`）

    只认前者的话，**所有脚本声明的背景图都解析不出来**
    （chapter1_3 里 305 帧的背景全落空）。
    """
    rel = str(rel or '').strip()
    if not rel or rel.startswith('#'):
        return None                    # 纯色（`image black = "#000000"`）
    from pathlib import Path
    p = Path(rel)
    if p.is_absolute():
        try:
            return p if p.exists() else None
        except OSError:
            return None
    if not project_root:
        return None
    for base in (Path(project_root), Path(project_root) / 'game'):
        cand = base / rel
        try:
            if cand.exists():
                return cand
        except OSError:
            continue
    return None


#: 磁盘索引缓存：`{项目根: (目录指纹, 建索引的时间, {小写文件名: 路径})}`。
#:
#: 从前只按项目根缓存、**永不失效**：用户往 `game/images/` 里丢一张新图，
#: 预览还是"找不到这张图"，得重启编辑器（2026-10-20 修，P3）。
#: 现在带"目录指纹 + 复核间隔"：
#:   · 加/删/改名文件会让**所在目录**的 mtime 变 → 指纹变 → 重建索引；
#:   · 指纹只看目录（几百个），不 stat 几千张图 —— 那正是这个缓存要省掉的开销；
#:   · 复核有 `refresh_seconds` 节流：`show_frame` 每拍都问一次，
#:     不能每拍都走一遍目录树（`refresh_seconds=0` 可强制复核，测试用它）。
_IMAGE_INDEX_CACHE: dict = {}

#: 索引复核间隔（秒）。一秒半之内连续翻拍不会重复扫目录。
_IMAGE_INDEX_REFRESH = 1.5


def _dirs_signature(roots, limit: int = 600) -> tuple:
    """
    图片目录的**目录指纹**：`((目录, mtime_ns), …)`（排序后）。

    只走目录、不 stat 文件：加一张图会让它所在目录的 mtime 变。
    有界（600 个目录）：项目大到大这个数，索引本来就该重建。
    """
    sig = []
    seen = 0
    for root in roots:
        try:
            if not root.is_dir():
                continue
        except OSError:
            continue
        stack = [root]
        while stack and seen < limit:
            cur = stack.pop()
            seen += 1
            try:
                sig.append((str(cur), cur.stat().st_mtime_ns))
                entries = list(os.scandir(cur))
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                except OSError:
                    continue
    sig.sort()
    return tuple(sig)


def image_index(project_root, refresh_seconds: float = None) -> dict:
    """
    项目里的图片索引（`bg6.2` / `bg6_2` → 真实文件）。

    **有界**：只在已知的图片目录里递归一次（`game/images/**`），跳过
    `gui/`（界面素材，几千张，与演出无关）。
    这不是"猜"，而是"表里没有、但文件确实在"时的兜底 ——
    找不到就老实说找不到，绝不编一个路径出来。

    `refresh_seconds`：多久复核一次目录指纹（默认 1.5 秒；**0 = 每次都查**）。
    新加的图不必重启就能被看见，翻拍时又不会每拍扫一遍目录树。
    """
    if not project_root:
        return {}
    key = str(project_root)
    roots = [Path(project_root) / 'game' / 'images',
             Path(project_root) / 'images']
    wait = _IMAGE_INDEX_REFRESH if refresh_seconds is None else float(
        refresh_seconds)
    now = time.monotonic()
    got = _IMAGE_INDEX_CACHE.get(key)
    if got is not None and now - got[1] < wait:
        return got[2]
    sig = _dirs_signature(roots)
    if got is not None and got[0] == sig:
        _IMAGE_INDEX_CACHE[key] = (sig, now, got[2])
        return got[2]
    idx: dict = {}
    exts = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
    try:
        for root in roots:
            if not root.is_dir():
                continue
            for f in root.rglob('*'):
                if f.suffix.lower() not in exts:
                    continue
                parts = {x.lower() for x in f.parts}
                if 'gui' in parts or 'screenshots' in parts:
                    continue
                idx.setdefault(f.stem.lower(), str(f))
    except OSError as e:
        swallow('stage.image_index', e)
    _IMAGE_INDEX_CACHE[key] = (sig, now, idx)
    return idx


def image_index_lookup(spec: str, index: dict):
    """图像名 → 索引里的文件。`bg6_2` 与 `bg6.2` 视为同一个（真实项目两种写法都有人用）。"""
    if not index:
        return None
    parts = str(spec or '').split()
    if not parts:
        return None
    cands = [parts[-1], '_'.join(parts), ' '.join(parts)]
    if len(parts) > 1:
        cands.append(parts[-1].replace('_', '.'))
    for c in cands:
        for key in (c.lower(), c.lower().replace('.', '_'),
                    c.lower().replace('_', '.')):
            hit = index.get(key)
            if hit:
                return hit
    return None


def sprite_plan(frame, sprite, layer_model, images: dict, project_root=None):
    """
    一个立绘 → **合成计划**（`LayerPlan`）或 None。

    设计文档：`docs/archive/设计_合成立绘合成与姿势切换_2026-10-05.md`（P2）。
    没有 `layeredimage` 声明 / 认不出 → 返回 None（调用方走今天的单图路径）。
    """
    if layer_model is None or not getattr(layer_model, 'images', None):
        return None
    try:
        from storygraph.story.layered_model import resolve_layers
        spec = ''
        if isinstance(sprite, (list, tuple)) and len(sprite) >= 2:
            spec = str(sprite[1] or '')
        else:
            spec = str(getattr(sprite, 'image', '') or '')
        parts = spec.split()
        if not parts:
            return None
        tag, attrs = parts[0], parts[1:]
        if not layer_model.has(tag):
            return None
        plan = resolve_layers(layer_model, tag, attrs, project_root)
        return plan if getattr(plan, 'layers', None) else None
    except Exception as e:                        # noqa: BLE001 - 认不出就走老路
        swallow('app.stage.sprite_plan', e)
        return None


def resolve_frame_assets(frame, images: dict, project_root=None,
                         index: dict = None, layer_model=None) -> dict:
    """
    一帧要用到的真实图片路径：`{'bg':..., 'layers':..., 'sprites': [(布局, 路径)]}`。

    纯函数 —— 给真实资源表就能无头跑，也顺手回答了
    "**这一章缺哪几张图**"（`missing` 列表），不用等到画面上空一片才发现。
    查不到的项一律 `None`，由绘制层降级。
    """
    def resolve(spec):
        """真图优先；没有文件但**是纯色**（`scene black`）也算解析成功。"""
        path = resolve_stage_asset(spec, images, project_root, index)
        if path is not None:
            return path
        if resolve_solid_color(spec, images, project_root):
            return None                  # 有解，只是不是文件 → 不算"找不到"
        return None

    sprites = []
    missing = []
    composed = {}
    for s in getattr(frame, 'sprites', []) or []:
        spec = getattr(s, 'image', '')
        # ① **合成立绘优先**（有 layeredimage 声明的项目）：
        #    按项目自己的 group/attribute + AlphaMask 蒙版叠出完整立绘。
        #    合成不出来就往下走老路（单图），**绝不画错也不留白**。
        path = None
        plan = sprite_plan(frame, s, layer_model, images, project_root)
        if plan is not None and getattr(plan, 'ok', False):
            try:
                from storygraph.app.layer_compose import compose_sprite
                path = compose_sprite(plan)
            except Exception as e:                # noqa: BLE001 - 合成失败走老路
                swallow('app.stage.resolve_frame_assets.compose', e)
                path = None
            if path is not None:
                composed[spec] = plan
        if path is None:
            path = resolve(spec)
        if path is None and spec and not resolve_solid_color(spec, images,
                                                             project_root):
            missing.append(spec)
        sprites.append((s, path))
    bg = None
    if getattr(frame, 'scene', ''):
        bg = resolve(frame.scene)
        if bg is None and not resolve_solid_color(frame.scene, images,
                                                 project_root):
            missing.append(frame.scene)
    layers = None
    if getattr(frame, 'layers', ''):
        layers = resolve(frame.layers)
        if layers is None and not resolve_solid_color(frame.layers, images,
                                                     project_root):
            missing.append(frame.layers)
    return {'bg': bg, 'layers': layers, 'sprites': sprites,
            'missing': missing, 'composed': composed}


# ===================== 画出来（tkinter） =====================
#
# 上面全是纯函数；这一层只负责"把帧画到画布上 + 播放控制"。
#
# 两种画法，**都能用**：
#   · 真实贴图（默认）：背景 + 立绘，走 `ui_qt.thumb_loader` 异步解码
#     （禁止在 UI 线程同步解码大图 —— 立绘常见 1500×2200）
#   · 示意图：找不到图 / 关掉真实贴图时，画方块 + 名字 + 站位
# 缺图时**同一帧里两种混着画**：有图的贴图、没图的落在方块位置上，
# 并在底部明确写出"缺哪几张"，不让用户对着空白猜。
#
# 画面尺寸用 16:9（`STAGE_W × STAGE_H`）：和游戏屏幕同比例，
# 站位才看得出来对不对。

#: 舞台画布尺寸（16:9）
STAGE_W, STAGE_H = 896, 504
#: 立绘占舞台高度的比例（**只在没有真实几何时**用；Ren'Py 立绘通常接近满高）
SPRITE_H_RATIO = 0.92
#: 立绘解码目标尺寸（真实几何下按 zoom×原图算；够清晰又不至于解整张大图）
SPRITE_MAX = (640, 1140)
#: 示意图方块的尺寸
BLOCK_W, BLOCK_H = 92, 232


def _screen_x(x: float, width: int) -> int:
    return int(max(0.0, min(1.0, x)) * width)


def sprite_box(layout, natural, screen=None):
    """
    一个立绘在**虚拟屏幕**上的位置与大小：`(left, top, w, h)`；算不出来返回 None。

    `natural` 是图片的原始像素尺寸 `(w, h)`，`layout.place` 是
    `story.atl` 从项目 transform 解析出来的几何。Ren'Py 的规则照抄：

      · 大小 = 原图尺寸 × `xzoom/yzoom`；
      · `pos` 是锚点落在哪，`anchor` 是图像自己的哪个点；
      · 小数 = 屏幕/图像比例，整数 = 像素（`Placement.box` 里实现）。

    为什么必须带上原图尺寸：真实项目立绘是 4320×7680，`zoom 0.30` 之后仍有
    1296×2304（1920×1080 屏幕的两倍高）—— 只按"屏幕百分比"画必然错位。
    `layout` 没有真实几何（没有 pos）或 `natural` 缺失时返回 None，
    调用方按老办法画示意图。
    """
    place = getattr(layout, 'place', None)
    if place is None:
        return None
    if (getattr(place, 'x_px', None) is None
            and getattr(place, 'y_px', None) is None):
        return None                      # 只有名字启发 → 交给示意图
    try:
        return place.box(natural, screen or (1920, 1080))
    except Exception as e:                    # noqa: BLE001 - 坏几何不该炸预览
        swallow('app.stage.sprite_box', e)
        return None


# ===================== 声音：素材名 → 真实音频文件 =====================
#
# 用户 2026-10-05：
# > 演出的预览界面可能仍旧有点问题，第一个是 bgm 未被实际播放
#
# 预览要出声，就必须回答"这个 BGM 名对应磁盘上哪个 mp3"。规则与图片那条路
# 同源：**先查资源表**（Path 列），查不到再按名字在已知音频目录里找。

#: 音频目录（Ren'Py 惯例 + 这个项目的实际布局）
_AUDIO_DIRS = ('game/bgm', 'game/audio', 'game/sound', 'game/voice',
               'game/music', 'bgm', 'audio', 'sound', 'voice')
_AUDIO_EXT = ('.mp3', '.ogg', '.wav', '.opus', '.flac', '.m4a')


def _audio_index(project_root) -> dict:
    """`game/**` 下的音频文件：`{小写名字(含/不含扩展名): [路径, …]}`（有界）。"""
    root = Path(project_root) if project_root else None
    if root is None or not root.is_dir():
        return {}
    out: dict = {}
    for rel in _AUDIO_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        try:
            files = sorted(base.rglob('*'))
        except OSError:
            continue
        for path in files:
            if path.suffix.lower() not in _AUDIO_EXT or not path.is_file():
                continue
            out.setdefault(path.stem.lower(), []).append(path)
            out.setdefault(path.name.lower(), []).append(path)
    return out


def audio_spec_name(spec) -> str:
    """
    从各种写法里取出"曲名/文件名"：`[音乐] bgm/x.mp3 fadein 1.0` → `bgm/x.mp3`；
    `bgm_Empt1y` → 原样。卡片上那一格是**带前缀的整行文本**，而快照里拿到的是
    资产 ID —— 两种都要认。
    """
    text = str(spec or '').strip()
    if not text:
        return ''
    text = re.sub(r'^\s*\[[^\]]*\]\s*', '', text)          # 去掉 `[音乐]` 前缀
    text = re.sub(r'\s+(fadein|fadeout|loop|volume|noloop)\b.*$', '', text,
                  flags=re.IGNORECASE)
    parts = text.split()
    for token in parts:
        if token.lower().endswith(_AUDIO_EXT):
            return token
    return parts[0] if parts else ''


def _norm_audio_stem(text) -> str:
    """
    近似比对用的"曲子名"：去扩展名、去尾部省略号、小写。

    为什么专门去省略号：真实项目里脚本写的是
    `play music "bgm/夜晚，断电之后…….mp3"`，而磁盘上是
    `夜晚，断电之后，不存在的教室.mp3` —— 作者当初把长名字省略着写，
    后来文件改名了，脚本没跟着改（游戏自己也会找不到这个文件）。
    """
    s = str(text or '').strip().replace('\\', '/').split('/')[-1]
    s = re.sub(r'\.(mp3|ogg|wav|opus|flac|m4a)$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'[\s.…·]+$', '', s)
    return s.lower()


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


#: 近似匹配的最低要求：至少这么长的公共前缀（少了就是瞎猜）
MIN_APPROX_PREFIX = 4


def _approx_audio_pool(registry, table: str, project_root, index: dict) -> list:
    """
    近似匹配的候选池：`[(比对用曲名, 真实路径, 显示名), …]`。

    两个来源（都是**表里的真路径**优先，再退到磁盘上真实存在的文件）：
      · 资源表那一张（`music` / `sounds` / `voices`）的每一行；
      · 磁盘音频索引（去重）。
    """
    out: list = []
    seen: set = set()
    rows = getattr(registry, table, None) or {}
    for key, row in (rows.items() if hasattr(rows, 'items') else ()):
        row = row or {}
        raw = str(row.get('Path') or row.get('File') or '').strip()
        path = _audio_path_from_raw(raw, project_root) if raw else None
        if path is None:
            continue
        stem = _norm_audio_stem(Path(raw).name)
        if not stem:
            continue
        p = str(path)
        if p in seen:
            continue
        seen.add(p)
        out.append((stem, p, Path(raw).name))
        # 表里的 ID 常常是 `bgm_夜晚，断电之后，不存在的教室` —— 也拿来比一次
        rid = _norm_audio_stem(re.sub(r'^(bgm|music|snd|sfx|voice)[_-]', '',
                                      str(key), flags=re.IGNORECASE))
        if rid and rid != stem:
            out.append((rid, p, Path(raw).name))
    for hits in index.values():
        for path in hits:
            p = str(path)
            if p in seen:
                continue
            seen.add(p)
            out.append((_norm_audio_stem(path.stem), p, path.name))
    return out


def _audio_path_from_raw(raw: str, project_root):
    """
    表里的 `Path` → 真实文件：**先按"相对当前项目"解释，再按原样**。

    真实项目里有一层**重复嵌套的 `sr_general/` 目录**，于是
    `sr_general/game/bgm/x.mp3` 两套基准都能命中（`…/sr_general/sr_general/…`
    与 `…/sr_general/game/…`）。先试去掉前缀的那一套，命中的就是**引擎真正
    会用的那一份**（外层短路径），不是嵌套副本。
    """
    raw = str(raw or '').replace('\\', '/').strip()
    if not raw:
        return None
    parts = raw.split('/')
    for cand in ['/'.join(parts[i:]) for i in range(1, len(parts))] + [raw]:
        path = _path_from_rel(cand, project_root)
        if path is not None:
            return path
    return None


def resolve_audio_path_info(spec, registry=None, project_root=None,
                            kind: str = 'music', allow_approx: bool = True,
                            index: dict = None) -> dict:
    """
    音频名 → `{'path', 'query', 'exact', 'approx', 'matched'}`（**详细版**）。

    用户 2026-10-05：

    > 这个你确定 bgm 导入了吗？……是否正确呢？查询一下，因为我这里没有声音……

    精确解析的候选顺序（**先按"相对当前项目"解释，再按表里原样**）：
      ① 名字本身（`bgm/615.mp3` 这种脚本写法）；
      ② 资源表 `Path` 去掉前面的目录段（真实项目的表里存的是
         `sr_general/game/bgm/x.mp3`，而我们打开的是 `…/sr_general`）；
      ③ 资源表 `Path` 原样；
      ④ 按名字在 `game/bgm|audio|sound|voice` 里找同名文件（**有界**扫一遍）。

    每一步都用"文件真的存在"来验收（`_path_from_rel` 自己会查）——
    绝不返回一个不存在的路径去骗播放器。

    精确都落空且 `allow_approx` 时，再按**公共前缀**找一次近似（真实项目实测：
    `bgm/夜晚，断电之后…….mp3` 在磁盘上根本不存在，同名的真文件叫
    `夜晚，断电之后，不存在的教室.mp3`）。近似结果 `approx=True` 且带着
    `matched` 真名 —— 界面必须把它**说出来**，绝不悄悄放一首别的曲子。
    并列第一（前缀一样长的两个候选）时**不猜**，返回找不到。
    """
    query = audio_spec_name(spec)
    out = {'path': None, 'query': query, 'exact': False, 'approx': False,
           'matched': ''}
    if not query:
        return out
    table = {'music': 'music', 'sound': 'sounds', 'sfx': 'sounds',
             'voice': 'voices'}.get(kind, 'music')
    rows = getattr(registry, table, None) or {}
    row = rows.get(query) or rows.get(str(spec or '').strip()) or {}
    raw = str(row.get('Path') or row.get('File') or '').strip()
    candidates = [query]
    parts = raw.replace('\\', '/').split('/')
    candidates.extend('/'.join(parts[i:]) for i in range(1, len(parts)))
    if raw:
        candidates.append(raw)
    for cand in candidates:
        path = _path_from_rel(cand, project_root)
        if path is not None:
            out.update(path=str(path), exact=True, matched=Path(cand).name)
            return out
    index = _audio_index(project_root) if index is None else index
    for key in (query.lower(), Path(query).name.lower(),
                Path(query).stem.lower()):
        hits = index.get(key) or []
        if hits:
            out.update(path=str(hits[0]), exact=True, matched=hits[0].name)
            return out
    # ①b 表里**文件同名**的行（表的 ID 可能跟文件名不一样：真实项目里
    #     `guitar.ogg` 挂在 `bgm_…` 之类的 ID 下）。同名就是同一个文件，
    #     这不是"猜"。
    base = Path(query).name.lower()
    for _key, row in (rows.items() if hasattr(rows, 'items') else ()):
        raw = str((row or {}).get('Path') or (row or {}).get('File') or '')
        raw = raw.strip()
        if not raw or Path(raw.replace('\\', '/')).name.lower() != base:
            continue
        path = _audio_path_from_raw(raw, project_root)
        if path is not None:
            out.update(path=str(path), exact=True, matched=Path(raw).name)
            return out
    if not allow_approx:
        return out
    stem = _norm_audio_stem(query)
    if len(stem) < MIN_APPROX_PREFIX:
        return out
    scored = []
    for cand_stem, path, label in _approx_audio_pool(registry, table,
                                                     project_root, index):
        score = _common_prefix_len(stem, cand_stem)
        if score >= MIN_APPROX_PREFIX:
            scored.append((score, path, label))
    if not scored:
        return out
    scored.sort(key=lambda t: -t[0])
    top = scored[0][0]
    winners = [s for s in scored if s[0] == top]
    if len({_norm_audio_stem(w[2]) for w in winners}) > 1:
        return out                    # 并列第一但是**不同的曲子**：不猜
    # 同名的那几份是同一首歌（真实项目里有一层重复嵌套的 `sr_general/`
    # 目录，同一首曲子会在两处；同一首也常有 mp3/wav 两种格式）：
    # 先挑**跟脚本写法同一个扩展名**的，再挑路径最短的（离项目根最近的那份
    # 就是引擎真正会用的那份）。
    ext = Path(query).suffix.lower()
    winners.sort(key=lambda w: (Path(str(w[2])).suffix.lower() != ext,
                                str(w[1]).count('\\') + str(w[1]).count('/'),
                                len(str(w[1]))))
    out.update(path=winners[0][1], approx=True, matched=winners[0][2])
    return out


def resolve_audio_path(spec, registry=None, project_root=None,
                       kind: str = 'music', allow_approx: bool = False):
    """
    音频名 → 磁盘路径（**字符串**；找不到返回 None）。

    默认**只要精确匹配**（`allow_approx=False`）——"这个名字到底有没有文件"
    是一个可以拿去写进校验报告的事实，不能被近似匹配污染。要那份"顺带告诉你
    真名是什么"的详细信息，用 `resolve_audio_path_info`。
    """
    info = resolve_audio_path_info(spec, registry, project_root, kind,
                                   allow_approx=allow_approx)
    return info.get('path')


def stage_audio_cues(frame, registry=None, project_root=None,
                     allow_approx: bool = True) -> dict:
    """
    一帧的**声音提示**。

    返回：
      · `music` / `music_name`：这一拍该放的 BGM（持续状态）；
      · `music_exact` / `music_matched`：精确命中吗 / 实际命中的文件名；
      · `once`：`[(kind, 路径)]` —— 音效与语音（换到这一拍响一次）；
      · `missing`：**脚本里写了、磁盘上找不到**的那些名字（用户 2026-10-05
        "我这里没有声音"就是这个：脚本引用的文件名不存在）；
      · `notes`：给界面直接显示的一句话（缺文件 / 近似匹配）。

    纯函数（只有磁盘查找，没有 Qt）—— 预览拿它决定"该放什么"，测试直接钉这里。
    BGM 是**持续状态**（这一拍没换就继续放），音效 / 语音是**一次性**的
    （换到这一拍就响一次）。
    """
    out = {'music': None, 'music_name': '', 'music_exact': True,
           'music_matched': '', 'once': [], 'missing': [], 'notes': []}
    if frame is None:
        return out
    spec = str(getattr(frame, 'music', '') or '').strip()
    out['music_name'] = spec
    if spec:
        info = resolve_audio_path_info(spec, registry, project_root, 'music',
                                       allow_approx=allow_approx)
        out['music'] = info['path']
        out['music_exact'] = bool(info['exact'])
        out['music_matched'] = info['matched']
        if info['path'] and info['approx']:
            out['notes'].append(
                f'♪ {spec} → 近似匹配「{info["matched"]}」'
                f'（脚本里写的这个文件名不存在，游戏里也会找不到）')
        elif not info['path']:
            out['missing'].append(spec)
            out['notes'].append(f'♪ {spec}（磁盘上找不到这个音频文件）')
    for kind in ('sound', 'voice'):
        raw = str(getattr(frame, kind, '') or '').strip()
        if not raw:
            continue
        info = resolve_audio_path_info(raw, registry, project_root, kind,
                                       allow_approx=allow_approx)
        if info['path'] is not None:
            out['once'].append((kind, info['path']))
            if info['approx']:
                out['notes'].append(
                    f'{"♫" if kind == "sound" else "🎙"} {raw} → '
                    f'近似匹配「{info["matched"]}」')
        else:
            out['missing'].append(raw)
            out['notes'].append(
                f'{"♫" if kind == "sound" else "🎙"} {raw}（磁盘上找不到）')
    return out

