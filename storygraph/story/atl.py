"""
storygraph.story.atl
====================
**把 transform 名算成舞台上的真实位置**（纯函数，不碰任何 UI）。

用户 2026-10-05：

> 演出的预览界面可能仍旧有点问题……我觉得这个 transform 未对应到舞台真实位置

为什么原来的预览"位置不对"
--------------------------
`app.stage.transform_x()` 只做了两件事：在**资源表那一行**里找 `xpos/xalign`
字面量，找不到就按名字首字母猜（`i→0.35`、`f→0.65`）。而真实项目的 transform
长这样（`sr_general/game/transform.rpy`）：

    transform f21:
        focus(320)                      # ← 一行调用，没有 xpos
    transform f22:
        focus(960)
    transform focus(x=640, z=0.30):     # ← 真正的定义在别处
        xcenter x xoffset 400 yoffset 1200 yanchor 1.0 ypos 1.03 zoom z*1.00
        on show:
            ...

于是 `f21` / `f22` 都落到"首字母 f → 0.65"，两个人**叠在同一个位置**；
`zoom 0.30` 也完全没体现。这里把这条链补上：

  1. **扫项目源码**里的 `transform <名>:` 块（顶层语句，`on show:` 之类的
     事件块不算 —— 预览要的是"停下来时的样子"）；
  2. **跟调用链**：`f21: focus(320)` → 把 `320` 绑到 `focus(x=640, …)` 的
     参数上，再把参数的表达式**代回**被调用的那几行；
  3. 解释常见的定位属性（`xcenter/xpos/xalign/xoffset/ypos/yanchor/yoffset/
     zoom/alpha` + 缓动前缀 `easein .25 …`），取**最后一个**值（= 最终的静止态）；
  4. 像素值按**游戏分辨率**归一化：`gui.init(1920, 1080)` 从 `gui.rpy` 读
     （读不到就用 1920×1080）。

算出来的是 `Placement`（参考点 + 锚 + 偏移 + 缩放），**画多大**由调用方
按图片原始尺寸算（`app.stage.sprite_box`）—— 因为"角色在画面里多大"取决于
图片本身的像素尺寸（真实项目的立绘是 4320×7680，`zoom 0.3` 之后仍有 2304 高）。

**禁止**在本模块 import 任何 UI 框架。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from storygraph.core.safeguard import swallow

#: 默认游戏分辨率（读不到 `gui.init(...)` 时用）
DEFAULT_SCREEN = (1920, 1080)

#: 位置词 → 归一化 x（与 `app.stage._POSITION_X` 同源；这里只做兜底）
_WORD_X = {
    'left': 0.2, 'center': 0.5, 'centre': 0.5, 'right': 0.8,
    'truecenter': 0.5, 'leftish': 0.2, 'close': 0.5, 'closeup': 0.5,
}
#: 自定义命名的首字母启发（`i11` 偏内 / `f11` 偏外 …）
_LETTER_X = {'l': 0.25, 'r': 0.75, 'c': 0.5, 'i': 0.35, 'f': 0.65}

#: ATL 里认识的属性（其余忽略：`subpixel` / `fit` / `nearest`…）
_X_PROPS = {'xpos', 'xcenter', 'xalign', 'xoffset', 'xanchor'}
_Y_PROPS = {'ypos', 'ycenter', 'yalign', 'yoffset', 'yanchor'}
#: 数值型属性（与位置无关，但 `zoom` 要用）
_NUM_PROPS = {'zoom', 'xzoom', 'yzoom', 'alpha', 'rotate'}
#: 缓动/控制关键字（后面可能跟一个时长表达式）
_WARPERS = {'linear', 'ease', 'easein', 'easeout', 'pause', 'repeat',
            'block', 'parallel', 'choice', 'animation', 'time', 'event',
            'function', 'contains', 'on'}
#: 这些属性按"**后面的覆盖前面的**"取（一行行读下来 = 时间线，取最终态）
_POSITIONAL_PROPS = ('xcenter', 'xpos', 'xalign', 'ypos', 'yalign', 'ycenter',
                     'xoffset', 'yoffset', 'zoom')
#: 出现在顶层 = 动画帧（不是静止属性）→ 位置只能算"猜"（B1；算准是 B2）
_ANIM_WARPERS = {'linear', 'ease', 'easein', 'easeout', 'pause', 'repeat',
                 'choice', 'animation', 'time', 'event', 'function'}
#: 块头里出现这些 = 动画/并行块 —— 里面的属性没进最终态
_ANIM_BLOCKS = {'block', 'animation', 'contains'}

_TRANSFORM_RE = re.compile(r'^(?P<indent>\s*)transform\s+(?P<name>[A-Za-z_]\w*)'
                           r'\s*(?:\((?P<params>[^)]*)\))?\s*:\s*$')
_CALL_RE = re.compile(r'^(?P<name>[A-Za-z_]\w*)\s*\((?P<args>.*)\)\s*$')


# ----------------------------------------------------------------------
#  1) 源码解析
# ----------------------------------------------------------------------

@dataclass
class TransformDef:
    """一个 `transform <名>(参数):` 的定义（只留**顶层**语句）。"""

    name: str
    params: list = field(default_factory=list)
    defaults: dict = field(default_factory=dict)
    body: list = field(default_factory=list)      # 顶层语句（已去掉缩进）
    #: `on show:` / `on hide:` 等**事件块**的属性行（2026-10-18，审计 P1-4）。
    #:
    #: 它们不是"动画中间态"：`on show:` 是"显示完之后停在哪"，
    #: 最后一帧才是可见的静止态（ATL 顺序执行）。
    on_blocks: dict = field(default_factory=dict)
    #: **"这个位置是猜的"的原因**（空 = 有把握）。
    #:
    #: 为什么需要它（用户 2026-10-19，B1）：`parallel:` 里的属性**没有参与计算**
    #: （只在 `_WARPERS` 里当关键字跳过），`block:` / 关键帧则是动画序列 ——
    #: 只写在里面的位置会退回首字母启发。问题不是"算不出"，而是
    #: **"算不出还不说"**：预览把猜出来的 x 画得跟真位置一样。
    #: 这里只记原因；算准是 B2 的事。
    uncertain: str = ''

    def bind(self, args: list, kwargs: dict) -> dict:
        """调用实参 → `{参数名: 表达式字符串}`（缺的用定义里的默认值）。"""
        values = {}
        for i, arg in enumerate(args or []):
            if i < len(self.params):
                values[self.params[i]] = str(arg).strip()
        for key, val in (kwargs or {}).items():
            values[str(key).strip()] = str(val).strip()
        for name, default in self.defaults.items():
            values.setdefault(name, default)
        for name in self.params:
            values.setdefault(name, '')
        return values


def _strip_comment(line: str) -> str:
    """去掉行尾注释（`#` 在引号里不算注释）。"""
    out, quote = [], ''
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ''
            continue
        if ch in '"\'':
            quote = ch
            out.append(ch)
            continue
        if ch == '#':
            break
        out.append(ch)
    return ''.join(out).rstrip()


def _split_params(text: str) -> tuple:
    """`x=640, z=0.30` → (['x', 'z'], {'x': '640', 'z': '0.30'})。"""
    params, defaults = [], {}
    for chunk in (text or '').split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        if '=' in chunk:
            name, _, value = chunk.partition('=')
            name = name.strip()
            params.append(name)
            defaults[name] = value.strip()
        else:
            params.append(chunk)
    return params, defaults


def parse_transforms(text: str) -> dict:
    """
    源码文本 → `{transform 名: TransformDef}`。

    只认 Ren'Py 的 `transform <名>[(参数)]:` 形式；**只收顶层语句**
    （缩进 == 定义行 + 4）——`block:` 里的动画帧不是"停下来时的样子"，
    收进来会把位置算成动画的中间态。

    ⚠ 2026-10-18（审计 P1-4"on show 整块忽略"）：**`on show:` 例外**。
    它不是"动画中间态"，而是"这一下显示完之后停在哪" —— `on show:` 里
    最后一帧的属性就是**可见的静止态**。从前整块跳过，于是

        transform slide_in:
            on show:
                xoffset -120
                linear .3 xoffset 0

    算出来的位置是"什么都没设"（退回首字母猜）。现在把它收进
    `TransformDef.on_blocks['show']`，由 `_from_def` 优先采用
    （**最后一帧赢**，因为 ATL 是顺序执行的）。
    """
    out: dict = {}
    lines = str(text or '').splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        m = _TRANSFORM_RE.match(_strip_comment(raw))
        if not m:
            i += 1
            continue
        name = m.group('name')
        indent = len(m.group('indent').expandtabs(4))
        params, defaults = _split_params(m.group('params') or '')
        body = []
        on_blocks: dict = {}
        flags: list = []                    # "位置是猜的"的原因（B1）
        i += 1
        while i < len(lines):
            line = lines[i]
            stripped = _strip_comment(line)
            if not stripped.strip():
                i += 1
                continue
            here = len(line[:len(line) - len(line.lstrip())].expandtabs(4))
            if here <= indent:
                break                       # 下一个顶层定义 / 语句
            if here == indent + 4:
                head = stripped.strip()
                if not head.rstrip().endswith(':'):
                    if _first_token(head) in _ANIM_WARPERS:
                        flags.append('关键帧')
                    body.append(head)
                else:
                    # 块头（`on show:` / `on hide:` / `parallel:` / `block:`）
                    # 记下名字；`on <事件>` 的子块**要收**（见 docstring）。
                    key = head.rstrip().rstrip(':').strip()
                    j = i + 1
                    sub = []
                    while j < len(lines):
                        sub_line = lines[j]
                        sub_stripped = _strip_comment(sub_line)
                        if not sub_stripped.strip():
                            j += 1
                            continue
                        sub_here = len(sub_line[:len(sub_line)
                                               - len(sub_line.lstrip())]
                                       .expandtabs(4))
                        if sub_here <= here:
                            break
                        # 只取**子块的第一层**（再深的是它自己的嵌套块）
                        if sub_here == here + 4:
                            sub.append(sub_stripped.strip())
                        j += 1
                    if key.startswith('on '):
                        on_blocks.setdefault(key[3:].strip(), []).extend(sub)
                        # `on show: repeat` 永远不停 → 没有"静止态"可言
                        if any(_first_token(x) in ('repeat', 'pause', 'event',
                                                   'time', 'choice', 'function')
                               for x in sub):
                            flags.append('关键帧')
                    elif key == 'parallel' or key.startswith('parallel'):
                        flags.append('parallel')
                    elif key in _ANIM_BLOCKS:
                        flags.append('关键帧')
            i += 1
        out[name] = TransformDef(name=name, params=params,
                                 defaults=defaults, body=body,
                                 on_blocks=on_blocks,
                                 uncertain='/'.join(dict.fromkeys(flags)))
    return out


def scan_transforms(project_root, limit_files: int = 400) -> dict:
    """
    扫项目里的 `*.rpy`，收集全部 transform 定义（**有界**：最多看这么多文件）。

    只读文本，不 import 项目里的任何东西 —— 项目源码是**数据**，不是代码。
    """
    root = Path(project_root) if project_root else None
    if root is None or not root.is_dir():
        return {}
    out: dict = {}
    files = []
    for base in (root / 'game', root):
        if not base.is_dir():
            continue
        files.extend(sorted(base.rglob('*.rpy')))
        if files:
            break
    for path in files[:max(1, int(limit_files))]:
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for name, defn in parse_transforms(text).items():
            out.setdefault(name, defn)      # 先定义的赢（与 Ren'Py 同名覆盖相反，
            #                                 但同名 transform 本身就该避免）
    return out


def project_screen(project_root) -> tuple:
    """项目分辨率：读 `gui.init(w, h)` / `config.screen_width`，读不到用默认值。"""
    root = Path(project_root) if project_root else None
    if root is None or not root.is_dir():
        return DEFAULT_SCREEN
    for name in ('gui.rpy', 'options.rpy'):
        path = root / 'game' / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        m = re.search(r'gui\.init\s*\(\s*(\d+)\s*,\s*(\d+)', text)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.search(r'config\.screen_width\s*=\s*(\d+)', text)
        if m:
            w = int(m.group(1))
            h = re.search(r'config\.screen_height\s*=\s*(\d+)', text)
            return w, (int(h.group(1)) if h else DEFAULT_SCREEN[1])
    return DEFAULT_SCREEN


# ----------------------------------------------------------------------
#  2) 表达式求值（白名单，绝不 eval 项目里的任意代码）
# ----------------------------------------------------------------------

_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
                  ast.Name, ast.Add, ast.Sub, ast.Mult, ast.Div,
                  ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd,
                  ast.Load, ast.Tuple)


def eval_number(expr, env: dict) -> Optional[float]:
    """
    ATL 里的数值表达式 → float（`z*1.00` / `400` / `-300` / `1.03`；失败 None）。

    用 `ast` 白名单自己走一遍，**不 `eval`** —— 项目源码里的表达式不可信。
    """
    text = str(expr or '').strip()
    if not text:
        return None
    try:
        tree = ast.parse(text, mode='eval')
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return None
    try:
        value = _eval_node(tree.body, env or {})
    except Exception as e:                        # noqa: BLE001 - 名字没绑上等
        swallow('story.atl.eval_number', e)
        return None
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _eval_node(node, env: dict):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        low = node.id.lower()
        if low in ('true', 'false'):
            return 1.0 if low == 'true' else 0.0
        raise KeyError(node.id)
    if isinstance(node, ast.UnaryOp):
        val = _eval_node(node.operand, env)
        return -val if isinstance(node.op, ast.USub) else +val
    if isinstance(node, ast.BinOp):
        left, right = _eval_node(node.left, env), _eval_node(node.right, env)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.FloorDiv):
            return left // right
        if isinstance(op, ast.Mod):
            return left % right
        if isinstance(op, ast.Pow):
            return left ** right
    raise ValueError('unsupported')


def _first_token(line: str) -> str:
    """一行 ATL 的第一个词（`linear .3 xpos 1` → `linear`）。"""
    parts = str(line or '').split()
    return parts[0] if parts else ''


def _props_of(line: str) -> list:
    """
    一行 ATL → `[(属性, 值表达式), …]`。

    要能吃掉缓动前缀与"一行多个属性"：
        `easein .25 xcenter x zoom z*1 alpha 1.00`
        → [('xcenter','x'), ('zoom','z*1'), ('alpha','1.00')]
    """
    tokens = str(line or '').split()
    out = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in _WARPERS:
            i += 1
            if i < len(tokens) and eval_number(tokens[i], {}) is not None:
                i += 1
            if i < len(tokens) and tokens[i] == ':':
                i += 1
            continue
        if token in _X_PROPS or token in _Y_PROPS or token in _NUM_PROPS:
            value = tokens[i + 1] if i + 1 < len(tokens) else ''
            out.append((token, value))
            i += 2
            continue
        i += 1
    return out


# ----------------------------------------------------------------------
#  3) transform → 舞台位置
# ----------------------------------------------------------------------

@dataclass
class Placement:
    """
    一个 transform 算出来的舞台位置（**虚拟屏幕像素** + 归一化兜底）。

    `x`/`y_kind` 等字段的含义与 Ren'Py 的 ATL 一一对应：
      · `x_kind='center'`  → `x_px` 是**中心**的 x（`xcenter`）
      · `x_kind='left'`    → `x_px` 是**左边缘**的 x（`xpos` 给像素时）
      · `x_kind='align'`   → `x_px` 是 **0..1 的分数**（`xalign` / `xpos ≤1`）
    """

    x: float = 0.5                  # 归一化中心（没有像素信息时用它）
    zoom: float = 1.0
    x_px: Optional[float] = None
    x_kind: str = 'center'
    xanchor: float = 0.5
    xoffset: float = 0.0
    y_px: Optional[float] = None
    y_kind: str = 'bottom'
    yanchor: float = 1.0
    yoffset: float = 0.0
    source: str = 'center'          # atl:<名> / word / letter / center
    #: 位置**不可靠**时的一句人话（空 = 有把握）。见 `TransformDef.uncertain`。
    #: 预览横条原样显示它 —— "算不出还不说"比算错更糟（用户 2026-10-19，B1）。
    uncertain: str = ''

    @property
    def from_atl(self) -> bool:
        """这个位置是从项目 transform 里**真算出来**的吗？"""
        return str(self.source).startswith('atl')


def uncertain_note(reason: str) -> str:
    """
    `parallel/关键帧` → 给用户看的一句话（预览横条用）。

    空原因返回空串（"有把握"时**不许**多嘴 —— 到处标警告等于没警告）。
    """
    reason = str(reason or '').strip()
    if not reason:
        return ''
    # 中文原因不留空格（"含关键帧"），拉丁词留一个（"含 parallel"）
    sep = ' ' if reason[:1].isascii() else ''
    return f'这个 transform 含{sep}{reason}，位置是猜的'


def _guess_x(name: str) -> tuple:
    """位置词 / 首字母启发 → (归一化 x, source)。都不认识就居中。"""
    low = str(name or '').strip().lower()
    if low in _WORD_X:
        return _WORD_X[low], 'word'
    if low and low[0] in _LETTER_X:
        return _LETTER_X[low[0]], 'letter'
    return 0.5, 'center'


def placement_of(name: str, index: Optional[dict] = None, registry=None,
                 screen=DEFAULT_SCREEN, _depth: int = 0) -> Placement:
    """
    transform 名 → `Placement`。

    优先级（可靠 → 启发）：
      1. 项目源码里的 transform（含**调用链**与参数）→ 真像素位置 + zoom；
      2. 资源表那一行的 ATL 里有 `xpos/xalign` 字面量（老路径，项目源码不在时用）；
      3. 已知位置词（`left` / `right` / `closeup`…）；
      4. 首字母启发（`i11` 偏内 / `f11` 偏外）；
      5. 兜底居中 —— **不猜**。
    """
    name = str(name or '').strip()
    if not name:
        return Placement(source='center')
    width, height = (tuple(screen) + (1920, 1080))[:2]
    width = max(1, int(width))
    height = max(1, int(height))

    # 1) 项目 transform（含调用链）
    defn = (index or {}).get(name)
    if defn is not None and _depth < 6:
        got = _from_def(defn, {}, index, (width, height), _depth)
        if got is not None:
            # 算出来了，但定义里有 parallel/关键帧 → 结果只是"能算的那部分"
            got.uncertain = got.uncertain or uncertain_note(
                getattr(defn, 'uncertain', ''))
            return got

    # 2) 资源表里的 ATL 字面量（老路径）
    if registry is not None:
        row = (getattr(registry, 'transforms', {}) or {}).get(name) or {}
        atl = str(row.get('ATL') or '')
        if atl:
            props = {}
            for line in atl.splitlines() or [atl]:
                for prop, value in _props_of(line):
                    props.setdefault(prop, value)
            if props:
                got = _from_props(props, {}, (width, height), f'atl:{name}')
                if got is not None and got.x_px is not None:
                    return got

    # 3)/4)/5) 词与启发
    x, source = _guess_x(name)
    place = Placement(x=x, source=source)
    if defn is not None and getattr(defn, 'uncertain', ''):
        # 定义里有 parallel/关键帧，而且**一个能算的属性都没落到最终态** ——
        # 这时位置是按名字猜的，必须说出来（这正是用户报的那个场景）。
        place.uncertain = (uncertain_note(defn.uncertain)
                           .replace('位置是猜的', '位置是按名字猜的'))
    return place


def _fmt(value) -> str:
    """
    把参数实参写成**源码该有的样子**再代回 ATL。

    ⚠ 关键：`xcenter 320` 是"320 像素"，而 `xcenter 320.0` 是"屏幕的 320 倍"
    （Ren'Py 按写法分辨整数/小数）。链路绑定拿到的是 float，直接 `str()` 会
    变成 `320.0`，把像素值误判成比例 —— 这里把整数值还原成 `320`。
    """
    text = str(value).strip()
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    if number == int(number):
        return str(int(number))
    return repr(number)


def _from_def(defn: TransformDef, args_env: dict, index, screen,
              depth: int) -> Optional[Placement]:
    """
    一个定义 + 实参 → `Placement`。

    两种形态：
      · **调用链**（body 只有一行 `focus(320)`）→ 把实参绑到被调用者的形参上，
        递归进去（实参本身可能又是外层参数，先替换再求值）；
      · **属性赋值**（`xcenter x xoffset 400 …`）→ 把形参的实参/默认值代回，
        再逐行收集属性；**定位/缩放类后面的覆盖前面的**（= 最终静止态）。
    """
    body = list(defn.body or [])
    on_show = list((defn.on_blocks or {}).get('show') or ())
    if not body and not on_show:
        return None

    if len(body) == 1:
        m = _CALL_RE.match(body[0])
        callee = (index or {}).get(m.group('name')) if m else None
        if callee is not None and depth < 6:
            args, kwargs = _split_args(m.group('args'))
            bound = {}
            for key, expr in callee.bind(args, kwargs).items():
                text = str(expr)
                for outer, outer_val in (args_env or {}).items():
                    if outer_val is None:
                        continue
                    text = re.sub(r'\b%s\b' % re.escape(outer), str(outer_val),
                                  text)
                number = eval_number(text, {})
                if number is not None:
                    bound[key] = number
            got = _from_def(callee, bound, index, screen, depth + 1)
            if got is not None:
                got.uncertain = got.uncertain or uncertain_note(
                    getattr(callee, 'uncertain', ''))
                return got

    subs = dict(defn.defaults or {})
    subs.update({k: v for k, v in (args_env or {}).items() if v is not None})

    def collect(lines: list, into: dict) -> None:
        for line in lines:
            text = str(line)
            for name, expr in subs.items():
                text = re.sub(r'\b%s\b' % re.escape(name), _fmt(expr), text)
            for prop, value in _props_of(text):
                if prop in _POSITIONAL_PROPS:
                    into[prop] = value       # 后面的覆盖前面的（最终静止态）
                else:
                    into.setdefault(prop, value)

    # `on show:` 的**最后一帧**是玩家看到的静止态（2026-10-18，审计 P1-4：
    # 从前整块忽略 → 位置退回首字母猜）。它设过的属性**赢**；
    # 顶层 body 只补它没设的（`xalign 0.5` + on show 只动 xoffset 时两都要）。
    props: dict = {}
    collect(on_show, props)
    rest: dict = {}
    collect(body, rest)
    for key, value in rest.items():
        props.setdefault(key, value)
    if not props:
        return None
    return _from_props(props, {}, screen, f'atl:{defn.name}')


def _split_args(text: str) -> tuple:
    """`320` / `x=320, z=0.3` → (['320'], {'x': '320', 'z': '0.3'})。"""
    args, kwargs = [], {}
    for chunk in (text or '').split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        if '=' in chunk:
            key, _, value = chunk.partition('=')
            kwargs[key.strip()] = value.strip()
        else:
            args.append(chunk)
    return args, kwargs


def _from_props(props: dict, env: dict, screen, source: str) -> Optional[Placement]:
    """
    属性表 → `Placement`。

    ⚠ Ren'Py 的位置属性是**按写法分辨**的（不是按大小）：
    **整数 = 像素，小数 = 屏幕比例** —— `ypos 1.03` 是"屏幕高的 103%"
    （真实项目的 `focus` 就用了它），而 `ypos 1200` 才是 1200 像素。
    不按这个规则解，位置会整片跑偏。
    """
    width, height = screen
    got = Placement(source=source)
    used_px = False

    def frac(raw) -> bool:
        return '.' in str(raw or '')

    def value(raw):
        return eval_number(raw, env)

    xalign = value(props.get('xalign'))
    xcenter_raw, xpos_raw = props.get('xcenter'), props.get('xpos')
    xcenter, xpos = value(xcenter_raw), value(xpos_raw)
    xoffset = value(props.get('xoffset')) or 0.0
    xanchor = value(props.get('xanchor'))

    if xalign is not None:
        got.x_kind, got.x_px = 'align', xalign
        got.xanchor = xanchor if xanchor is not None else xalign
        got.x = max(0.0, min(1.0, xalign))
        used_px = True
    elif xcenter is not None:
        got.xanchor = xanchor if xanchor is not None else 0.5
        if frac(xcenter_raw):                 # 小数 = 屏幕比例（锚点 0.5）
            got.x_kind, got.x_px = 'align', xcenter
            base = xcenter * width + xoffset
        else:                                 # 整数 = 像素
            got.x_kind, got.x_px = 'center', xcenter
            base = xcenter + xoffset
        got.x = max(0.0, min(1.0, base / width))
        used_px = True
    elif xpos is not None:
        if frac(xpos_raw):
            got.x_kind, got.x_px = 'align', xpos
            got.xanchor = xanchor if xanchor is not None else 0.0
            got.x = max(0.0, min(1.0, xpos))
        else:
            got.x_kind, got.x_px = 'left', xpos
            got.xanchor = xanchor if xanchor is not None else 0.0
            got.x = max(0.0, min(1.0, (xpos + xoffset) / width))
        used_px = True
    got.xoffset = xoffset

    yalign = value(props.get('yalign'))
    ypos_raw, ycenter_raw = props.get('ypos'), props.get('ycenter')
    ypos, ycenter = value(ypos_raw), value(ycenter_raw)
    yoffset = value(props.get('yoffset')) or 0.0
    yanchor = value(props.get('yanchor'))

    if yalign is not None:
        got.y_kind, got.y_px = 'align', yalign
        got.yanchor = yanchor if yanchor is not None else yalign
        used_px = True
    elif ypos is not None:
        got.y_kind = 'align' if frac(ypos_raw) else 'bottom'
        got.y_px = ypos
        # 没写 yanchor 时按 Ren'Py 的默认（0 = 图片顶端落在 ypos 上）。
        # ⚠ 不能沿用"贴底"那个默认：那会把 `ypos 0.5` 画到屏幕外。
        got.yanchor = yanchor if yanchor is not None else 0.0
        used_px = True
    elif ycenter is not None:
        got.y_kind = 'align' if frac(ycenter_raw) else 'bottom'
        got.y_px = ycenter * height if frac(ycenter_raw) else ycenter
        got.yanchor = yanchor if yanchor is not None else 0.5
        used_px = True
    got.yoffset = yoffset

    zoom = value(props.get('zoom'))
    if zoom is None:
        zx, zy = value(props.get('xzoom')), value(props.get('yzoom'))
        zoom = zx if zx is not None else zy
    got.zoom = float(zoom) if zoom and zoom > 0 else 1.0
    if not used_px and got.zoom == 1.0:
        return None                          # 什么定位信息都没有 → 交回启发式
    return got


__all__ = ['DEFAULT_SCREEN', 'Placement', 'TransformDef', 'eval_number',
           'parse_transforms', 'placement_of', 'project_screen',
           'scan_transforms']
