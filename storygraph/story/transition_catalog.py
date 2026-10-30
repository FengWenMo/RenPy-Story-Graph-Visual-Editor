"""
`storygraph.story.transition_catalog`
=====================================
把**项目自己 `define` 的转场**读成"效果描述"（只读，不 eval 项目代码）。

背景（2026-10-30 用户第 3 条反馈）
----------------------------------
用户的过场不是"淡入"，是项目自己在 `.rpy` 里用构造器拼出来的特效：

```renpy
define dissolve = Dissolve(0.25)
define fade = Fade(0.5)
define wipeleft = ImageDissolve("images/menu/wipeleft.png", 0.5, ramplen=64)
define close_eyes = MultipleTransition([
    False, Dissolve(0.5),
    Solid("#000"), Pause(0.25),
    True])
```

`transition_expr` 负责认出"这一拍用过场"，这一层负责回答"这个过场**长
什么样**"：扫描项目 `.rpy` 里 `define 名字 = <表达式>`，递归解析成
`Effect`（kind / duration / color / ramp / segments / 人话 label）。预览只
按 `Effect` 演；**认不出就 `kind='unknown'` + label 写明"无法精确预览"**，
绝不假装淡入。

纪律
----
- **不 eval**：只用 `transition_expr` 的安全字面量解析器；`define` 的
  表达式认不出 → `unknown`（ATL 块、变量拼接都走这条）。
- **通用性**：全部从项目 `.rpy` 推导，不写死任何项目常量。扫描口径复用
  `cds_registry.find_scripts`（跳过构建产物/嵌套副本/缓存目录）。
- **纯读**：不写用户项目、不改 `Beat`。

`Effect.kind` 的取值
--------------------
`'dissolve' | 'fade' | 'pixellate' | 'imagedissolve' | 'multiple' |
'pause' | 'solid' | 'unknown'`。`'unknown'` 表示"认得出名字但工具不知道
怎么演"或"连名字都认不出" —— 消费方**只能显示 label，不许套默认淡入**。

目录是**全量**的，展示层自己过滤（Lead 2026-10-30 拍板）
--------------------------------------------------------
`scan_project_transitions` 返回项目里**全部** `define` 的转场：包括
`unknown` 的、以及定义了却没有任何卡片引用的（真机 `sr_general` 有 168 个
`unknown` 定义，来自 `renpy/common` 与项目自定义库）。**目录不做过滤**，
否则"定义了但没引用"就查不出来；**展示层只显示被这一拍引用到的**
（`effect_of(call, catalog)` 的调用方按引用过滤），卡片/预览不为没人用的
定义付像素与版面代价。第二波接入（`app/stage_render.py` / 卡片摘要）按此
口径实现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.story.cds_registry import find_scripts
from storygraph.story.transition_expr import (CallExpr, ListExpr, LiteralExpr,
                                              NameExpr, TupleExpr,
                                              TransitionCall, parse_expression)

#: 认得（能给出效果描述）的 Ren'Py 转场构造器。认的是**名字**，语义按
#: Ren'Py 公开 API 的参数顺序取字面量；不在这张表里的构造器 → `unknown`。
TRANSITION_CONSTRUCTORS = frozenset({
    'Dissolve', 'Fade', 'Pixellate', 'ImageDissolve', 'MultipleTransition',
    'Pause', 'MoveTransition', 'CropMove', 'ComposeTransition',
})

#: `define 名字 = 表达式`（名字允许点号 / 中文，Python 标识符口径）
_DEFINE_RE = re.compile(
    r'^[ \t]*define[ \t]+([^\W\d]\w*(?:\.[^\W\d]\w*)*)[ \t]*=[ \t]*(.*)$',
    re.UNICODE)
#: 一个 `define` 的表达式最多往下吃多少行（防止畸形文件把整个项目读穿）
_MAX_DEFINE_LINES = 120
#: `dict.get` 的哨兵：区分"名字不存在"与"名字在但表达式认不出（None）"
_MISSING = object()

#: `describe_effect` 在 label 为空时的兜底人话名
_KIND_LABELS = {
    'dissolve': '溶解',
    'fade': '闪屏',
    'pixellate': '像素化',
    'imagedissolve': '图像溶解',
    'multiple': '组合转场',
    'pause': '停顿',
    'solid': '纯色',
    'unknown': '无法精确预览的转场',
}


@dataclass(frozen=True)
class Effect:
    """一个过场**怎么演**（预览的输入；只描述，不含任何写回信息）。"""

    kind: str = 'unknown'     # 见模块 docstring 的取值表
    duration: float = 0.0     # 秒；0 = 时长未知（不是"瞬间"）
    color: str = ''           # fade 的目标色 / solid 的颜色
    ramp: str = ''            # imagedissolve 的遮罩图（项目里的写法，可能为空）
    ramplen: int = 0          # imagedissolve 的 ramp 长度
    segments: tuple = ()      # multiple 的子效果序列（Effect 元组）
    label: str = ''           # 人话名，如 '闪黑 1s（0.5 出 + 0 停 + 0.5 入）'
    # `fade` 的三段时长（秒）：出 / 停 / 入。渲染引擎（`app/transition_fx`）
    # 靠它们把"闪黑"真的演成"淡出→停→淡入"；缺省 0 = 老数据/非 fade，
    # 引擎会退回"把 duration 均分"（见 §1.7）。
    out_time: float = 0.0
    hold_time: float = 0.0
    in_time: float = 0.0

    def to_dict(self) -> dict:
        """可 JSON 化（探针/报告用）。"""
        return {
            'kind': self.kind,
            'duration': self.duration,
            'color': self.color,
            'ramp': self.ramp,
            'ramplen': self.ramplen,
            'out_time': self.out_time,
            'hold_time': self.hold_time,
            'in_time': self.in_time,
            'segments': [seg.to_dict() for seg in self.segments],
            'label': self.label,
        }


# ---------------------------------------------------------------------------
#  小工具：取字面量参数（读不到就是"不知道"，不猜）
# ---------------------------------------------------------------------------

def _fmt(seconds: float) -> str:
    """0.25 → '0.25'；1.0 → '1'（label 里好读）。"""
    return f'{float(seconds):g}'


def _arg(args: tuple, index: int):
    """位置参数节点（没有 → None）。"""
    return args[index] if index < len(args) else None


def _pos_or_kw(args: tuple, kwargs: dict, index: int, names: tuple):
    """
    位置参数优先、关键字参数兜底（Ren'Py 构造器两种写法都允许）。

    例：`ImageDissolve("a.png", 0.5)` 与
    `ImageDissolve(image="a.png", time=0.5)` 是同一个意思。
    """
    node = _arg(args, index)
    if node is not None:
        return node
    for name in names:
        if name in kwargs:
            return kwargs[name]
    return None


def _short_name(name: str) -> str:
    """`a.b.Dissolve` → `Dissolve`（构造器分派只看最后一段）。"""
    return (name or '').rsplit('.', 1)[-1]


def _number(node):
    """字面量数字 → float；不是字面量数字 → None（**不把变量当真**）。"""
    if isinstance(node, LiteralExpr) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    return None


def _integer(node) -> int:
    value = _number(node)
    return int(value) if value is not None else 0


def _text(node):
    """字面量字符串 → str；不是字面量字符串 → None。"""
    if isinstance(node, LiteralExpr) and isinstance(node.value, str):
        return node.value
    return None


def _duration(node, default: float):
    """`(秒数, 是否可信)`：参数缺省用 `default`；给的但不是字面量 → 不可信。"""
    if node is None:
        return float(default), True
    value = _number(node)
    if value is None:
        return 0.0, False
    return value, True


def _unknown(name: str = '', why: str = '') -> Effect:
    """`kind='unknown'` 的 Effect —— label 一定写明"无法精确预览"。"""
    text = name or '这段转场'
    if why:
        text += f'（{why}，无法精确预览）'
    else:
        text += '（无法精确预览）'
    return Effect(kind='unknown', label=text)


# ---------------------------------------------------------------------------
#  表达式节点 → Effect
# ---------------------------------------------------------------------------

def _effect_from_node(node, resolve) -> Effect:
    """
    表达式节点 → `Effect`。

    `resolve(名字)` 负责把**名字引用**翻成 Effect（项目 `define` 过的
    转场名，如 `MultipleTransition([False, dissolve_scene, True])`）。
    """
    if isinstance(node, CallExpr):
        return _effect_of_constructor(node, resolve)
    if isinstance(node, NameExpr):
        return resolve(node.text)
    if isinstance(node, TupleExpr) and len(node.items) == 1:
        # `(Dissolve(0.5))` 这种多余的括号在 Python 里就是里面那个值
        return _effect_from_node(node.items[0], resolve)
    if isinstance(node, LiteralExpr):
        return _unknown(repr(node.value), '这是字面量，不是转场')
    return _unknown('', '认不出这段转场定义')


def _effect_of_constructor(node: CallExpr, resolve) -> Effect:
    """一个构造器调用 → `Effect`（按 Ren'Py 公开 API 的参数顺序取字面量）。"""
    name = _short_name(node.name)
    args = node.args
    kwargs = node.kwargs
    if name == 'Dissolve':
        duration, known = _duration(
            _pos_or_kw(args, kwargs, 0, ('delay', 'time')), 0.5)
        if not known:
            return Effect(kind='dissolve',
                          label='溶解（时长未知，无法精确预览）')
        return Effect(kind='dissolve', duration=duration,
                      label=f'溶解 {_fmt(duration)}s')
    if name == 'Fade':
        return _fade_effect(args, kwargs)
    if name == 'Pixellate':
        duration, known = _duration(
            _pos_or_kw(args, kwargs, 0, ('time', 'delay')), 0.5)
        if not known:
            return Effect(kind='pixellate',
                          label='像素化（时长未知，无法精确预览）')
        return Effect(kind='pixellate', duration=duration,
                      label=f'像素化 {_fmt(duration)}s')
    if name == 'ImageDissolve':
        return _image_dissolve_effect(args, kwargs)
    if name == 'MultipleTransition':
        return _multiple_effect(args, kwargs, resolve)
    if name == 'Pause':
        duration, known = _duration(
            _pos_or_kw(args, kwargs, 0, ('delay', 'time')), 0.0)
        if not known:
            return Effect(kind='pause',
                          label='停顿（时长未知，无法精确预览）')
        return Effect(kind='pause', duration=duration,
                      label=f'停顿 {_fmt(duration)}s')
    if name == 'Solid':
        color = _text(_pos_or_kw(args, kwargs, 0, ('color',))) or ''
        label = f'纯色 {color}' if color else '纯色（颜色认不出）'
        return Effect(kind='solid', color=color, label=label)
    if name in ('MoveTransition', 'CropMove', 'ComposeTransition'):
        # 认得出名字、但工具没有"平移/裁剪/并行合成"的预览实现 →
        # 如实标 unknown（预览按"无法精确预览"处理，绝不套淡入）。
        duration, _known = _duration(
            _pos_or_kw(args, kwargs, 0, ('delay', 'time')), 0.0)
        label = {
            'MoveTransition': '动作转场（MoveTransition）',
            'CropMove': '裁剪转场（CropMove）',
            'ComposeTransition': '并合转场（ComposeTransition）',
        }[name]
        if duration > 0:
            label += f' {_fmt(duration)}s'
        return Effect(kind='unknown', duration=duration,
                      label=label + '，无法精确预览')
    return _unknown(name)


def _fade_effect(args: tuple, kwargs: dict) -> Effect:
    """
    `Fade(out_time, hold_time=None, in_time=None, color="#000")`。

    缺省语义按 Ren'Py 公开 API：`hold_time` 缺省 0、`in_time` 缺省等于
    `out_time`；总时长 = 出 + 停 + 入（预览按这个时长演"闪一下"）。三段
    时长分别存进 `Effect.out_time / hold_time / in_time`，渲染引擎据此演
    "淡出 → 停 → 淡入"，而不是把总时长均分。
    """
    out_node = _pos_or_kw(args, kwargs, 0, ('out_time',))
    hold_node = _pos_or_kw(args, kwargs, 1, ('hold_time',))
    in_node = _pos_or_kw(args, kwargs, 2, ('in_time',))
    color_node = _pos_or_kw(args, kwargs, 3, ('color',))
    color = _text(color_node) or '#000'
    out, out_ok = _duration(out_node, 0.5)
    hold, hold_ok = _duration(hold_node, 0.0)
    in_time, in_ok = _duration(in_node, out)
    if not (out_ok and hold_ok and in_ok):
        return Effect(kind='fade', color=color,
                      label='闪屏（时长未知，无法精确预览）')
    total = out + hold + in_time
    label = (f'闪黑 {_fmt(total)}s（{_fmt(out)} 出 + {_fmt(hold)} 停 + '
             f'{_fmt(in_time)} 入）' if _is_black(color)
             else f'闪到 {color} {_fmt(total)}s')
    return Effect(kind='fade', duration=total, color=color, label=label,
                  out_time=out, hold_time=hold, in_time=in_time)


def _is_black(color: str) -> bool:
    return (color or '').strip().lower() in ('#000', '#000000', 'black')


def _image_dissolve_effect(args: tuple, kwargs: dict) -> Effect:
    """
    `ImageDissolve(image, time, ramplen=8, ramp=None, reverse=False)`。

    `image` 是遮罩图（项目里的写法，如 `"images/menu/wipeleft.png"`）；
    它**不是**资产 ID，这一层不查资源表，原样带进 `Effect.ramp`。
    """
    ramp = _text(_pos_or_kw(args, kwargs, 0, ('image', 'imagedissolve'))) or ''
    time_node = _pos_or_kw(args, kwargs, 1, ('time', 'delay'))
    if time_node is None:
        duration, known = 0.0, False
    else:
        duration, known = _duration(time_node, 0.0)
    ramplen = _integer(_pos_or_kw(args, kwargs, 2, ('ramplen',)))
    if not known:
        return Effect(kind='imagedissolve', ramp=ramp, ramplen=ramplen,
                      label='图像溶解（时长未知，无法精确预览）')
    label = f'图像溶解 {_fmt(duration)}s'
    if ramp:
        label += f'（{ramp}）'
    return Effect(kind='imagedissolve', duration=duration, ramp=ramp,
                  ramplen=ramplen, label=label)


def _multiple_effect(args: tuple, kwargs: dict, resolve) -> Effect:
    """
    `MultipleTransition([<状态>, <过渡>, <状态>, <过渡>, …, <状态>])`。

    列表是"状态 / 过渡"交替的：`False`、`True`、`Solid("#000")` 是**状态**
    （换哪一帧画面），`Dissolve(…)`、`Pause(…)` 才是**过渡段**。预览只用
    过渡段；状态只影响"过渡到哪张画面"，这一层不模拟。
    """
    list_node = _pos_or_kw(args, kwargs, 0, ('transitions', 'args'))
    if isinstance(list_node, (ListExpr, TupleExpr)):
        items = list_node.items
    else:
        return _unknown('MultipleTransition', '参数不是字面量列表')
    segments = []
    for item in items:
        effect = _segment_effect(item, resolve)
        if effect is not None:
            segments.append(effect)
    if not segments:
        return Effect(kind='multiple',
                      label='组合转场（没有可识别的过渡段，无法精确预览）')
    duration = sum(seg.duration for seg in segments)
    label = '组合：' + ' → '.join(describe_effect(seg) for seg in segments)
    return Effect(kind='multiple', duration=duration,
                  segments=tuple(segments), label=label)


def _segment_effect(item, resolve):
    """`MultipleTransition` 列表里的一项 → 过渡段 Effect（状态 → None）。"""
    if isinstance(item, NameExpr):
        effect = resolve(item.text)
        return None if effect.kind == 'solid' else effect
    if isinstance(item, CallExpr):
        short = _short_name(item.name)
        if short == 'Solid':
            return None                        # 画面状态，不是过渡段
        if short in TRANSITION_CONSTRUCTORS:
            return _effect_of_constructor(item, resolve)
        # 不在已知表里的调用：可能是项目自定义的过渡 → 留成 unknown 段，
        # 比悄悄当成"画面状态"诚实（label 会写明认不出）。
        return _unknown(short)
    return None                                # False / True / 字符串…都是状态


# ---------------------------------------------------------------------------
#  扫项目：`define 名字 = <表达式>`
# ---------------------------------------------------------------------------

def _expr_complete(text: str) -> bool:
    """括号/引号都闭合了吗？（多行 `define` 往下吃行的判据）"""
    depth = 0
    quote = ''
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == '\\':
                i += 2
                continue
            if ch == quote:
                quote = ''
            i += 1
            continue
        if ch in '"\'':
            quote = ch
            i += 1
            continue
        if ch == '#':
            nl = text.find('\n', i)
            if nl < 0:
                break
            i = nl + 1
            continue
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        i += 1
    return depth <= 0 and not quote


def _is_config_value(node) -> bool:
    """纯字面量（字符串/数字/布尔/None/字面量列表）不是转场定义。"""
    if isinstance(node, LiteralExpr):
        return True
    if isinstance(node, (ListExpr, TupleExpr)):
        return all(_is_config_value(item) for item in node.items)
    return False


def parse_define_transitions(text: str) -> dict:
    """
    一份 `.rpy` 文本 → `{名字: 表达式节点 | None}`。

    `None` 表示"`define` 在，但表达式认不出"（ATL 块、变量拼接…），
    仍然占位，好让调用方把"项目里没有这个转场"和"有但认不出"分开说。
    纯字面量的配置项（`define gui.text_size = 33`）**不进**结果。
    """
    src = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    lines = src.split('\n')
    nodes: dict = {}
    i = 0
    while i < len(lines):
        match = _DEFINE_RE.match(lines[i])
        if not match:
            i += 1
            continue
        name = match.group(1)
        buf = [match.group(2)]
        while (not _expr_complete('\n'.join(buf))
               and i + 1 < len(lines) and len(buf) < _MAX_DEFINE_LINES):
            i += 1
            buf.append(lines[i])
        node = parse_expression('\n'.join(buf).strip())
        if node is None:
            # 点号名字多半是 `gui.*` / `config.*` 之类配置，不进来
            if '.' not in name:
                nodes.setdefault(name, None)
        elif not _is_config_value(node):
            nodes.setdefault(name, node)
        i += 1
    return nodes


def _project_define_nodes(project_root) -> dict:
    """项目里全部 `define` → `{名字: 节点|None}`（同名先到先得）。"""
    nodes: dict = {}
    try:
        scripts = find_scripts(project_root)
    except Exception as e:                     # noqa: BLE001 - 项目目录坏了不该崩
        swallow('story.transition_catalog.find_scripts', e)
        return nodes
    for path in scripts:
        try:
            text = Path(path).read_text(encoding='utf-8', errors='ignore')
        except OSError as e:
            swallow('story.transition_catalog.read_define_file', e)
            continue
        for name, node in parse_define_transitions(text).items():
            nodes.setdefault(name, node)
    return nodes


def scan_project_transitions(project_root) -> dict:
    """
    项目 `.rpy` 里 `define` 的转场名 → `Effect`（递归解析，允许 A 引用 B）。

    认不出定义的以 `unknown` 进结果（label 说明原因），不当它不存在。
    """
    nodes = _project_define_nodes(project_root)
    memo: dict = {}

    def resolve(name: str, stack: tuple = ()) -> Effect:
        if name in memo:
            return memo[name]
        if name in stack:
            return _unknown(name, '循环引用')
        node = nodes.get(name, _MISSING)
        if node is _MISSING:
            return _unknown(name, '项目里没有 define 过这个名字')
        if node is None:
            return _unknown(name, '定义认不出')
        effect = _effect_from_node(
            node, lambda child, st=stack + (name,): resolve(child, st))
        memo[name] = effect
        return effect

    return {name: resolve(name) for name in nodes}


def _catalog_lookup(name: str, catalog: dict) -> Effect:
    effect = catalog.get(name)
    if isinstance(effect, Effect):
        return effect
    return _unknown(name, '项目里没有 define 过这个名字')


def effect_of(call: TransitionCall, catalog: dict) -> Effect:
    """
    一次过场调用 + 项目转场目录 → `Effect`。

    `catalog` 来自 `scan_project_transitions`；没有目录也能用（裸构造器
    调用照样能解析；裸名字只能如实标"认不出"）。
    """
    if call is None:
        return Effect(kind='unknown', label='（没有过场）')
    expr = str(getattr(call, 'expr', '') or '')
    node = parse_expression(expr)
    if node is None:
        return _unknown(str(getattr(call, 'name', '') or expr or '转场'),
                        '表达式认不出')
    return _effect_from_node(node, lambda name: _catalog_lookup(name, catalog or {}))


def describe_effect(effect: Effect) -> str:
    """`Effect` → 一句人话（无 label 时按 kind 兜底拼）。"""
    if not isinstance(effect, Effect):
        return '（无过场）'
    if effect.label:
        return effect.label
    text = _KIND_LABELS.get(effect.kind, effect.kind or '过场')
    if effect.color:
        text += f' {effect.color}'
    if effect.duration > 0:
        text += f' {_fmt(effect.duration)}s'
    return text


__all__ = [
    'TRANSITION_CONSTRUCTORS', 'Effect', 'parse_define_transitions',
    'scan_project_transitions', 'effect_of', 'describe_effect',
]
