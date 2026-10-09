"""
`storygraph.story.layered_model`
================================
**合成立绘（layeredimage）的通用模型**。

设计文档：`docs/设计_合成立绘合成与姿势切换_2026-10-05.md`。

为什么要有这个模块（用户 2026-10-05）：

> 解析 layeredimage 的 group/attribute + 读那张裁剪表 + 在预览里叠图层，
> 同时保持工具的通用性（要适配所有用 Ren'Py、用资源分离方式编排演出的用户）

真实项目（`sr_general`）的做法是 **Ren'Py 标准 displayable**：

    layeredimage ye:
        image_format 'images/characters/ye/{image}.png'
        group expressions when not base_handdown and not base_swimsuit:
            attribute happy default:
                'happy'                                  # 整图
        group expressions when base_handdown:
            attribute happy default:
                ye_clip_expr('happy', '_hd_cut.png')     # 只有面部
        group outfit when base_handdown:
            attribute base_handdown:
                ye_clip_outfit('base_handdown.png', '_hd_keep.png')

    init -1 python:
        def ye_clip_expr(expr, cut_mask="_hd_cut.png"):
            return AlphaMask(
                "images/characters/ye/" + expr + ".png",
                "images/characters/ye/" + cut_mask)

所以"读那张裁剪表"= **把这些 displayable 表达式展开**（`AlphaMask(图, 蒙版)`
只读蒙版的 alpha 通道），不需要读像素表。

本模块**只做静态解析**（不 import、不 eval 项目代码）；认不出的结构一律
**明说**并降级，绝不猜。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.safeguard import swallow

# ---------------------------------------------------------------------------
#  数据结构
# ---------------------------------------------------------------------------


@dataclass
class LayerAttr:
    """layeredimage 里的一个 `attribute`。"""

    name: str
    group: str = ''
    default: bool = False
    exprs: list = field(default_factory=list)     # displayable 表达式源码

    def to_dict(self) -> dict:
        return {'name': self.name, 'group': self.group,
                'default': self.default, 'exprs': list(self.exprs)}


@dataclass
class LayerGroup:
    """一个 `group`（`condition` 为空字符串 = 永远成立）。"""

    name: str
    condition: str = ''
    attrs: dict = field(default_factory=dict)      # name -> LayerAttr
    order: int = 0                                 # 声明顺序 = 叠放顺序

    def to_dict(self) -> dict:
        return {'name': self.name, 'condition': self.condition,
                'attrs': {k: v.to_dict() for k, v in self.attrs.items()},
                'order': self.order}


@dataclass
class LayeredImage:
    """一个 `layeredimage <tag>:` 的声明。"""

    tag: str
    image_format: str = ''
    always: list = field(default_factory=list)
    groups: list = field(default_factory=list)     # [LayerGroup, …]

    def all_attrs(self) -> dict:
        out = {}
        for g in self.groups:
            for name, attr in g.attrs.items():
                out.setdefault(name, attr)
        return out

    def to_dict(self) -> dict:
        return {'tag': self.tag, 'image_format': self.image_format,
                'always': list(self.always),
                'groups': [g.to_dict() for g in self.groups]}


@dataclass
class LayerPlan:
    """
    一张立绘**怎么叠**的结论。

    `layers` 是按叠放顺序排好的层：

        {'path': 绝对路径|None,      # 图片文件
         'mask': 绝对路径|None,      # AlphaMask 的蒙版（alpha 决定保留谁）
         'crop': (x,y,w,h)|None,
         'offset': (x,y)|None,
         'note': str}

    `ok=False` 时调用方应当退回今天的老路（单图 / 蓝框）——
    **认不出就说认不出**，不许画错。
    """

    tag: str = ''
    ok: bool = False
    layers: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    pose: str = ''                     # 当前姿势值（'' = 普通/无姿势）
    pose_options: list = field(default_factory=list)
    pose_labels: dict = field(default_factory=dict)
    attrs_used: list = field(default_factory=list)

    def paths(self) -> list:
        return [ly.get('path') for ly in self.layers if ly.get('path')]


@dataclass
class LayerModel:
    """整个项目的合成立绘模型。"""

    images: dict = field(default_factory=dict)      # tag -> LayeredImage
    helpers: dict = field(default_factory=dict)     # 函数名 -> 函数体源码
    poses: dict = field(default_factory=dict)       # tag -> ['', 'a', 'b']
    pose_labels: dict = field(default_factory=dict)  # 'ye|base_handdown' -> '泳装'
    warnings: list = field(default_factory=list)

    def has(self, tag: str) -> bool:
        return str(tag or '') in self.images

    def pose_options(self, tag: str) -> list:
        """这个 tag 的姿势取值（含 `''` = 普通）。认不出就返回 []。"""
        tag = str(tag or '')
        if tag in self.poses:
            return list(self.poses[tag])
        return self._pose_from_groups(tag)

    def _pose_from_groups(self, tag: str) -> list:
        """
        没有循环表时，从 layeredimage 自己推姿势维度：

        **出现在某个 `when` 条件里、而且自己也有层**的属性 = 姿势/服装
        （实测项目：`tall` / `gown` / `base_handdown` … 正是这种写法；
        纯表情属性（`calm` / `glad`）从不进条件）。

        ⚠ 这是**启发式**：循环表存在时以表为准；推错了也只影响"姿势提示"，
        不影响叠层结果（`resolve_layers` 走的是组条件）。
        """
        img = self.images.get(tag)
        if img is None:
            return []
        declared = set(img.all_attrs())
        cond_attrs = set()
        for g in img.groups:
            for name in re.findall(r'[A-Za-z_][\w]*', g.condition or ''):
                if name not in _COND_KEYWORDS:
                    cond_attrs.add(name)
        poses = sorted(cond_attrs & declared)
        return [''] + poses if poses else []


_COND_KEYWORDS = {'and', 'or', 'not', 'True', 'False', 'None', 'in'}


# ---------------------------------------------------------------------------
#  解析：layeredimage 块
# ---------------------------------------------------------------------------

_RE_LAYERED = re.compile(r'^\s*layeredimage\s+([\w.]+)\s*:')
_RE_FORMAT = re.compile(r"""^\s*image_format\s+['"](.+?)['"]""")
_RE_GROUP = re.compile(
    r"""^\s*group\s+([\w]+)(.*?):\s*$""")
_RE_ATTR = re.compile(r"""^\s*attribute\s+([\w]+)(.*?):?\s*$""")
_RE_ALWAYS = re.compile(r'^\s*always\s*:?\s*$')


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(' \t'))


def _split_conditions(rest: str) -> str:
    """
    `group expressions when not a and not b:` 的尾巴 → 条件表达式。

    支持 `when <expr>` / `if_any [a, b]` / `if_all [a, b]` / `if_not [a]`。
    """
    rest = (rest or '').strip()
    if not rest:
        return ''
    m = re.search(r'\bwhen\s+(.+)$', rest)
    if m:
        return m.group(1).strip()
    m = re.search(r'\b(if_any|if_all|if_not)\s*\[([^\]]*)\]', rest)
    if m:
        kind, items = m.group(1), [s.strip() for s in m.group(2).split(',')
                                   if s.strip()]
        join = {'if_any': ' or ', 'if_all': ' and ', 'if_not': ' or '}[kind]
        expr = join.join(items)
        return f'not ({expr})' if kind == 'if_not' else expr
    return ''


def parse_layeredimage_block(lines: list, start: int) -> tuple:
    """
    从 `lines[start]`（`layeredimage <tag>:`）开始解析一个块。

    返回 `(LayeredImage, 下一行下标)`。缩进决定层级 —— 与 Ren'Py 一致。
    """
    m = _RE_LAYERED.match(lines[start])
    if not m:
        raise ValueError('不是 layeredimage 行')
    img = LayeredImage(tag=m.group(1))
    base_indent = _indent(lines[start])
    i = start + 1
    cur_group = None
    cur_attr = None
    attr_expr_indent = None
    order = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        stripped = line.strip()
        i += 1
        if not stripped or stripped.startswith('#'):
            continue
        ind = _indent(line)
        if ind <= base_indent:
            i -= 1
            break
        # —— 顶层子句 ——
        if ind <= base_indent + 4 or stripped.startswith(
                ('group ', 'attribute ', 'always', 'image_format')):
            gm = _RE_GROUP.match(line)
            am = _RE_ATTR.match(line)
            if _RE_FORMAT.match(line):
                img.image_format = _RE_FORMAT.match(line).group(1)
                cur_group = cur_attr = None
                attr_expr_indent = None
                continue
            if _RE_ALWAYS.match(line):
                cur_group = cur_attr = None
                attr_expr_indent = ind
                continue
            if gm and not am:
                order += 1
                cur_group = LayerGroup(name=gm.group(1),
                                       condition=_split_conditions(gm.group(2)),
                                       order=order)
                img.groups.append(cur_group)
                cur_attr = None
                attr_expr_indent = None
                continue
            if am:
                name, rest = am.group(1), am.group(2) or ''
                is_default = bool(re.search(r'\bdefault\b', rest))
                attr = LayerAttr(name=name, default=is_default)
                if cur_group is None:
                    # 顶层 attribute：单独一个"永远成立"的组
                    cur_group = LayerGroup(name='', order=order)
                    img.groups.append(cur_group)
                    order += 1
                attr.group = cur_group.name
                cur_group.attrs[name] = attr
                cur_attr = attr
                attr_expr_indent = ind
                # `attribute happy default: 'happy'`（同一行给值）
                inline = rest.split(':', 1)[1].strip() if ':' in rest else ''
                if inline:
                    attr.exprs.append(inline)
                    cur_attr = None
                continue
        # —— 表达式行（属于最近一个 attribute / always）——
        if cur_attr is not None and ind >= (attr_expr_indent or ind):
            cur_attr.exprs.append(stripped)
            continue
        if cur_attr is None and attr_expr_indent is not None \
                and ind >= attr_expr_indent:
            img.always.append(stripped)
            continue
    return img, i


def parse_layeredimages(project_root) -> dict:
    """扫描项目里所有 `.rpy`，解析全部 `layeredimage` 块。"""
    root = Path(project_root)
    out = {}
    game = root / 'game' if (root / 'game').is_dir() else root
    for f in sorted(game.rglob('*.rpy')):
        try:
            lines = f.read_text(encoding='utf-8', errors='ignore') \
                .splitlines()
        except OSError as e:                    # noqa: BLE001 - 读不了就跳过
            swallow('story.layered_model.parse_layeredimages.read', e)
            continue
        for idx, line in enumerate(lines):
            if _RE_LAYERED.match(line):
                try:
                    img, _nxt = parse_layeredimage_block(lines, idx)
                except Exception as e:          # noqa: BLE001 - 坏块跳过
                    swallow('story.layered_model.parse_layeredimages', e)
                    continue
                out.setdefault(img.tag, img)
    return out


# ---------------------------------------------------------------------------
#  解析：init python 里的辅助函数（"那张裁剪表"的载体）
# ---------------------------------------------------------------------------

_RE_DEF = re.compile(r'^(\s*)def\s+([\w]+)\s*\(([^)]*)\)\s*:')


def parse_helper_functions(project_root) -> dict:
    """
    收集项目里 `init python:` 块中的 `def <name>(...):` 函数体源码。

    只作为**展开模板**用（参数代入后解析 displayable），**绝不执行**。
    """
    root = Path(project_root)
    game = root / 'game' if (root / 'game').is_dir() else root
    helpers = {}
    for f in sorted(game.rglob('*.rpy')):
        try:
            lines = f.read_text(encoding='utf-8', errors='ignore') \
                .splitlines()
        except OSError as e:                    # noqa: BLE001 - 读不了就跳过
            swallow('story.layered_model.parse_helper_functions', e)
            continue
        i = 0
        while i < len(lines):
            m = _RE_DEF.match(lines[i])
            if not m:
                i += 1
                continue
            indent, name, params = m.group(1), m.group(2), m.group(3)
            body = []
            j = i + 1
            while j < len(lines):
                ln = lines[j]
                if ln.strip() and _indent(ln) <= len(indent):
                    break
                body.append(ln.strip())
                j += 1
            helpers.setdefault(name, {'params': _parse_params(params),
                                      'body': body})
            i = j
    return helpers


def _parse_params(text: str) -> list:
    out = []
    for part in (text or '').split(','):
        part = part.strip()
        if not part or part in ('*args', '**kwargs'):
            continue
        if '=' in part:
            n, d = part.split('=', 1)
            out.append((n.strip(), d.strip()))
        else:
            out.append((part, None))
    return out


# ---------------------------------------------------------------------------
#  表达式：静态展开（不 eval）
# ---------------------------------------------------------------------------

#: 认识的 displayable 构造 → 展开规则
_DISPLAYABLES = {'AlphaMask', 'Crop', 'Composite', 'LiveComposite',
                 'Transform', 'ConditionSwitch', 'Fixed', 'im.AlphaMask',
                 'im.Crop', 'Flatten', 'Scale', 'Null'}


class ExprNode:
    """极简表达式树：Call / Const / Name / Tuple / Add / Attr / Sub。"""

    __slots__ = ('kind', 'value', 'args')

    def __init__(self, kind, value=None, args=None):
        self.kind = kind
        self.value = value
        self.args = args or []


def _tokenize(src: str) -> list:
    tokens, i, n = [], 0, len(src)
    while i < n:
        ch = src[i]
        if ch in ' \t\r\n':
            i += 1
            continue
        if ch in '(),':
            tokens.append((ch, ch))
            i += 1
            continue
        if ch == '+':
            tokens.append(('op', '+'))
            i += 1
            continue
        if ch in '\'"':
            quote, j = ch, i + 1
            buf = []
            while j < n and src[j] != quote:
                if src[j] == '\\' and j + 1 < n:
                    buf.append(src[j + 1])
                    j += 2
                    continue
                buf.append(src[j])
                j += 1
            tokens.append(('str', ''.join(buf)))
            i = j + 1
            continue
        m = re.match(r'[-\d.]+', src[i:])
        if m and (ch.isdigit() or ch == '-'):
            tokens.append(('num', m.group(0)))
            i += len(m.group(0))
            continue
        m = re.match(r'[A-Za-z_][\w.]*', src[i:])
        if m:
            tokens.append(('name', m.group(0)))
            i += len(m.group(0))
            continue
        i += 1                                  # 认不出的字符：跳过
    return tokens


def parse_expr(src: str) -> ExprNode:
    """`src` → 表达式树（只支持调用/字面量/拼接/元组）。"""
    tokens = _tokenize(src)
    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else (None, None)

    def eat(kind=None, value=None):
        k, v = peek()
        if kind is not None and k != kind:
            return None
        if value is not None and v != value:
            return None
        pos[0] += 1
        return (k, v)

    def parse_atom():
        k, v = peek()
        if k == 'str':
            eat()
            return ExprNode('const', v)
        if k == 'num':
            eat()
            return ExprNode('const', float(v) if '.' in v else int(v))
        if k == 'name':
            eat()
            if peek()[0] == '(':
                eat('(')
                args = []
                if peek()[0] != ')':
                    while True:
                        args.append(parse_add())
                        if not eat(','):
                            break
                eat(')')
                return ExprNode('call', v, args)
            return ExprNode('name', v)
        if k == '(':
            eat('(')
            first = parse_add()
            if peek()[0] == ',':
                items = [first]
                while eat(','):
                    if peek()[0] == ')':
                        break
                    items.append(parse_add())
                eat(')')
                return ExprNode('tuple', None, items)
            eat(')')
            return first
        return None

    def parse_add():
        node = parse_atom()
        while peek() == ('op', '+'):
            eat('op', '+')
            rhs = parse_atom()
            node = ExprNode('add', None, [node, rhs])
        return node

    return parse_add() or ExprNode('const', None)


def expand_displayable(expr, helpers: dict, image_format: str = '',
                       params: dict = None, depth: int = 0) -> list:
    """
    displayable 表达式 → 层列表 `[{path|mask|crop|offset|note}, …]`。

    `params` 是已知实参（展开辅助函数时用）。`depth` 防递归。
    """
    if depth > 6:
        return [{'note': f'展开太深，放弃：{str(expr)[:60]}'}]
    params = params or {}
    src = str(expr or '').strip()
    if not src:
        return []
    node = parse_expr(src)
    return _expand_node(node, helpers, image_format, params, depth)


def _return_expr(body: list) -> str:
    """
    函数体 → `return` 的那个**完整表达式**（可能跨多行）。

    ⚠ 实测踩到：真实项目的辅助函数是

        def ye_clip_expr(expr, cut_mask="_hd_cut.png"):
            \"\"\"…\"\"\"
            return AlphaMask(
                "images/characters/ye/" + expr + ".png",
                "images/characters/ye/" + cut_mask)

    —— `return` 后面那行只有 `AlphaMask(`，只看一行就永远"不认识"。
    所以从这里开始**按括号配平拼到闭合**。
    """
    for idx, line in enumerate(body):
        if 'return' not in line:
            continue
        text = line.split('return', 1)[1].strip()
        depth = text.count('(') - text.count(')')
        j = idx + 1
        while depth > 0 and j < len(body):
            nxt = body[j].strip()
            text += ' ' + nxt
            depth += nxt.count('(') - nxt.count(')')
            j += 1
        return text.rstrip(',').strip()
    return ''


def _value_of(node, helpers, image_format, params, depth):
    """把节点求成"字符串 / 元组 / 未知"（未知返回 None）。"""
    if node is None:
        return None
    if node.kind == 'const':
        return node.value
    if node.kind == 'name':
        if node.value in params:
            return params[node.value]
        return None
    if node.kind == 'add':
        a = _value_of(node.args[0], helpers, image_format, params, depth)
        b = _value_of(node.args[1], helpers, image_format, params, depth)
        if isinstance(a, str) and isinstance(b, str):
            return a + b
        return None
    if node.kind == 'tuple':
        out = []
        for a in node.args:
            v = _value_of(a, helpers, image_format, params, depth)
            if v is None:
                return None
            out.append(v)
        return tuple(out)
    if node.kind == 'call':
        name = node.value
        if name in helpers:
            fn = helpers[name]
            fn_params = dict(params)
            for idx, (pname, pdefault) in enumerate(fn['params']):
                if idx < len(node.args):
                    v = _value_of(node.args[idx], helpers, image_format,
                                  params, depth)
                else:
                    v = _value_of(parse_expr(pdefault), helpers,
                                  image_format, params, depth) \
                        if pdefault is not None else None
                if v is not None:
                    fn_params[pname] = v
            expr = _return_expr(fn['body'])
            if expr:
                return _value_of(parse_expr(expr), helpers, image_format,
                                 fn_params, depth + 1)
        return None
    return None


def _image_path(text: str, image_format: str) -> str:
    """`'happy'` + `image_format` → 真实相对路径（认不出就原样）。"""
    text = str(text or '')
    if not text:
        return ''
    if text.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
        return text
    if image_format and '{image}' in image_format:
        return image_format.replace('{image}', text)
    return text


def _expand_helper_call(name, params, args, depth, helpers, image_format):
    """`_expand_node` 里"命中 helpers（`resize`/`crop` 这类）"那一支（M4 搬出，行为一字不改）。返回展开结果。"""
    if name in helpers:
        fn = helpers[name]
        fn_params = dict(params)
        for idx, (pname, pdefault) in enumerate(fn['params']):
            if idx < len(args):
                v = _value_of(args[idx], helpers, image_format, params, depth)
                if v is None and args[idx].kind == 'const':
                    v = args[idx].value
                if v is None:
                    v = args[idx].value if args[idx].kind == 'name' else None
            else:
                v = (_value_of(parse_expr(pdefault), helpers, image_format,
                               params, depth) if pdefault is not None else None)
            if v is not None:
                fn_params[pname] = v
        inner = _return_expr(fn['body'])
        if not inner:
            return [{'note': f'辅助函数 {name} 里没有可解析的 return'}]
        out = expand_displayable(inner, helpers, image_format,
                                 fn_params, depth + 1)
        return out or [{'note': f'辅助函数 {name} 展开为空'}]

def _expand_composite_call(name, params, args, depth, helpers, image_format, idx):
    """`Composite` / `LiveComposite` 那一支（M4 搬出，行为一字不改）。"""
    if name in ('Composite', 'LiveComposite'):
        out = []
        idx = 0
        if args and args[0].kind == 'tuple':
            idx = 1
        while idx + 1 < len(args) + 1 and idx < len(args):
            off = _value_of(args[idx], helpers, image_format, params, depth) \
                if args[idx].kind == 'tuple' else None
            img_node = args[idx + 1] if idx + 1 < len(args) else None
            if img_node is None:
                break
            for layer in _expand_node(img_node, helpers, image_format,
                                      params, depth):
                if off and isinstance(off, tuple) and len(off) == 2:
                    layer = dict(layer, offset=(int(off[0]), int(off[1])))
                out.append(layer)
            idx += 2
        return out or [{'note': f'{name} 没展开出层'}]

def _expand_node(node, helpers, image_format, params, depth) -> list:
    if node is None:
        return []
    if node.kind in ('const', 'name', 'add'):
        val = _value_of(node, helpers, image_format, params, depth)
        if isinstance(val, str) and val:
            return [{'path': _image_path(val, image_format), 'mask': None,
                     'crop': None, 'offset': None, 'note': ''}]
        return [{'note': f'认不出的值：{_src_of(node)}'}]
    if node.kind == 'tuple':
        return []
    if node.kind != 'call':
        return [{'note': f'认不出的表达式：{_src_of(node)}'}]
    name = node.value.split('.')[-1]
    args = node.args
    # 项目里的辅助函数：代入参数后重解析
    if name in helpers:
        return _expand_helper_call(name, params, args, depth,
                                   helpers, image_format)
    if name == 'AlphaMask' and len(args) >= 2:
        img = _value_of(args[0], helpers, image_format, params, depth)
        mask = _value_of(args[1], helpers, image_format, params, depth)
        if isinstance(img, str) and isinstance(mask, str):
            return [{'path': _image_path(img, image_format),
                     'mask': _image_path(mask, image_format),
                     'crop': None, 'offset': None, 'note': 'AlphaMask'}]
        return [{'note': 'AlphaMask 的参数认不出'}]
    if name in ('Crop', 'im.Crop') and len(args) >= 2:
        box = _value_of(args[0], helpers, image_format, params, depth)
        img = _value_of(args[1], helpers, image_format, params, depth)
        if isinstance(img, str) and isinstance(box, tuple) and len(box) == 4:
            return [{'path': _image_path(img, image_format), 'mask': None,
                     'crop': tuple(int(v) for v in box), 'offset': None,
                     'note': 'Crop'}]
        return [{'note': 'Crop 的参数认不出'}]
    if name in ('Composite', 'LiveComposite'):
        return _expand_composite_call(name, params, args, depth,
                                      helpers, image_format, idx)
    if name in ('Transform', 'Flatten', 'Scale') and args:
        out = _expand_node(args[0], helpers, image_format, params, depth)
        return [dict(ly, note=(ly.get('note') or '') + f'（{name} 未处理）')
                for ly in out]
    if name == 'Null':
        return []
    return [{'note': f'不认识的 displayable：{name}'}]


def _src_of(node) -> str:
    if node is None:
        return ''
    if node.kind == 'const':
        return repr(node.value)
    if node.kind == 'name':
        return str(node.value)
    if node.kind == 'call':
        return f'{node.value}(…)'
    if node.kind == 'add':
        return f'{_src_of(node.args[0])} + {_src_of(node.args[1])}'
    return node.kind


# ---------------------------------------------------------------------------
#  条件求值
# ---------------------------------------------------------------------------

_RE_TOK = re.compile(r'\(|\)|\band\b|\bor\b|\bnot\b|True|False|[A-Za-z_][\w]*')


def evaluate_condition(expr: str, attrs) -> object:
    """
    按"属性名在不在集合里"求值。返回 `True`/`False`；**认不出返回 None**。
    """
    expr = str(expr or '').strip()
    attrs = {str(a).strip().lower() for a in (attrs or ()) if str(a).strip()}
    if not expr:
        return True
    tokens = _RE_TOK.findall(expr)
    if not tokens:
        return None
    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def eat(tok=None):
        if pos[0] >= len(tokens):
            return None
        if tok is not None and tokens[pos[0]] != tok:
            return None
        pos[0] += 1
        return tokens[pos[0] - 1]

    def parse_or():
        val = parse_and()
        while peek() == 'or':
            eat()
            rhs = parse_and()
            if val is None or rhs is None:
                val = None
            else:
                val = bool(val or rhs)
        return val

    def parse_and():
        val = parse_not()
        while peek() == 'and':
            eat()
            rhs = parse_not()
            if val is None or rhs is None:
                val = None
            else:
                val = bool(val and rhs)
        return val

    def parse_not():
        if peek() == 'not':
            eat()
            inner = parse_not()
            return None if inner is None else (not inner)
        return parse_atom()

    def parse_atom():
        tok = peek()
        if tok == '(':
            eat()
            val = parse_or()
            eat(')')
            return val
        if tok == 'True':
            eat()
            return True
        if tok == 'False':
            eat()
            return False
        if tok and re.match(r'^[A-Za-z_]\w*$', tok):
            eat()
            return tok.lower() in attrs
        return None

    try:
        result = parse_or()
    except Exception as e:                      # noqa: BLE001 - 解析炸了=认不出
        swallow('story.layered_model.evaluate_condition', e)
        return None
    if pos[0] != len(tokens):
        return None                             # 有没吃掉的 token = 认不出
    return result


# ---------------------------------------------------------------------------
#  姿势表（项目自定义的循环表）
# ---------------------------------------------------------------------------

_RE_DICT_HEAD = re.compile(r'^(\s*)([A-Z_][\w]*)\s*=\s*\{\s*$')
#: 单行写法：`CYCLE_LABEL = {'': '普通', 'tall': '高个'}`
_RE_DICT_ONE_LINE = re.compile(
    r"^(\s*)([A-Z_][\w]*)\s*=\s*\{(.*)\}\s*$")
_RE_DICT_ITEM = re.compile(r"""^\s*['"]?([\w]*)['"]?\s*:\s*\[(.*?)\]\s*,?\s*$""")
_RE_LABEL_ITEM = re.compile(r"""^\s*['"]?([\w]*)['"]?\s*:\s*
                                ['"](.+?)['"]\s*,?\s*$""", re.X)


def parse_pose_tables(project_root, tags) -> tuple:
    """
    自动认出"姿势循环表"（**按形状，不按变量名**）：

        {tag: ['', 'a', 'b'], …}     且键都是 layeredimage 的 tag、
                                     列表里有一个空串（= 普通）

    顺带收人话标签 `{'': '普通', 'a': '泳装'}`（同文件里另一个字典）。
    返回 `(poses, labels, warnings)`。
    """
    root = Path(project_root)
    game = root / 'game' if (root / 'game').is_dir() else root
    tags = {str(t) for t in (tags or ())}
    poses, labels, warnings = {}, {}, []
    label_pool = {}
    if not tags:
        return poses, labels, warnings
    for f in sorted(game.rglob('*.rpy')):
        try:
            lines = f.read_text(encoding='utf-8', errors='ignore') \
                .splitlines()
        except OSError as e:                    # noqa: BLE001 - 读不了就跳过
            swallow('story.layered_model.parse_helper_functions', e)
            continue
        i = 0
        while i < len(lines):
            one = _RE_DICT_ONE_LINE.match(lines[i])
            if one and '{' not in one.group(3):
                body = one.group(3)
                items, plain_labels = {}, {}
                pat = r"'([^']*)'\s*:\s*(\[[^\]]*\]|'[^']*')"
                for key, val in re.findall(pat, body):
                    if val.startswith('['):
                        items[key] = [q.strip().strip("'")
                                      for q in val[1:-1].split(',') if q.strip()]
                    else:
                        plain_labels[key] = val.strip("'")
                hit = {k: v for k, v in items.items()
                       if k in tags and '' in v}
                if hit:
                    poses.update({k: list(v) for k, v in hit.items()})
                    warnings.append(
                        f'{f.name}: 认出姿势循环表 `{one.group(2)}`'
                        f'（单行写法，{len(hit)} 个角色）')
                elif plain_labels:
                    label_pool.update(plain_labels)
                i += 1
                continue
            head = _RE_DICT_HEAD.match(lines[i])
            if not head:
                i += 1
                continue
            indent, name = len(head.group(1)), head.group(2)
            items, plain_labels = {}, {}
            j = i + 1
            while j < len(lines):
                ln = lines[j]
                if ln.strip() and _indent(ln) <= indent:
                    break
                m = _RE_DICT_ITEM.match(ln)
                if m:
                    vals = [s.strip().strip('\'"')
                            for s in m.group(2).split(',') if s.strip()]
                    items[m.group(1)] = vals
                else:
                    lm = _RE_LABEL_ITEM.match(ln)
                    if lm:
                        plain_labels[lm.group(1)] = lm.group(2)
                j += 1
            # 形状判据：键命中 layeredimage tag、且**列表里有空串**
            hit = {k: v for k, v in items.items()
                   if k in tags and '' in v}
            if hit:
                poses.update({k: list(v) for k, v in hit.items()})
                warnings.append(
                    f'{f.name}: 认出姿势循环表 `{name}`'
                    f'（{len(hit)} 个角色）')
            elif plain_labels:
                # 人话标签表通常在**另一个** dict 里（`CC_OUTFIT_LABEL`）：
                # 收集"键是姿势值、值是字符串"的字典，稍后并入。
                label_pool.update(plain_labels)
            i = max(j, i + 1)
    if label_pool and poses:
        known = {p for vals in poses.values() for p in vals}
        keep = {k: v for k, v in label_pool.items() if k in known}
        if keep:
            out_labels = dict(labels)
            out_labels.setdefault('__all__', {}).update(keep)
            labels.clear()
            labels.update(out_labels)
    return poses, labels, warnings


# ---------------------------------------------------------------------------
#  对外：建模型 / 解析一帧的层
# ---------------------------------------------------------------------------


def load_layer_model(project_root) -> LayerModel:
    """扫一遍项目，建出合成立绘模型（纯只读）。"""
    model = LayerModel()
    try:
        model.images = parse_layeredimages(project_root)
        model.helpers = parse_helper_functions(project_root)
        poses, labels, warns = parse_pose_tables(project_root,
                                                 model.images.keys())
        model.poses = poses
        model.pose_labels = labels
        model.warnings = warns
    except Exception as e:                      # noqa: BLE001 - 建不出来就退回老路
        swallow('story.layered_model.load_layer_model', e)
    return model


def resolve_layers(model: LayerModel, tag: str, attrs, project_root=None) -> LayerPlan:
    """
    `tag + 属性集合` → 该怎么叠（`LayerPlan`）。

    **按组推进**（layeredimage 的语义）：每个组的取值是该组里"当前属性集合
    命中的那个"，没命中就用该组的 `default`；组条件不成立的组直接跳过。
    组顺序 = 叠放顺序。
    """
    tag = str(tag or '').strip()
    attrs = [str(a).strip() for a in (attrs or ()) if str(a).strip()]
    lset = {a.lower() for a in attrs}
    plan = LayerPlan(tag=tag, attrs_used=list(attrs))
    plan.pose_options = model.pose_options(tag)
    plan.pose = next((a for a in attrs if a.lower() in
                      {p.lower() for p in plan.pose_options if p}), '')
    if model.pose_labels:
        for _name, table in (model.pose_labels or {}).items():
            for key, label in (table or {}).items():
                plan.pose_labels[key] = label
    img = model.images.get(tag)
    if img is None:
        plan.unresolved.append(f'没有 `layeredimage {tag}:` 声明')
        return plan
    # 永远叠加的层
    for expr in img.always:
        plan.layers.extend(
            _abs_layers(expand_displayable(expr, model.helpers,
                                           img.image_format), project_root))
    for group in img.groups:
        cond = evaluate_condition(group.condition, lset)
        if cond is None:
            plan.unresolved.append(
                f'组 `{group.name}` 的条件认不出：{group.condition}')
            continue
        if not cond:
            continue
        chosen = None
        for name, attr in group.attrs.items():
            if name.lower() in lset:
                chosen = attr
                break
        if chosen is None:
            chosen = next((a for a in group.attrs.values() if a.default), None)
        if chosen is None:
            continue
        for expr in (chosen.exprs or [chosen.name]):
            plan.layers.extend(
                _abs_layers(expand_displayable(expr, model.helpers,
                                               img.image_format,
                                               {'image': chosen.name}),
                            project_root))
    plan.ok = bool(plan.layers) and not all(
        ly.get('note') and not ly.get('path') for ly in plan.layers)
    return plan


def _abs_layers(layers, project_root) -> list:
    """把相对路径解到真实文件（找不到就把 `note` 写清楚）。"""
    out = []
    for ly in layers or ():
        ly = dict(ly)
        for key in ('path', 'mask'):
            rel = ly.get(key)
            if not rel:
                continue
            real = _resolve_path(rel, project_root)
            if real is None:
                ly['note'] = (ly.get('note') or '') + f'（找不到 {rel}）'
                if key == 'path':
                    ly['path'] = None
            else:
                ly[key] = real
        out.append(ly)
    return out


def _resolve_path(rel, project_root):
    """资源路径的基准五花八门，逐个试（与 `resolve_thumb_source` 同思路）。"""
    text = str(rel or '').strip().replace('\\', '/')
    if not text:
        return None
    p = Path(text)
    if p.is_absolute():
        return p if p.is_file() else None
    bases = []
    if project_root:
        root = Path(project_root)
        bases = [root, root / 'game']
    bases.append(Path.cwd())
    for base in bases:
        cand = base / text
        try:
            if cand.is_file():
                return cand
        except OSError as e:                    # noqa: BLE001 - 摸不动的路径
            swallow('story.layered_model._resolve_path', e)
            continue
    return None


def pose_switch_problem(model: LayerModel, tag: str, prev_attrs,
                        next_attrs) -> object:
    """
    "从 B 姿势改到 A（普通/空字符串）"这个**表达不出来**的问题。

    用户 2026-10-05：

    > 从 B 改到 A 的时候是要先隐藏 B 才行的，不然改动作/服装无效……
    > 例子：`"ye": ["", "base_handdown", "base_swimsuit"]`，A 姿势 = 空字符串，
    > 标签叫"普通"。既然 A 没有名字，就没有任何属性名能"选中"它；
    > 而 show 只会叠加/替换属性，永远不会清空没提到的属性。

    返回 `None`（没问题）或：

        {'kind': 'empty_pose_needs_hide', 'tag':…, 'from':'base_handdown',
         'to':'', 'from_label':'…', 'to_label':'普通', 'advice':'…'}

    ⚠ 只**报告**，不改用户脚本（与工具的 P0 一致；自动补 `hide` 见设计文档 P4）。
    """
    tag = str(tag or '').strip()
    options = [p for p in (model.pose_options(tag) or []) if p]
    if not options:
        return None
    low = {o.lower(): o for o in options}
    prev_pose = next((low[str(a).lower()] for a in (prev_attrs or [])
                      if str(a).lower() in low), '')
    next_pose = next((low[str(a).lower()] for a in (next_attrs or [])
                      if str(a).lower() in low), '')
    if not prev_pose or next_pose:
        return None
    labels = {}
    for _name, table in (model.pose_labels or {}).items():
        labels.update(table or {})
    to_label = labels.get('', '普通')
    from_label = labels.get(prev_pose, prev_pose)
    return {
        'kind': 'empty_pose_needs_hide',
        'tag': tag,
        'from': prev_pose,
        'to': '',
        'from_label': from_label,
        'to_label': to_label,
        'advice': (
            f'`show {tag} …` **改不回**「{to_label}」：show 只叠加/替换属性，'
            f'永远不会清空没提到的属性，而「{to_label}」没有任何属性名可选中'
            f'（它是空姿势）。要写成：`hide {tag}` 然后 `show {tag} …`。'),
    }


def frame_pose_warnings(frames, model: LayerModel) -> list:
    """
    逐帧检查"姿势改回普通但没 hide"（给预览/校验用）。

    返回 `[(帧下标, problem), …]`；`show` 序列里**没出现 hide** 才算问题
    —— 用户自己写了 `hide ye` 的拍不会再报（那是正确写法）。
    """
    out = []
    if model is None or not getattr(model, 'images', None):
        return out
    state = {}          # tag -> 上一拍结束时的属性列表
    for idx, frame in enumerate(frames or ()):
        hides = set()
        for item in (getattr(frame, 'hides', None) or []):
            hides.add(str(item).split()[0] if str(item).strip() else '')
        raw_hide = str(getattr(frame, 'hide', '') or '')
        for part in raw_hide.replace(',', ' ').split():
            hides.add(part)
        for sp in (getattr(frame, 'sprites', None) or []):
            spec = str(getattr(sp, 'image', '') or '')
            parts = spec.split()
            if not parts:
                continue
            tag, attrs = parts[0], parts[1:]
            prev = state.get(tag, [])
            if tag in hides:
                prev = []
            problem = pose_switch_problem(model, tag, prev, attrs)
            if problem is not None:
                out.append((idx, problem))
            # 按组推进：姿势属性替换，其它保留（与 layeredimage 语义一致）
            options = {p.lower() for p in model.pose_options(tag) if p}
            kept = [a for a in prev if a.lower() not in options]
            state[tag] = kept + [a for a in attrs if a.lower() in options] \
                + [a for a in attrs if a.lower() not in options]
    return out


__all__ = ['LayerAttr', 'LayerGroup', 'LayeredImage', 'LayerPlan',
           'LayerModel', 'parse_layeredimages', 'parse_helper_functions',
           'parse_expr', 'expand_displayable', 'evaluate_condition',
           'parse_pose_tables', 'load_layer_model', 'resolve_layers',
           'pose_switch_problem', 'frame_pose_warnings']
