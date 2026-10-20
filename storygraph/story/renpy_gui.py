"""
storygraph.story.renpy_gui
==========================
**把项目的 gui.rpy / screens.rpy / 角色定义读成一份可绘制的排版参数**。

用户 2026-10-20：

> 舞台的预览效果必须更真实！最好跟真正编译的一模一样……目前先做 Renpy 的吧！

为什么不能继续用"固定的 960×540 画布 + 画布外的 QLabel"：真实游戏里
对话框、名条、字体、字号、颜色、描边、内边距全都来自项目自己的 `gui.*`
变量；同一个 `.rpy` 在 1280×720 和 1920×1080 下、在换了字体的项目里，
长得完全不一样。预览要"像编译出来的"，就必须读**这个项目**的这些值，
而不是编辑器自己的主题。

本模块只做**静态读取**（项目源码是数据，不 import、不 eval），产出
`GuiProfile` / `CharacterStyle`。画由 `app.stage_render` 用 Pillow 做。

支持的写法（都是 Ren'Py 模板里的真实写法）：

    init python:
        gui.init(1920, 1080)

    define gui.textbox_height = 277
    define gui.name_xpos = 575
    define gui.dialogue_ypos = 39
    define gui.text_font = "fonts/xxx.otf"
    define gui.text_outlines = [(2, "#000000", 0, 0)]

    init python:
        gui.text_size = 45          # 全局覆盖（不在 @gui.variant 里）

    @gui.variant
    def small():                    # 手机/平板变体：**不用于桌面预览**
        gui.text_size = 45

角色：

    define l = DynamicCharacter('l_name', image='lan',
                                who_color='#ffffff', what_prefix='「')

    default l_name = "「蓝」"

刻意**不猜**：
  · 找不到字体 → `font_path=None` + `warnings`，渲染器退回系统字体并明说；
  · `gui.textbox_height` 没写 → 用 Ren'Py 模板默认值 278 并记一条 warning；
  · 自定义 say screen 的复杂布局（bubble/nvl）不在第一版范围，记 warning。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from storygraph.core.safeguard import swallow

#: Ren'Py 8.x 模板默认值（项目没写时用；有 warning）
DEFAULT_SCREEN = (1920, 1080)
DEFAULTS = {
    'textbox_height': 278.0,
    'textbox_yalign': 1.0,
    'name_xpos': 360.0,
    'name_ypos': 0.0,
    'name_xalign': 0.0,
    'namebox_width': 0.0,          # 0 = auto
    'namebox_height': 0.0,         # 0 = auto
    'dialogue_xpos': 402.0,
    'dialogue_ypos': 75.0,
    'dialogue_width': 1116.0,
    'dialogue_text_xalign': 0.0,
    'text_size': 33.0,
    'name_text_size': 45.0,
    'text_color': '#ffffff',
    'name_text_color': '#ffffff',
    'text_outlines': [(2, '#000000', 0, 0)],
    'name_text_outlines': [(2, '#000000', 0, 0)],
    'line_spacing': 0.0,
    'text_font': 'DejaVuSans.ttf',
    'name_text_font': 'DejaVuSans.ttf',
}

_ASSIGN_RE = re.compile(
    r'^\s*(?:define\s+)?gui\.(?P<key>[A-Za-z_]\w*)\s*=\s*(?P<value>.+?)\s*$')
_INIT_RE = re.compile(r'^init(?:\s+[-+]?\d+)?\s+python\s*:')
_VARIANT_RE = re.compile(r'^@gui\.variant\b')
_DEF_RE = re.compile(r'^def\s+\w+\s*\(')
_CHAR_RE = re.compile(
    r'^define\s+(?P<id>[A-Za-z_]\w*)\s*=\s*'
    r'(?P<cls>(?:Dynamic)?Character)\s*\(')
_DEFAULT_STR_RE = re.compile(
    r'^default\s+(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<q>["\'])(?P<value>.*?)(?P=q)')
_BACKGROUND_RE = re.compile(
    r'background\s+(?:Image\s*\(\s*)?["\'](?P<path>[^"\']+)["\']')
_SCREEN_SAY_RE = re.compile(r'^screen\s+say\s*\(')
_STYLE_RE = re.compile(r'^style\s+(?P<name>[A-Za-z_]\w*)\s*(?:is\s+\w+\s*)?:')


@dataclass
class CharacterStyle:
    """一个角色的显示样式（名条颜色 / 前后缀 / 图像 tag）。"""

    id: str = ''
    name: str = ''
    who_color: str = ''
    what_color: str = ''
    who_prefix: str = ''
    who_suffix: str = ''
    what_prefix: str = ''
    what_suffix: str = ''
    image_tag: str = ''
    name_var: str = ''
    kind: str = ''


@dataclass
class GuiProfile:
    """一个项目的 say 界面排版参数（全部是**屏幕像素**）。"""

    project_root: str = ''
    screen: tuple = DEFAULT_SCREEN
    values: dict = field(default_factory=dict)
    textbox_image: Optional[str] = None
    namebox_image: Optional[str] = None
    text_font_path: Optional[str] = None
    name_font_path: Optional[str] = None
    characters: dict = field(default_factory=dict)   # id -> CharacterStyle
    names: dict = field(default_factory=dict)        # 显示名 -> CharacterStyle
    warnings: list = field(default_factory=list)
    #: 字体替换记录（`想要的 → 实际用的`）。预览横条可以把这条直接显示给用户：
    #: "字不是那个字"必须说出来，否则看起来"像"的排版其实是假的。
    fonts_substituted: list = field(default_factory=list)
    screen_say_custom: bool = False

    # ---- 便于渲染器读的强类型属性 ----
    @property
    def textbox_height(self) -> float:
        return float(self.values.get('textbox_height', 278.0))

    @property
    def textbox_yalign(self) -> float:
        return float(self.values.get('textbox_yalign', 1.0))

    @property
    def text_size(self) -> float:
        return float(self.values.get('text_size', 33.0))

    @property
    def name_text_size(self) -> float:
        return float(self.values.get('name_text_size', 45.0))

    @property
    def text_color(self) -> str:
        return str(self.values.get('text_color') or '#ffffff')

    @property
    def name_text_color(self) -> str:
        return str(self.values.get('name_text_color') or '#ffffff')

    @property
    def text_outlines(self) -> list:
        return list(self.values.get('text_outlines') or [])

    @property
    def name_text_outlines(self) -> list:
        return list(self.values.get('name_text_outlines') or [])

    @property
    def line_spacing(self) -> float:
        return float(self.values.get('line_spacing') or 0.0)

    @property
    def namebox_padding(self) -> tuple:
        pad = self.values.get('namebox_borders') or (0, 0, 0, 0)
        try:
            left, top, right, bottom = (list(pad) + [0, 0, 0, 0])[:4]
            return float(left), float(top), float(right), float(bottom)
        except (TypeError, ValueError):
            return 0.0, 0.0, 0.0, 0.0

    def character_for(self, speaker: str, display: str = '') -> CharacterStyle:
        """按变量名 / 显示名找角色样式（找不到返回空样式）。"""
        style = self.characters.get(str(speaker or ''))
        if style is not None:
            return style
        return self.names.get(str(display or '')) or CharacterStyle(id=speaker)


# ----------------------------------------------------------------------
#  值解析
# ----------------------------------------------------------------------

def _literal(text: str):
    """安全地解析一个 Python 字面量；失败返回 `_MISSING`。"""
    text = str(text or '').strip()
    if not text:
        return _MISSING
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return _MISSING


class _Missing:
    pass


_MISSING = _Missing()


def _number(value, fallback):
    if isinstance(value, bool) or value is None:
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def _borders(text: str) -> tuple:
    """`Borders(1, 0, 1, 0)` / `(1, 0, 1, 0)` → 四元组。"""
    nums = re.findall(r'-?\d+(?:\.\d+)?', str(text or ''))
    if len(nums) >= 4:
        return tuple(float(n) for n in nums[:4])
    return (0.0, 0.0, 0.0, 0.0)


def _outlines(text: str) -> list:
    """
    `[(2, "#000000", 0, 0)]` → `[(2, "#000000", 0, 0)]`。

    容忍 `absolute(2)` 与变量引用：只取能确定的数字/颜色；认不出就空表
    （宁可没有描边，也不画错颜色）。
    """
    out = []
    for m in re.finditer(
            r'\(\s*(-?\d+(?:\.\d+)?)\s*,\s*["\'](#[0-9A-Fa-f]{3,8})["\']'
            r'(?:\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*)?\)',
            str(text or '')):
        out.append((float(m.group(1)), m.group(2),
                    float(m.group(3) or 0), float(m.group(4) or 0)))
    return out


def _strip_inline_comment(line: str) -> str:
    """去掉 `#` 注释（引号里的 `#` 是颜色，不能切）。"""
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


def _scan_rpy_files(game: Path):
    """`game/**/*.rpy`（有界；跳过存档/翻译/缓存）。"""
    skip = {'cache', 'saves', 'tl', '.git', 'build', '__pycache__'}
    try:
        files = sorted(game.rglob('*.rpy'))
    except OSError:
        return []
    return [p for p in files if not (skip & set(p.parts))]


def _read(path: Path) -> str:
    try:
        # Ren'Py 项目常见 UTF-8 BOM（Windows 记事本/旧脚本）；带 BOM 时
        # 第一行 `define …` 会匹配不到，角色颜色/名字就整片丢失。
        return path.read_text(encoding='utf-8-sig', errors='replace')
    except OSError:
        return ''


def _parse_defines(text: str) -> dict:
    """
    扫 `gui.*` 赋值。返回 `{key: 值文本}`。

    `define gui.x = ...` 与 `init python:` 里的 `gui.x = ...` 都收；
    `@gui.variant def small():` 里的**不收**（那是手机/平板变体，
    桌面预览用默认那套）。
    """
    out: dict = {}
    lines = str(text or '').splitlines()
    variant_pending = False
    variant_func_indent = None
    for raw in lines:
        line = _strip_inline_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if variant_pending:
            if _DEF_RE.match(stripped):
                variant_func_indent = indent
                variant_pending = False
                continue
            continue
        if variant_func_indent is not None:
            if indent > variant_func_indent:
                continue
            variant_func_indent = None
        if _VARIANT_RE.match(stripped):
            variant_pending = True
            continue
        m = _ASSIGN_RE.match(line)
        if not m:
            continue
        out[m.group('key')] = m.group('value').strip()
    return out


def _resolve_value(key: str, raw: str, values: dict):
    """把 `gui.*` 的原始文本变成 Python 值（数字/字符串/元组/列表）。"""
    text = str(raw or '').strip()
    # 引用另一个 gui 变量
    m = re.fullmatch(r'gui\.([A-Za-z_]\w*)', text)
    if m:
        other = values.get(m.group(1))
        return other if other is not None else _MISSING
    if key in ('namebox_borders',):
        return _borders(text)
    if key.endswith('_outlines'):
        got = _outlines(text)
        return got if got else _MISSING
    lit = _literal(text)
    if lit is not _MISSING:
        return lit
    # `Borders(...)` 已经被上面处理；`None` / `True` / `False`
    low = text.lower()
    if low == 'none':
        return None
    if low in ('true', 'false'):
        return low == 'true'
    # 数字/颜色字符串（有些项目写 `#fff` 不带引号？极少）
    if re.fullmatch(r'-?\d+(?:\.\d+)?', text):
        return float(text)
    return _MISSING


def _sdk_roots(project_root: Path) -> list:
    """
    可能装有 Ren'Py 自带字体的 SDK 根目录（**引擎就是这么找到 DejaVu 的**）。

    项目通常只写 `gui.text_font = "DejaVuSans.ttf"`，字体文件却在
    `<SDK>/renpy/common/` 里；不按这个顺序找，就会掉进 Windows 字体目录，
    抓到一个"能开、但字形完全不同"的字体（微软雅黑），预览的字宽/字重
    和真机差一截。2026-10-20 实测：the_question 的 "Sylvie" 引擎宽 88px，
    用雅黑只有 82px —— 字号一样，字不是那个字。
    """
    out = []
    # 项目就在 SDK 里（`<sdk>/the_question`）时，祖父目录就是**引擎这次用
    # 的那一份** SDK —— 比全局探测到的 SDK 更可信，排前面。
    out.extend(list(Path(project_root).parents)[:3])
    try:
        from storygraph.pipeline.engine_collect import find_sdk
        out.append(Path(find_sdk()))
    except Exception as e:                   # noqa: BLE001 - 没有 SDK 也能用
        swallow('renpy_gui.find_sdk', e)
    return out


def _font_candidates(project_root: Path, rel: str):
    """
    字体候选路径，按"最像引擎"的顺序：
      1. 项目内的相对路径（作者自己带的字体）；
      2. 项目目录里的同名文件；
      3. SDK 的 `renpy/common` / `gui` / `sdk-fonts` —— 引擎真正的兜底；
      4. Windows 系统字体（最后手段，会记 warning）。
    """
    rel = str(rel or '').strip().strip('"\'')
    if not rel:
        return []
    out = []
    for base in (project_root / 'game', project_root):
        p = base / rel
        if p.is_file():
            out.append(p)
    name = Path(rel).name
    game = project_root / 'game'
    try:
        for p in sorted(game.rglob(name))[:5]:
            if p.is_file():
                out.append(p)
    except OSError as e:
        swallow('renpy_gui.rglob_font', e)
    for root in _sdk_roots(project_root):
        for rel_dir in ('renpy/common', 'gui', 'sdk-fonts', 'common'):
            p = root / rel_dir / name
            if p.is_file():
                out.append(p)
    win = Path(r'C:\Windows\Fonts')
    for cand in (name, 'msyh.ttc', 'msyhbd.ttc', 'simhei.ttf',
                 'arial.ttf', 'segoeui.ttf'):
        p = win / cand
        if p.is_file():
            out.append(p)
    return out


def _find_file(project_root: Path, rel: str) -> Optional[str]:
    """项目内的图片/字体相对路径 → 绝对路径（找不到 None）。"""
    rel = str(rel or '').strip().strip('"\'')
    if not rel:
        return None
    for base in (project_root / 'game', project_root):
        p = base / rel
        if p.is_file():
            return str(p)
    return None


def _parse_characters(texts: dict, registry_names: dict | None = None):
    """
    扫 `define <id> = Character(...)` / `DynamicCharacter(...)`。

    `texts` 是 `{路径: 源码}`；返回 `(by_id, by_name, warnings)`。
    """
    by_id: dict = {}
    by_name: dict = {}
    warnings: list = []
    defaults = {}
    for text in texts.values():
        for line in text.splitlines():
            m = _DEFAULT_STR_RE.match(line.strip())
            if m:
                defaults[m.group('name')] = m.group('value')
    for path, text in texts.items():
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = _strip_inline_comment(lines[i])
            m = _CHAR_RE.match(line.strip())
            if not m:
                i += 1
                continue
            # 收集到括号配平（角色定义经常分好几行）
            chunk = line[line.index('('):]
            depth = chunk.count('(') - chunk.count(')')
            j = i + 1
            while depth > 0 and j < len(lines):
                nxt = _strip_inline_comment(lines[j])
                chunk += '\n' + nxt
                depth += nxt.count('(') - nxt.count(')')
                j += 1
            expr = 'Character' + chunk[chunk.index('('):]
            try:
                node = ast.parse(expr, mode='eval').body
            except SyntaxError as e:
                warnings.append(f'{Path(path).name}:{i + 1} 角色定义读不出（{e.msg}）')
                i = j
                continue
            if not isinstance(node, ast.Call):
                i = j
                continue
            kw = {}
            for item in node.keywords:
                if item.arg and isinstance(item.value, ast.Constant):
                    kw[item.arg] = item.value.value
            display = ''
            name_var = ''
            cls_name = m.group('cls')
            if node.args and isinstance(node.args[0], ast.Constant):
                first = str(node.args[0].value or '')
                if cls_name == 'DynamicCharacter':
                    # DynamicCharacter 的第一个参数是**变量名**，不是显示名：
                    #   define l = DynamicCharacter('l_name', …)
                    #   default l_name = "「蓝」"
                    name_var = first
                    display = defaults.get(first, '')
                else:
                    display = first
            if not name_var:
                name_var = str(kw.get('name') or '')
            if not display and name_var:
                display = defaults.get(name_var, '')
            if not display and registry_names:
                display = registry_names.get(m.group('id'), '')
            style = CharacterStyle(
                id=m.group('id'), name=display,
                who_color=str(kw.get('who_color') or kw.get('color') or ''),
                what_color=str(kw.get('what_color') or ''),
                who_prefix=str(kw.get('who_prefix') or ''),
                who_suffix=str(kw.get('who_suffix') or ''),
                what_prefix=str(kw.get('what_prefix') or ''),
                what_suffix=str(kw.get('what_suffix') or ''),
                image_tag=str(kw.get('image') or ''),
                name_var=name_var,
                kind=str(kw.get('kind') or ''),
            )
            by_id[m.group('id')] = style
            if style.name:
                by_name.setdefault(style.name, style)
            i = j
    return by_id, by_name, warnings


def _parse_say_screen(texts: dict) -> tuple:
    """
    是否自定义了 say screen；以及 `style window/namebox` 的背景图。

    只认"标准模板 + 换背景图"这一档；`background DynamicDisplayable(...)`
    这种运行时函数也会尽量取出里面第一个字符串路径。
    """
    custom = False
    background = {}
    for text in texts.values():
        lines = text.splitlines()
        current = ''
        variant_indent = None
        for line in lines:
            stripped = _strip_inline_comment(line).strip()
            indent = len(line) - len(line.lstrip())
            if variant_indent is not None:
                if indent > variant_indent:
                    continue
                variant_indent = None
            if re.match(r'^variant\s+["\'](?:small|touch|phone)["\']\s*:', stripped):
                variant_indent = indent
                continue
            if _SCREEN_SAY_RE.match(stripped):
                custom = True
            if current and re.match(
                    r'variant\s+["\'](?:small|touch|phone)["\']', stripped):
                # 同一 style 里的手机/平板变体：桌面预览不采用，整块跳过。
                current = ''
                continue
            m = _STYLE_RE.match(stripped)
            if m:
                current = m.group('name')
                continue
            if current and stripped and not line.startswith((' ', '\t')):
                current = ''
            if current in ('window', 'namebox'):
                bm = _BACKGROUND_RE.search(stripped)
                if bm:
                    background.setdefault(current, bm.group('path'))
    return custom, background


# ----------------------------------------------------------------------
#  入口
# ----------------------------------------------------------------------

_CACHE: dict = {}


def load_gui_profile(project_root, registry=None, force: bool = False) -> GuiProfile:
    """
    读项目 → `GuiProfile`（带**文件指纹**缓存）。

    缓存键包含 `gui.rpy` / `screens.rpy` / `definitions.rpy` 的 mtime：
    作者改了字号再开预览，必须立刻看到新字号（不能一次会话只读一次盘）。
    """
    root = Path(project_root) if project_root else None
    if root is None or not root.is_dir():
        return GuiProfile(warnings=['没有项目目录，无法读取 gui 排版'])
    game = root / 'game'
    files = ['gui.rpy', 'screens.rpy', 'options.rpy', 'definitions.rpy',
             'script.rpy']
    paths = []
    for name in files:
        p = game / name
        if p.is_file():
            paths.append(p)
    # 角色定义可能不在 definitions.rpy：扫全部 rpy（有界）
    if not any(p.name == 'definitions.rpy' for p in paths):
        paths.extend(_scan_rpy_files(game)[:80])
    try:
        stamp = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size)
                      for p in paths)
    except OSError:
        stamp = tuple(str(p) for p in paths)
    key = (str(root), stamp, id(registry))
    if not force:
        got = _CACHE.get(key)
        if got is not None:
            return got

    texts = {}
    for p in paths:
        texts[str(p)] = _read(p)
    raw = {}
    for text in texts.values():
        for key_, value in _parse_defines(text).items():
            raw[key_] = value
    values = {}
    for key_, text in raw.items():
        got = _resolve_value(key_, text, values)
        if got is not _MISSING:
            values[key_] = got

    profile = GuiProfile(project_root=str(root), values=values)
    # 分辨率
    screen = DEFAULT_SCREEN
    for text in texts.values():
        m = re.search(r'gui\.init\s*\(\s*(\d+)\s*,\s*(\d+)', text)
        if m:
            screen = (int(m.group(1)), int(m.group(2)))
            break
    profile.screen = screen

    # 缺省值 + warning
    for key_, default in DEFAULTS.items():
        if key_ not in values:
            if key_ == 'textbox_height':
                profile.warnings.append(
                    'gui.textbox_height 没写，按 Ren\'Py 默认 278 画')
            values[key_] = default

    # 字体
    font_rel = str(values.get('text_font') or 'DejaVuSans.ttf')
    name_rel = str(values.get('name_text_font') or font_rel)
    requested_font = _find_file(root, font_rel)
    candidates = _font_candidates(root, font_rel)
    profile.text_font_path = (requested_font
                              or (str(candidates[0]) if candidates else None))
    if not requested_font and profile.text_font_path:
        used = Path(profile.text_font_path).name
        # 同名 = 引擎也会在同一个搜索路径里找到它（典型：SDK 自带的
        # DejaVuSans.ttf）。这不是"替换"，别吓用户。
        if used.lower() != Path(font_rel).name.lower():
            profile.warnings.append(
                f'项目里没有正文字体 {font_rel}，用 {used} 代替（字形可能不同）')
            profile.fonts_substituted.append(f'{font_rel} → {used}')
    elif not profile.text_font_path:
        profile.warnings.append(f'项目里没有正文字体 {font_rel}，'
                                '系统也找不到可用字体')
    requested_name = _find_file(root, name_rel)
    ncandidates = _font_candidates(root, name_rel)
    profile.name_font_path = (requested_name
                              or (str(ncandidates[0]) if ncandidates else
                                  profile.text_font_path))
    if not requested_name and profile.name_font_path and \
            Path(profile.name_font_path).name.lower() != \
            Path(name_rel).name.lower():
        used = Path(profile.name_font_path).name
        profile.fonts_substituted.append(f'{name_rel} → {used}')

    # 背景图
    custom, backgrounds = _parse_say_screen(texts)
    profile.screen_say_custom = custom
    textbox_rel = backgrounds.get('window') or 'gui/textbox.png'
    namebox_rel = backgrounds.get('namebox') or 'gui/namebox.png'
    profile.textbox_image = _find_file(root, textbox_rel)
    profile.namebox_image = _find_file(root, namebox_rel)
    if not profile.textbox_image:
        profile.warnings.append(f'找不到文本框底图 {textbox_rel}')
    if not profile.namebox_image:
        profile.warnings.append(f'找不到名条底图 {namebox_rel}')

    # 角色（注册表名字优先：DynamicCharacter 的真名在 store 变量里）
    registry_names = {}
    if registry is not None:
        for cid, row in (getattr(registry, 'characters', {}) or {}).items():
            registry_names[str(cid)] = str((row or {}).get('Name') or '')
    char_texts = {p: t for p, t in texts.items()
                  if 'Character' in t or 'default' in t}
    if not char_texts:
        char_texts = {str(p): _read(p) for p in _scan_rpy_files(game)[:80]}
    by_id, by_name, warnings = _parse_characters(char_texts, registry_names)
    profile.characters = by_id
    profile.names = by_name
    profile.warnings.extend(warnings)

    _CACHE[key] = profile
    # 只留最近几份，避免长期运行时缓存无限长
    if len(_CACHE) > 32:
        for old in list(_CACHE)[:-16]:
            _CACHE.pop(old, None)
    return profile


__all__ = ['DEFAULT_SCREEN', 'DEFAULTS', 'CharacterStyle', 'GuiProfile',
           'load_gui_profile']
