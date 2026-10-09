"""
asset_registry.py
==================
资产扫描、Excel 读写、解析与校验。

对外提供的接口（被 asset_engine.py 使用）：
  · AssetRegistry           资产注册表数据结构
  · scan_assets             从目录扫描资产（图片/音频）
  · write_asset_excel       将扫描结果写入 Excel
  · parse_asset_tables      从 Excel 目录构建 AssetRegistry
  · load_hooks              加载自定义钩子模块
  · analyze_references      扫描 .rpy 脚本填充 registry.references
  · validate_registry       校验文件存在性、依赖引用、颜色格式
"""

import re
from pathlib import Path
from collections import defaultdict
from typing import Optional
import openpyxl
import importlib.util

from storygraph.core.vocabulary import (
    ASSET_TYPE_ATTR as _TYPE_ATTR,
    RENPY_BUILTIN_TRANSFORMS,
)
from storygraph.excel.cells import append_text_row, set_text

from storygraph.core.safeguard import swallow


# ===================== 常量 =====================
SUPPORTED_IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}
SUPPORTED_AUDIO_EXT = {'.ogg', '.mp3', '.wav', '.m4a', '.flac', '.aac', '.opus'}

CATEGORY_KEYWORDS = {
    'background': ['bg', 'background', 'backdrop', 'scene'],
    'cg': ['cg', 'event', 'cutscene', 'illust'],
    'character': ['character', 'char', 'sprite', 'avatar', 'portrait',
                  'figure', 'people'],
    'ui': ['gui', 'ui', 'dutui', 'interface', 'system', 'icon', 'button',
           'menu'],
    'music': ['bgm', 'music', 'theme', 'loop', 'ost'],
    'sound': ['sfx', 'sound', 'effect', 'se', 'voice', 'click', 'hit'],
}


# ===================== 数据结构 =====================

class AssetRegistry:
    """
    资产注册表。

    · characters / images / transforms / music / sounds: {id: row_dict}
    · custom: {table_name: {id: row_dict}}
    · references: {asset_id: ['file.rpy:line', ...]}
    · errors / warnings: 校验过程中收集的诊断信息
    """

    def __init__(self):
        self.characters = {}
        self.images = {}
        self.transforms = {}
        self.music = {}
        self.sounds = {}
        self.voices = {}
        self.custom = {}
        self.references = defaultdict(list)
        self.unresolved = defaultdict(list)
        #: **不可用的资产**：`{表属性名: {资产 ID: 原因}}`。
        #:
        #: 目前只有一种：表里登记了、但 `Path` 指向的文件**不在了**，
        #: 而且这一行**没有**人工占位标记（见 `is_placeholder_row`）。
        #: 它们仍然留在表/注册表里（对账、体检要看得见），但
        #: `AssetIndex` 建索引时**跳过** → 候选下拉里没有、引用解析不到、
        #: 编译按缺资源处理（用户 2026-10-05："表里有但实际没有的资源也不能用"）。
        self.unusable = defaultdict(dict)
        self.errors = []
        self.warnings = []
        #: 资源表是从哪个项目/目录读来的（缩略图、路径定位要用它找到真实文件）。
        #: 由 parse_asset_tables 填；纯内存构造时是 None。
        self.asset_dir = None
        self.project_root = None

    def get_asset_ids(self, asset_type):
        mapping = {
            'character': self.characters,
            'image': self.images,
            'transform': self.transforms,
            'music': self.music,
            'sound': self.sounds,
            'voice': self.voices,
        }
        return list(mapping.get(asset_type, {}).keys())


# ===================== Excel 读写 =====================

#: 资源表列名：**内部规范键（英文）→ 中文表头**（用户 2026-10-07 要"改中文"）。
#:
#: 为什么内部键不改：`parse_asset_tables` / 改名 / 删行 / 搜索 / 合并
#: 全按 `row['ID']`、`row['Path']` 取值，改内部键等于全仓库换契约。
#: 做法是**只在读写边界翻译**：
#:   · **写**：表头写中文（`write_dict_to_excel`）；
#:   · **读**：中文表头映射回规范键（`_canon_field` / `_sheet_headers`）；
#:   · 于是**老表（英文表头）继续能读**、新表是中文 —— 用户手上的表不用改名。
ASSET_FIELD_LABELS = {
    'ID': '编号',
    'Path': '真实文件',
    'Tag': '角色标签',
    'Attributes': '表情',
    'Category': '类别',
    'Notes': '备注',
    'Loop': '循环',
    'Volume': '音量',
    'ATL': 'ATL',
    'Params': '参数',
    'Doc': '说明',
}

#: 中文表头 → 规范键（读表时用）。**只认我们自己写出去的那些名字**，
#: 用户手加的列（`Custom_xxx` / `内容编号`…）原样保留，绝不改写。
ASSET_FIELD_ALIASES = {label: field
                       for field, label in ASSET_FIELD_LABELS.items()}


def _canon_field(name):
    """表头 → 规范键（中文映射回英文；认不出就**原样返回**）。"""
    if name is None:
        return None
    return ASSET_FIELD_ALIASES.get(str(name).strip(), name)


def _sheet_headers(ws) -> list:
    """
    表头行 → **规范列名**（顺序 = 表里的列顺序）。

    直接读 `ws[1]` 的地方都要用它：表头改成中文之后，`'ID' not in headers`
    这类判据会全部落空（改名/删行**静默不生效**）。
    """
    return [_canon_field(c.value) for c in ws[1]]


#: ⚠ **列顺序目前不动**（用户 2026-10-07 说"与内容编号换位置往前挪"，
#: 但"内容编号"是哪一列还没确认）。
#:
#: 试过一版"顺序表 + 重排表头"（`Path` 提到 `ID` 前），**当场 4 条判据红**：
#: 表头一重排，**按行追加的那些写入点**（固定按 `['ID','Path',…]` 写值）
#: 就和表头错位了 —— 实测 ID 被读成 `game/images/bg/planned.png`
#: （占位行的 ID 变成了路径）。结论：
#:
#:   **换位不是"改表头那一行"就能完成的**：要么把列顺序做成"头与行共用
#:   同一份顺序表"（所有写入点都得跟着走），要么在写表时**按表头名逐列取值**
#:   而不是按位置。两件事都比"改一行"大，得单独一轮做。
#:
#: 所以这一版**只交付中文表头**（已验证：老表照旧能读能改、6 处直接读
#: `ws[1]` 的地方都改走 `_sheet_headers`）。换位等确认列名后再动。


def read_excel_rows(file_path) -> list:
    """
    读取 Excel 首个 sheet，返回 [{header: value, ...}, ...]。

    **`data_only=False`**（2026-10-05，与 `story.excel_import` 同一处修复）：
    资源表的 `Doc` / `Notes` / `ATL` 都是**自由文本**，里面完全可能出现
    以 `=` 开头的内容（transform 说明里的 `=====` 分隔线、用户写的
    `=TODO`）。openpyxl 会给这样的字符串打上 `data_type='f'`，而
    `data_only=True` 读到的是**缓存的计算结果** —— 这张表从没被 Excel
    打开算过，缓存是空的，于是那些格读回来是 `None`，**用户的文本静默消失**。

    要的是"格子里的原始字符串"，不是"公式的值"：资源表里没有需要计算的
    真实公式（下拉框走 defined name，不属于单元格值）。
    """
    file_path = Path(file_path)
    if not file_path.exists():
        return []
    wb = openpyxl.load_workbook(file_path, data_only=False)
    ws = wb.active
    # 表头中文 → 规范键（老表英文表头原样通过）—— 见 `_canon_field`。
    headers = [_canon_field(cell.value) for cell in ws[1]]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if all(v is None for v in row):
            continue
        row_dict = {}
        for i, header in enumerate(headers):
            if header is not None:
                row_dict[header] = row[i] if i < len(row) else None
        rows.append(row_dict)
    wb.close()
    return rows


# 安全写单元格的唯一实现在 `storygraph.excel.cells` —— 这段逻辑曾在这里、
# script_table、compile 三处各写一份（行为已开始漂移）。保留别名，调用点写法不变。

_asset_set_text = set_text
_asset_append_row = append_text_row


def write_dict_to_excel(file_path, data_dict: dict, field_order: list):
    """
    将 {id: row_dict} 写入 Excel。

    表头写**中文**（`ASSET_FIELD_LABELS`），取值仍按**规范键**
    （`field_order` 是内部英文键；`row_data` 也是）——
    所以"改成中文"只动了表头那一行，数据语义一个字节都没变（用户 2026-10-07）。

    列顺序**不动**（用户 2026-10-07 后来确认：`编号`/`ID` 就是"本该藏起来的
    那个工具内部用的东西，已经藏起来就不需要换位"）→ 这里改为**把 `ID` 列隐藏**：
    策划看到的第一列就是 `真实文件`，效果与"换位"一样，但**零风险**
    （隐藏列 openpyxl 照旧读得到，改名/删行/搜索都不受影响）。
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([ASSET_FIELD_LABELS.get(f, f) for f in field_order])
    for _asset_id, row_data in data_dict.items():
        excel_row = [row_data.get(field, '') for field in field_order]
        _asset_append_row(ws, excel_row)
    # **`ID`（编号）整列隐藏**（用户 2026-10-07："它就是那个本该藏起来、
    # 没什么用的内部编号，已经藏起来就不需要换位了"）。
    # 隐藏只是观感：openpyxl 读得到、`rename`/`delete`/搜索全不受影响。
    try:
        from openpyxl.utils import get_column_letter
        col = get_column_letter(list(field_order).index('ID') + 1) \
            if 'ID' in list(field_order) else ''
        if col:
            ws.column_dimensions[col].hidden = True
    except Exception as e:                      # noqa: BLE001 - 隐藏失败不影响数据
        swallow('assets.asset_registry.write_dict_to_excel.hide_id', e)
    wb.save(file_path)


# ===================== 智能扫描 =====================

def _segment_tokens(segment: str) -> list:
    """目录名 → 词元（`Dynamic_Images` → ['dynamic','images']）。"""
    s = str(segment or '').lower()
    for ch in ('_', '-', '.', '(', ')', '[', ']'):
        s = s.replace(ch, ' ')
    return [t for t in s.split() if t]


def _segment_is_ui(segment: str) -> bool:
    """
    这一段是 UI 容器吗（`gui` / `dutui` / `charactercenter` / `button` …）？

    ⚠ **不能拿整条路径做子串匹配**（2026-10-05 实测踩到的坑）：
    `'ui' in path` 会把 `build` / `guide` / `fruit` 也算成 UI；
    `'se'` 会把 `displeased` 算成音效；`'bg'` 会把
    `choice_hover_background.png` 算成背景。所以：

      · 先按词元**精确**匹配（`my_ui_button` → ['my','ui','button'] ✓）；
      · 再按**后缀**收几个常见的复合词（`dutui` 结尾是 `ui`、
        `charactercenter` 结尾是 `center`）—— 这些词尾几乎不会误伤
        （`build` 不以 `ui` 结尾）。
    """
    toks = _segment_tokens(segment)
    if any(t in _UI_WORDS for t in toks):
        return True
    low = str(segment or '').lower().strip()
    return any(low.endswith(sfx) for sfx in _UI_SUFFIXES)


#: UI 目录的**精确词元**（含 `dutui` 拆不开时靠后缀兜）
_UI_WORDS = frozenset({
    'gui', 'ui', 'ux', 'interface', 'interfaces', 'system', 'systems',
    'icon', 'icons', 'button', 'buttons', 'menu', 'menus', 'hud', 'skin',
    'skins', 'widget', 'widgets', 'overlay', 'overlays', 'cursor', 'cursors',
    'font', 'fonts', 'frame', 'frames', 'panel', 'panels', 'dialog',
    'dialogs', 'slider', 'sliders', 'scrollbar', 'scrollbars', 'checkbox',
    'checkboxs', 'window', 'windows', 'title', 'titles', 'logo', 'logos',
    'badge', 'badges', 'bar', 'bars', 'phone', 'phones', 'screen', 'screens',
    'center', 'centers', 'gallery', 'galleries', 'menu_ui', 'dutui',
})
#: UI 目录的**词尾**（复合词：`dutui` / `charactercenter` / `mainmenu`）
_UI_SUFFIXES = ('gui', 'ui', 'hud', 'center', 'screen', 'menu', 'button',
                'icon', 'skin', 'widget', 'panel', 'frame', 'slider', 'bar')

#: 立绘目录的**精确词元**（`characters/ling/` 这种）
_CHARACTER_CONTAINER_WORDS = frozenset({
    'character', 'characters', 'char', 'chars', 'sprite', 'sprites',
    'portrait', 'portraits', 'figure', 'figures', 'actor', 'actors',
    'tachie', 'standing',
})

#: "图片根"目录名：它们的**直接子目录**按角色目录看
#: （`images/eileen/happy.png` → 角色 eileen）
_GENERIC_IMAGE_ROOTS = frozenset({
    'images', 'image', 'img', 'imgs', 'graphics', 'assets', 'art', 'pics',
    'pictures',
})
#: 背景 / CG 目录的精确词元
_BG_WORDS = frozenset({'bg', 'bgs', 'background', 'backgrounds', 'backdrop',
                       'backdrops', 'scene', 'scenes'})
_CG_WORDS = frozenset({'cg', 'cgs', 'event', 'events', 'cutscene',
                       'cutscenes', 'illust', 'illusts', 'illustration',
                       'illustrations', 'gallery', 'galleries'})

#: 文件名里的**弱线索**（只在没有任何目录语义时才用）
#:
#: 背景那条允许**连写**（`bgbed.jpg` / `bgforest.jpg` —— 实测迷你游戏里
#: 全是这种写法），但排掉 `bgm`（那是音乐目录/文件）。
_BG_NAME_RE = re.compile(r'^(bg(?!m)|backdrop|background)([a-z\d_\-. ]|$)')
_CG_NAME_RE = re.compile(r'^(cg|cutscene|event)([\d_\-. ]|$)')
#: 界面/流程类文件名（`start_menu_glamour.jpg` 这种菜单底图）
_UI_NAME_RE = re.compile(
    r'^(start|main|title|titles|menu|settings|config|preference|load|save'
    r'|gameover|game_over|credits|splash|pause|confirm|dialog)'
    r'([\d_\-. ]|$)')
_BG_NAME_HINTS = ('bg_', '_bg', 'background', 'backdrop')
_CG_NAME_HINTS = ('cg_', '_cg', 'cutscene')
_UI_NAME_HINTS = ('ui_', '_ui', 'icon_', '_icon', 'button_', '_button',
                  'btn_', '_btn', 'gui_', '_gui')

#: 平铺文件名首段**不是**角色名的那些词（`images/bg_1.jpg` / `icon_x.png`）
#:
#: 第二组是**界面/流程用的词**（`start_menu_glamour.jpg` 是迷你游戏的菜单底图，
#: 不是谁的立绘）：实测把它们当角色名会造出 `start` 这种不存在的 tag。
_NON_CHARACTER_FIRST_WORDS = frozenset(
    set(_BG_WORDS) | set(_CG_WORDS) | set(_UI_WORDS) | {
        'bgm', 'music', 'sfx', 'sound', 'sound_effect', 'se', 'voice',
        'photo', 'pic', 'pics', 'img', 'imgs', 'image', 'screenshot',
        'clipboard', 'snipaste', 'test', 'tmp', 'temp', 'new', 'old',
        'copy', 'sprite', 'sprites', 'texture', 'textures', 'effect',
        'effects', 'logo', 'map', 'item', 'items', 'prop', 'props',
        # —— 界面 / 流程 / 系统 ——
        'start', 'menu', 'main', 'title', 'titles', 'titlemenu', 'splash',
        'settings', 'config', 'preference', 'preferences', 'option',
        'options', 'load', 'save', 'pause', 'quit', 'exit', 'gameover',
        'game_over', 'credits', 'about', 'help', 'tutorial', 'back', 'next',
        'prev', 'previous', 'close', 'open', 'yes', 'no', 'ok', 'cancel',
        'confirm', 'warning', 'error', 'info', 'notice', 'hint', 'tip',
        'frame', 'panel', 'box', 'line', 'bar', 'label', 'note', 'card',
        'cards', 'list', 'grid', 'table', 'page', 'tab', 'tabs', 'arrow',
        'cursor', 'shadow', 'mask', 'overlay', 'layer', 'filter', 'blur',
    })


#: 扫描时**整目录跳过**的名字：这些不是游戏素材。
#:
#: 实测（2026-10-05，用户克隆）：`tmp/` 里有 11 张 `about_collection_smoke.png`
#: 之类的临时截图被当成资源收进表，于是"指向 `game/` 之外的行"多出 12 条，
#: 每次对账都在报噪声。Ren'Py 的素材在 `game/` 下，项目根上只放工程文件。
_SKIP_SCAN_DIRS = frozenset({
    'tmp', 'temp', 'temporary', '.git', '.svn', '.hg', '__pycache__',
    'node_modules', '.venv', 'venv', 'env', 'saves', 'cache', '.cache',
    '.pytest_cache', '.mypy_cache', '.idea', '.vscode', 'build',
    '.workbuddy', 'logs', 'log',
})


def _under_skip_dir(rel_path: Path) -> bool:
    """这一条路径下面是不是有"整目录跳过"的名字？"""
    for part in rel_path.parts[:-1]:
        if part.lower() in _SKIP_SCAN_DIRS:
            return True
    return False


def _path_under_skip_dir(path, scan_root) -> bool:
    """
    `_under_skip_dir` 的**安全版**：路径可能不在 `scan_root` 底下
    （音频目录允许在项目外），那时只拿绝对路径自己比。

    ⚠ **调用方必须传"这个文件是被哪个目录扫进来的"那个目录**，不能一律传
    项目根（2026-10-07 修，长期红的 `test_custom_audio_dirs` 就是它）：

      · `scan_root` 内的文件 → 相对路径照旧 ✓；
      · **项目外**的音频目录 → 相对路径算不出来，退回**绝对路径**逐段比，
        于是路径里任何一段叫 `temp` / `build` / `cache` / `logs` … 都会命中
        `_SKIP_SCAN_DIRS` 而被**整条丢掉** ——
        实测 `…\\AppData\\Local\\Temp\\extra_audio\\theme_song.mp3` 里的
        `Temp` 命中，音乐/音效**双双为空**；
        真实项目只要放在 `D:\\build\\我的游戏\\…` 这种路径下，音频会**全部消失**。

      传"扫描基准目录"（`adir` / `vdir`）之后：`build/` 这类子目录照样跳过
      （它就在基准下面），而 `Temp` / `AppData` 这些**无关祖先**不再参与判定。
    """
    try:
        rel = Path(path).relative_to(scan_root)
    except ValueError as e:                     # 不在基准目录内：按绝对路径比
        swallow('assets.asset_registry._path_under_skip_dir', e)
        rel = Path(path)
    return _under_skip_dir(rel)


def _detect_category(rel_path: Path) -> str:
    """
    路径 → 资产类别（`background` / `cg` / `character` / `ui` / `other`）。

    **唯一真源**：只认**目录段**的语义，从最靠近文件的目录往外找；
    文件名只在"一点目录线索都没有"时当弱线索。

    为什么重写（2026-10-05 用户实测："现在的 ui bg 一类的仍然不太准"）：
    旧实现把**整条路径**当字符串做 `kw in path_str`，于是

      · `game/photo/music_room_photo/*.png` → `music`（图片被判成音乐，16 行）；
      · `game/pomodorro/images/dororo_displeased.png` → `sound`
        （`se` 命中了 `displea**se**d`，12 行）；
      · `game/gui/button/choice_hover_background.png` → `background`
        （按钮图被当成背景，16 行）；
      · `game/charactercenter/base.png` → `character`（界面图被当成立绘）；
      · `game/gui/CG_frame.png` / `game/gui/dutui/**/CG鉴赏.png` → `cg`
        （界面帧被当成 CG）。

    新规则（真实项目逐条对齐过）：

      · **最近目录优先**：`gui/dutui/Frame 24/CG鉴赏.png` → 最近的是 `dutui`
        → ui ✓（`gui` 更远，但同样是 ui）；
      · 目录语义**压过**文件名线索：`gui/button/*_background.png` → ui ✓；
      · 只有"整条路径一个语义目录都没有"时才看文件名：
        `images/bg_1.jpg` → background ✓、`images/cg01.png` → cg ✓；
      · 都不认 → `other`（**不再瞎猜**：`pomodorro/images/*`、`minigamepieces/*`
        这些就是 other）。
    """
    dirs = [p for p in rel_path.parts[:-1] if p]
    for seg in reversed(dirs):                     # 从最近往外
        toks = _segment_tokens(seg)
        if not toks:
            continue
        if any(t in _CG_WORDS for t in toks):
            return 'cg'
        if any(t in _BG_WORDS for t in toks):
            return 'background'
        if any(t in _CHARACTER_CONTAINER_WORDS for t in toks):
            return 'character'
        if _segment_is_ui(seg):
            return 'ui'
    # `images/<tag>/x.png`：**`images` 的直接子目录**就是角色目录 —— 这是最常见的
    # 立绘布局（`game/images/eileen/happy.png`），而目录名本身（`eileen`）
    # 看不出任何语义。⚠ 只认"父目录的父目录叫 images"这一种：
    # `game/pomodorro/images/dororo.png`（文件直接躺在 `images/` 里）不算，
    # `game/minigamepieces/logo/x.png` 也不算（那个 `logo` 上面不是 `images`）。
    if len(dirs) >= 2 and dirs[-2].lower() in _GENERIC_IMAGE_ROOTS:
        return 'character'
    name = rel_path.name.lower()                   # 弱线索：只看文件名
    stem = rel_path.stem.lower()
    if _CG_NAME_RE.match(stem) or any(h in name for h in _CG_NAME_HINTS):
        return 'cg'
    if _BG_NAME_RE.match(stem) or any(h in name for h in _BG_NAME_HINTS):
        return 'background'
    if _UI_NAME_RE.match(stem) or any(h in name for h in _UI_NAME_HINTS):
        return 'ui'
    # **平铺约定**：`images/eileen_happy.png` —— 目录无语义，但文件名是
    # `<tag>_<属性>`（Ren'Py 自动图像的惯例），按立绘收，tag = 首段。
    #
    # ⚠ 只认"确实在 `images/` 下"的：项目根目录的零散图
    # （`about_collection_smoke.png`）不算 —— 否则会给它编出一个
    # 脚本里根本不存在的角色 tag（用户表里那些行 Tag 是空的）。
    low_dirs = [d.lower() for d in dirs]
    if 'images' in low_dirs or 'image' in low_dirs:
        toks = _segment_tokens(stem)
        if len(toks) >= 2 and toks[0] not in _NON_CHARACTER_FIRST_WORDS:
            return 'character'
    return 'other'


# 图片目录里"只是容器、不含语义"的名字：它们不能当 image tag 用
# （`images/eileen/happy.png` 的 tag 是 eileen 而不是 images）。
_GENERIC_IMAGE_DIRS = frozenset({
    'game', 'images', 'image', 'img', 'imgs', 'assets', 'asset', 'graphics',
    'sprite', 'sprites', 'characters', 'character', 'char', 'chars',
    'pics', 'pic', 'pictures', 'picture', 'figure', 'figures', 'art',
    'portrait', 'portraits', 'avatar', 'avatars', 'story', 'data',
})


def _normalize_tag(name: str) -> str:
    """目录名/文件名 → image tag：去分隔符、转小写。"""
    name = str(name or '').strip()
    for ch in ('_', '-'):
        name = name.replace(ch, ' ')
    return ' '.join(name.split()).lower()


def _detect_image_tag(file: Path, rel_path: Path, category: str) -> str:
    """
    推断一张图在脚本里的 **image tag**。

    这是 P0 的关键：脚本写的是 `show eileen happy`（tag + attribute），
    而资产 ID 取的是文件名（`happy`）。算不出 tag，两边的映射就断了，
    `eileen/` 目录下的立绘会被整组误报成"未使用资产"。

    规则（**真实项目逐条对齐过**，2026-10-05 重写）：

      · 背景 / CG / UI → 固定 tag（`bg` / `cg` / `ui`）；
      · 立绘：`characters/<tag>/…` 里 **`<tag>` 就是角色名** —— 取容器目录的
        **下一段**，不再"从最近往外找第一个非通用目录"：
          `images/characters/lan/dynamic_images/lan_body.png` → `lan`
          （旧实现给的是 `dynamic images` ✗ 22 行）
          `images/characters/avatar/lan.png` → `avatar`
          （旧实现给 `''` ✗ —— 因为 `avatar` 在通用目录表里，
            可这里它就是角色名）
      · 没有 `characters/` 容器时：文件名首段（`eileen_happy.png` → `eileen`）；
      · **其余一律空**：`other` 类的图（`pomodorro/images/*`、`minigamepieces/*`、
        根目录的零散图）**不再拿目录名硬编一个 tag** —— 实测那会造出
        `pomodorro` / `ball moving game` / `music room photo` / `logo` 这些
        **脚本里根本不存在的 tag**（用户表里这些行的 Tag 是空的）。
    """
    if category == 'background':
        return 'bg'
    if category == 'cg':
        return 'cg'
    if category == 'ui':
        return 'ui'

    parts = list(rel_path.parts[:-1])              # 目录段（不含文件名）
    for i, seg in enumerate(parts):
        toks = _segment_tokens(seg)
        if any(t in _CHARACTER_CONTAINER_WORDS for t in toks):
            # 容器目录的下一段 = 角色名（`characters/ling/happy.png` → ling）
            if i + 1 < len(parts):
                nxt = _normalize_tag(parts[i + 1])
                if nxt:
                    return nxt
            stem = file.stem
            return _normalize_tag(stem.split('_')[0]) if '_' in stem else ''
    if category != 'character':
        return ''
    # **没有 `characters/` 容器**（`images/eileen/happy.png` 这种最常见）：
    #   · 文件就躺在通用图片目录下（`images/dororo_displeased.png`）→ 平铺约定，
    #     tag = 文件名首段；
    #   · 否则 → 最近的那个有语义的目录名（`images/eileen/happy.png` → eileen）。
    parent = parts[-1] if parts else ''
    if not parent or parent.lower() in _GENERIC_IMAGE_DIRS:
        stem = file.stem
        if '_' in stem:
            return _normalize_tag(stem.split('_')[0])
        return ''
    for seg in reversed(parts):
        low = seg.lower()
        if low in _GENERIC_IMAGE_DIRS:
            continue
        if any(t in _CHARACTER_CONTAINER_WORDS
               for t in _segment_tokens(seg)):
            continue
        return _normalize_tag(seg)
    return ''


# 音频用途判定的目录白名单。目录名比文件名可靠得多：
# `audio/night.ogg` 是首曲子，但文件名里既没有 bgm 也没有 music，
# 旧实现一律判成"音效"——于是 Music 下拉里空空如也，文案根本选不到音乐。
_MUSIC_DIR_NAMES = {'music', 'bgm', 'bgm_music', 'ost', 'soundtrack',
                    'bgm_music_box'}
_SOUND_DIR_NAMES = {'sound', 'sounds', 'sfx', 'se', 'effect', 'effects',
                    'ampl', 'noise'}

# 文件名里的"用途线索"。音效线索优先判定：`sfx_xxx.ogg` 就算放在
# music/ 目录下也是音效，名字比目录更具体。
_SOUND_NAME_HINTS = ('sfx', 'se_', '_se', 'sound', 'effect', 'click', 'hit',
                     'voice', 'button', 'ui_', 'impact', 'step', 'door',
                     'noise', 'beep', 'alarm', 'switch')
_MUSIC_NAME_HINTS = ('bgm', 'music', 'theme', 'ost', 'song', 'loop',
                     'soundtrack')


def _classify_audio(rel_path: Path) -> str:
    """
    判断一个音频文件是 music 还是 sound。

    优先级：目录语义 > 文件名线索 > 默认 music。

    为什么默认 music：文案最在意"音乐能不能选到"。`game/audio/night.ogg`
    这种既不在音效目录、名字也不像音效的文件，当音乐收进下拉，文案就能
    选；判成音效则它永远不出现在 Music 列，这正是用户抱怨的痛点。
    真正的音效几乎都带 sfx_/se_/click 这类名字或放在 sfx/ 目录。
    """
    parts = [p.lower() for p in rel_path.parts[:-1]]
    for p in reversed(parts):
        if p in _SOUND_DIR_NAMES:
            return 'sound'
        if p in _MUSIC_DIR_NAMES:
            return 'music'

    name = rel_path.name.lower()
    if any(h in name for h in _SOUND_NAME_HINTS):
        return 'sound'
    if any(h in name for h in _MUSIC_NAME_HINTS):
        return 'music'

    path_str = rel_path.as_posix().lower()
    if any(kw in path_str for kw in CATEGORY_KEYWORDS['music']):
        return 'music'
    return 'music'


def _make_unique_id(stem: str, parent_dirs: list, existing_ids: set) -> str:
    """生成唯一 ID：优先用 stem，冲突时逐步添加父目录名。"""
    if stem not in existing_ids:
        existing_ids.add(stem)
        return stem
    for i in range(len(parent_dirs) - 1, -1, -1):
        new_id = f"{parent_dirs[i]}_{stem}"
        if new_id not in existing_ids:
            existing_ids.add(new_id)
            return new_id
    counter = 1
    while True:
        new_id = f"{stem}_{counter}"
        if new_id not in existing_ids:
            existing_ids.add(new_id)
            return new_id
        counter += 1


def _is_scan_noise(filename: str) -> bool:
    """
    过滤掉"不是真资产"的噪声文件，避免误扫进资源表。

    典型来源：从聊天/浏览器粘贴截图时产生的
    ``Clipboard_Screenshot.png``、``屏幕截图 xxx.png``、``Snipaste_xxx.png``，
    以及 Excel 的临时锁文件 ``~$xxx.xlsx``。这些不该出现在候选下拉里。
    """
    name = (filename or '').lower()
    if not name:
        return True
    if name.startswith('~$'):
        return True
    for kw in ('clipboard', 'screenshot', '屏幕截图', '截屏',
               'snipaste', 'screen shot'):
        if kw in name:
            return True
    return False


def scan_assets(scan_root, include_ui=True, audio_dirs=None,
                voice_dirs=None, walk=None) -> dict:
    """
    扫描目录下的资产，返回：
    {
        'images': [...],
        'audio': {'music': [...], 'sound': [...]},
        'voices': [...],          # 语音文件（game/voice/ 等）
    }

    scan_root:   扫描根目录（Path 或 str）
    include_ui:  是否包含 UI 相关图片
    audio_dirs:  自定义音频目录列表；None 则自动探测
    voice_dirs:  自定义语音目录列表；None 则自动探测（game/voice/、
                 game/audio/voice/）
    """
    scan_root = Path(scan_root)
    if not scan_root.exists():
        return {'images': [], 'audio': {'music': [], 'sound': []},
                'voices': []}

    image_assets = []
    audio_music = []
    audio_sound = []
    voice_assets = []

    # 音频目录自动探测。要同时看项目根和 Ren'Py 的 game/ 目录——
    # 真实项目里音频在 game/audio/、game/music/，只探根目录会全部漏掉，
    # 于是退化成"整棵树 rglob"，全靠文件名猜音乐还是音效。
    #
    # ⚠ 2026-10-05 实测修：以前这里只探**写死的几个名字**（`audio` / `music` /
    # `sounds` / `bgm`，且只在根与 `game/` 两层）—— 用户项目里
    # `game/gui/sfx/*.wav`（8 个音效）和 `game/pomodorro/music/*`（7 个音乐，
    # 嵌在子模块里）**根本没进目录列表**，于是 49 个音频文件只扫出 34 个、
    # 「音效 0」。
    #
    # 现在按**目录名**在任意深度认（`sfx` / `se` 这类常见写法也认），
    # 认不到任何目录时才退回"整棵树"。
    #
    # ⚠ 有 `walk` 时这两段探测**整个跳过**：走索引那条路按**目录段名**判类型
    # （`_classify_audio` + `voice/` 段名），根本不需要目录列表；而这两个
    # `rglob` 在真实项目要白跑 1.9 秒（用户："太卡了"）。
    if audio_dirs is None and walk is None:
        known = {'audio', 'music', 'bgm', 'sound', 'sounds', 'sfx', 'se',
                 'voice', 'voices'}
        found = []
        try:
            for d in scan_root.rglob('*'):
                try:
                    if d.is_dir() and d.name.lower() in known:
                        found.append(d)
                except OSError:
                    continue
        except OSError as e:                    # noqa: BLE001 - 目录读不动就算了
            swallow('assets.asset_registry.scan_assets.audio_dirs', e)
        audio_dirs = found if found else [scan_root]

    # 语音目录自动探测（语音常独立存放，不应混进音效候选）
    if voice_dirs is None and walk is None:
        vcands = []
        for base in (scan_root, scan_root / 'game'):
            vcands += [base / 'voice', base / 'audio' / 'voice',
                       base / 'voices', base / 'sound' / 'voice']
        # 任意深度的 `voice/` / `voices/` 也算（`game/pomodorro/voice/` 这种）
        known_voice = {'voice', 'voices'}
        try:
            for d in scan_root.rglob('*'):
                try:
                    if d.is_dir() and d.name.lower() in known_voice:
                        vcands.append(d)
                except OSError:
                    continue
        except OSError as e:                    # noqa: BLE001
            swallow('assets.asset_registry.scan_assets.voice_dirs', e)
        voice_dirs = [d for d in vcands if d.exists()]
    voice_dirs = list(voice_dirs or [])

    existing_image_ids = set()

    # ---------- 图片扫描 ----------
    #
    # 有 walk（一次遍历的索引）就用它枚举 —— 原先这里自己 rglob 一遍，
    # 音频/语音又各走一遍，真实项目**三遍树 ~3.8 秒**（用户："太卡了"）。
    if walk is not None:
        # ⚠ 用索引里**已经算好的相对路径**，别再逐文件 `Path.relative_to()`：
        # Windows 上一次 relative_to 要 0.34 ms，3273 张图实测 **2.3 秒**。
        # 排序是为了**确定性**（遍历顺序随目录栈而变；同名文件靠
        # `_make_unique_id` 编后缀时，顺序不稳就会得到不同的 ID）。
        #
        # ⚠⚠ **嵌套副本不算项目资源**（`sr_general/game/…`）：把它们扫进来
        # 会编出 `ling_angry` 这种 ID 去跟真正的"tag_attribute"抢解析，
        # 实测直接导致"立绘不显示 + 表情调不了"。
        _items = sorted(((rel, p) for rel, p in walk.files().items()
                         if not walk.is_nested(rel)), key=lambda kv: kv[0])
    else:
        _items = []
        for p in scan_root.rglob('*'):
            if not p.is_file():
                continue
            try:
                _items.append((p.relative_to(scan_root).as_posix().lower(), p))
            except ValueError:
                continue
    for rel_low, file in _items:
        if file.suffix.lower() not in SUPPORTED_IMAGE_EXT:
            continue
        if _is_scan_noise(file.name):
            continue
        rel_path = Path(rel_low)
        rel_str = rel_low

        if _under_skip_dir(rel_path):
            continue                            # tmp/ 之类的目录不是素材

        if not include_ui:
            if any(kw in rel_str
                   for kw in ('gui', 'ui', 'dutui', 'interface', 'system')):
                continue

        category = _detect_category(rel_path)
        parent_dirs = [Path(seg).name for seg in rel_low.split('/')[:-1]]

        # tag **只有一个真源** = `_detect_image_tag`（2026-10-05 修）。
        #
        # 这里从前对 `category == 'character'` 另写了一套内联逻辑（找"含
        # character/char/sprite… 关键词的目录段"），于是：
        #   `game/images/eileen/happy.png`（**没有** `characters/` 容器，最常见的
        #   立绘布局）走的是内联那套 → 找不到关键词 → tag 退成文件名首段 `happy`
        #   → 脚本里的 `show eileen happy` 解析不到它 → 整组立绘被报"未使用"。
        # 两套实现必然漂移，所以直接把内联那套删掉。
        tag = _detect_image_tag(file, rel_path, category)
        attributes = file.stem

        unique_id = _make_unique_id(file.stem, parent_dirs, existing_image_ids)
        image_assets.append({
            'ID': unique_id,
            'Path': rel_path.as_posix(),
            'Tag': tag,
            'Attributes': attributes,
            'Category': category,
            'Notes': '',
        })

    # ---------- 音频扫描 ----------
    #
    # **有 `walk` 时全部从那一份索引里取**（一次遍历）：目录探测与文件枚举
    # 都不再各走一遍树。没给 walk 时保持老路（其它调用方/测试照旧）。
    if walk is not None:
        for rel_low, file in _items:
            if file.suffix.lower() not in SUPPORTED_AUDIO_EXT:
                continue
            if _is_scan_noise(file.name):
                continue
            rel_path = Path(rel_low)
            if _under_skip_dir(rel_path):
                continue
            stem = file.stem
            # 语音目录（任意深度的 `voice/` / `voices/`）单独收
            if any(p.lower() in ('voice', 'voices')
                   for p in rel_path.parts[:-1]):
                voice_assets.append({
                    'ID': stem, 'Path': rel_path.as_posix(), 'Notes': '',
                })
                continue
            if _classify_audio(rel_path) == 'music':
                audio_music.append({
                    'ID': f"bgm_{stem}" if not stem.startswith('bgm_')
                          else stem,
                    'Path': rel_path.as_posix(),
                    'Loop': True, 'Volume': 1.0, 'Notes': '',
                })
            else:
                audio_sound.append({
                    'ID': f"sfx_{stem}" if not stem.startswith('sfx_')
                          else stem,
                    'Path': rel_path.as_posix(), 'Notes': '',
                })
        return {
            'images': image_assets,
            'audio': {'music': audio_music, 'sound': audio_sound},
            'voices': voice_assets,
        }

    voice_dirs_resolved = [Path(d) for d in voice_dirs]

    def _under_voice(file: Path) -> bool:
        for vd in voice_dirs_resolved:
            try:
                file.relative_to(vd)
                return True
            except ValueError:
                continue
        return False

    for adir in audio_dirs:
        adir = Path(adir)
        if not adir.exists():
            continue
        for file in adir.rglob('*'):
            if not file.is_file() or file.suffix.lower() not in SUPPORTED_AUDIO_EXT:
                continue
            if _is_scan_noise(file.name):
                continue
            if _path_under_skip_dir(file, adir):
                continue
            # 语音目录已经单独扫过，别再把同一个文件当作音效收一遍
            if _under_voice(file):
                continue
            try:
                rel_path = file.relative_to(scan_root)
            except ValueError:
                rel_path = file
            stem = file.stem
            if _classify_audio(rel_path) == 'music':
                audio_music.append({
                    'ID': f"bgm_{stem}" if not stem.startswith('bgm_')
                          else stem,
                    'Path': rel_path.as_posix(),
                    'Loop': True,
                    'Volume': 1.0,
                    'Notes': '',
                })
            else:
                audio_sound.append({
                    'ID': f"sfx_{stem}" if not stem.startswith('sfx_')
                          else stem,
                    'Path': rel_path.as_posix(),
                    'Notes': '',
                })

    # ---------- 语音扫描（独立目录，不与音效混） ----------
    for vdir in voice_dirs:
        vdir = Path(vdir)
        if not vdir.exists():
            continue
        for file in vdir.rglob('*'):
            if not file.is_file() or file.suffix.lower() not in SUPPORTED_AUDIO_EXT:
                continue
            if _is_scan_noise(file.name):
                continue
            if _path_under_skip_dir(file, vdir):
                continue
            try:
                rel_path = file.relative_to(scan_root)
            except ValueError:
                rel_path = file
            voice_assets.append({
                'ID': file.stem,
                'Path': rel_path.as_posix(),
                'Notes': '',
            })

    return {
        'images': image_assets,
        'audio': {'music': audio_music, 'sound': audio_sound},
        'voices': voice_assets,
    }


# ===================== Excel 生成 =====================

def asset_table_dirs(project_root, asset_dir=None) -> list:
    """
    资源表的候选目录 —— **顺序必须与 `pipeline.build.load_asset_registry` 一致**。

    （两处各写一套的话，"能找到但建在别处"就会变成"每次打开都要重建"。）
    """
    root = Path(project_root)
    out = []
    if asset_dir:
        out.append(Path(asset_dir))
    out += [root / 'asset_tables', root / 'game' / 'asset_tables']
    return out


def is_real_table_file(p) -> bool:
    """
    这是一个**真资源表**吗？

    排除三类"看着像表、其实不该当表用"的文件（2026-10-05 实测踩到）：

      · `images.bak.xlsx` —— 我们自己的备份（把目录里只剩备份的情况误判成
        "已经有表了"，于是永远不建表、打开项目得到一张**空表**）；
      · `~$images.xlsx` —— Excel 打开文件时放的锁文件；
      · `*.broken-<时间戳>.xlsx` —— 读不出来时我们挪到一边的坏文件。
    """
    name = str(getattr(p, 'name', p))
    low = name.lower()
    if name.startswith('~$'):
        return False
    if low.endswith('.bak.xlsx') or '.broken-' in low:
        return False
    return low.endswith('.xlsx')


#: 兼容内部旧写法。
_is_real_table_file = is_real_table_file


def find_asset_tables(project_root, asset_dir=None):
    """已经有资源表（任一候选目录里有**真表**）→ 返回那个目录；没有返回 None。"""
    for d in asset_table_dirs(project_root, asset_dir):
        try:
            if d.is_dir() and any(is_real_table_file(p)
                                  for p in d.glob('*.xlsx')):
                return d
        except OSError:
            continue
    return None


def _quarantine_broken_tables(table_dir, reason: str = '') -> list:
    """
    把**读不出来**的表文件挪到一边（改名成 `*.broken-<时间戳>.xlsx`），返回清单。

    为什么不删：那是用户的数据 —— 也许是 Excel 正开着（锁住）、也许写坏了，
    但**都要留着**让人自己去救（或者从我们的 `.bak.xlsx` 恢复）。
    能读的文件一个都不动（例如只有 `images.xlsx` 坏了，`characters.xlsx`
    照旧保留，重建时不会被覆盖）。
    """
    moved = []
    import time
    stamp = time.strftime('%Y%m%d-%H%M%S')
    try:
        for p in sorted(Path(table_dir).glob('*.xlsx')):
            if not is_real_table_file(p):
                continue
            try:
                read_excel_rows(p)               # 读得动就留着
                continue
            except Exception as e:               # noqa: BLE001 - 读不动 → 挪走
                swallow('assets.asset_registry._quarantine_broken_tables', e)
            target = p.with_name(f'{p.stem}.broken-{stamp}.xlsx')
            try:
                p.rename(target)
                moved.append(target.name)
            except OSError as e:                 # noqa: BLE001 - 挪不动就算了
                swallow('assets.asset_registry._quarantine_broken_tables.move', e)
    except Exception as e:                       # noqa: BLE001
        swallow('assets.asset_registry._quarantine_broken_tables.scan', e)
    if moved and reason:
        pass                                     # 原因由调用方写进日志
    return moved


def compare_registry_to_disk(project_root, registry, include_ui=True,
                             walk=None, assets=None) -> dict:
    """
    **资源表 vs 磁盘**的对账（**只读**，一个文件都不改）。

    返回：

        {
          'disk':  {'images': 1645, 'music': 41, 'sounds': 8, 'voices': 0},
          'table': {'images': 2890, 'music': 41, 'sounds': 8, 'voices': 0},
          'unregistered': {'music': ['game/gui/sfx/点击.wav', ...]},  # 盘上有、表里没有
          'missing':      {'images': ['game/images/gone.png', ...]},  # 表里有、盘上找不到
          'stale_paths': 0,
        }

    为什么要它（用户 2026-10-05 实测报）：

    > 你确定这对吗？为何两个克隆出来的项目并不一样？
    > 每次导入的时候你的表是不是没更新？？？

    两个克隆报出来的数字不一样，原因是**资源的"真相"有两个**：

      · **表**（`game/asset_tables/*.xlsx`）是**用户可编辑的数据**，
        工具**从不自动改写**（只在完全没有表时才新建）；
      · **磁盘**是另一份真相。

    两者漂移时，界面上（下拉候选、缩略图）看到的是**表**，而用户以为
    看到的是项目 —— 于是"为什么这个克隆少了一千多张图"这种问题只能靠猜。
    实测那次现场就是：自动新建的表漏了 15 个音频（`game/gui/sfx/*.wav`
    与 `game/pomodoro/music/*` 不在写死的音频目录列表里，已修），
    而旧表里那 15 个是有的。

    这个函数只回答"差在哪"，补登由 `session.register_missing_assets()`
    （UI 的「补登缺失资源」，写前自动备份）负责。
    """
    from storygraph.story.excel_export import resolve_thumb_source
    root = Path(project_root)
    game_dir = root / 'game'                    # 只用于"是不是在 game/ 外"的判断
    table_paths = {}
    missing = {}
    #: 表里指到 `game/` **外面**的行（`{顶层目录: 行数}`）—— 见下面注释
    outside = {}
    outside_total = 0
    for kind, attr in (('image', 'images'), ('music', 'music'),
                       ('sound', 'sounds'), ('voice', 'voices')):
        key = {'image': 'images', 'music': 'music', 'sound': 'sounds',
               'voice': 'voices'}[kind]
        bucket = set()
        gone = []
        for _aid, row in ((getattr(registry, attr, {}) or {}) if registry
                          else {}).items():
            rel = str((row or {}).get('Path') or '').strip()
            if not rel:
                continue
            # ⚠ 有 `walk` 时**只查字典**：原先这里对每一行做
            # `resolve_thumb_source`（逐个基准 `is_file()`）+ `Path.resolve()`
            # —— 3123 行要上万次系统调用，实测这一步 **12.4 秒**，正是用户
            # "程序干未响应了！太卡了"的元凶。见 `FileWalkIndex`。
            if walk is not None:
                real = walk.match(rel)
            elif kind == 'image':
                real = resolve_thumb_source(rel, root)
            else:
                real = _resolve_audio_source(rel, root)
            if real is None:
                gone.append(rel)
                continue
            bucket.add(str(real))
            # **表里指到 `game/` 外面的行**：真实项目实测有 1386 行指向
            # `sr_general/**`（**另一份嵌套副本**）—— 这就是"两个克隆数字
            # 为什么不一样"的答案：表引用的是别的目录里的文件。
            try:
                Path(real).relative_to(game_dir)
            except ValueError:
                outside_total += 1
                try:
                    top = real.relative_to(root).parts[0]
                except ValueError:
                    top = str(real.drive or real.anchor or '')
                outside[top] = outside.get(top, 0) + 1
        table_paths[key] = bucket
        if gone:
            missing[key] = gone

    if assets is None:
        assets = scan_assets(root, include_ui=include_ui, walk=walk)
    disk = {
        'images': [a.get('Path') for a in (assets.get('images') or [])],
        'music': [a.get('Path') for a in
                  ((assets.get('audio') or {}).get('music') or [])],
        'sounds': [a.get('Path') for a in
                   ((assets.get('audio') or {}).get('sound') or [])],
        'voices': [a.get('Path') for a in (assets.get('voices') or [])],
    }
    disk_counts = {k: len([p for p in v if p]) for k, v in disk.items()}
    unregistered = {}
    for key, paths in disk.items():
        have = table_paths.get(key) or set()
        extra = []
        for rel in paths:
            if not rel:
                continue
            if walk is not None:
                real = walk.match(rel)
            elif key == 'images':
                real = resolve_thumb_source(rel, root)
            else:
                real = _resolve_audio_source(rel, root)
            if real is None:
                continue
            # ⚠ 有 `walk` 时**别再 `resolve()`**：两边都出自同一份索引，
            # 字符串本来就一致。实测这里的 `Path.resolve()` 3373 次要 **3.8 秒**
            # （Windows 上 resolve 会摸文件系统）—— 这是"对账还要 4 秒"的元凶。
            same = str(real) if walk is not None else str(Path(real).resolve())
            if same not in have:
                extra.append(str(rel))
        if extra:
            unregistered[key] = extra
    return {
        'disk': disk_counts,
        'table': {k: len(v) for k, v in table_paths.items()},
        #: 表里各类型**解析后的绝对路径**（判重必须用它，见 `sync_asset_tables`）。
        'table_resolved': {k: set(v) for k, v in table_paths.items()},
        'unregistered': unregistered,
        'missing': missing,
        'stale_paths': sum(len(v) for v in missing.values()),
        #: 表里指到 `<root>/game/` 外面的行数 + 按顶层目录的分布。
        #: 真实项目实测：1386 行落在 `sr_general/**`（另一份嵌套副本）——
        #: 那不是"文件丢了"，而是"表引用的是别的目录"，两种情况给出的
        #: 补救完全不同，所以必须分开报。
        'outside_game': {'count': outside_total, 'by_top': dict(outside)},
    }


def _resolve_audio_source(rel, project_root=None, walk=None):
    """
    音频的 Path 基准和图片一样乱，用同一套候选（只是扩展名不同）。

    给了 `walk`（`FileWalkIndex`）就**只在字典里查**（不碰磁盘）——
    这是"打开项目 N 秒 → 毫秒"的关键，见 `FileWalkIndex` 的说明。
    """
    if walk is not None:
        hit = walk.match(rel)
        return hit
    text = str(rel or '').strip().replace('\\', '/')
    if not text:
        return None
    p = Path(text)
    cands = []
    if p.is_absolute():
        cands.append(p)
    else:
        parts = [q for q in text.split('/') if q not in ('', '.')]
        bases = [Path(project_root) if project_root else Path.cwd()]
        if project_root:
            root = Path(project_root)
            bases = [root, root.parent, root / 'game', Path.cwd()]
        for base in bases:
            cands.append(base / text)
            if len(parts) > 1:
                cands.append(base / '/'.join(parts[1:]))
                cands.append(base / 'game' / '/'.join(parts[1:]))
    for c in cands:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def _reload_tables_and_diff(table_dir, root, walk, assets, tag: str):
    """
    删 / 改指 / 合并之后**重新读表并重算对账**（内存里的注册表已经过期）。

    三处一模一样 → 收敛成一处（M4 重构：只为拆复杂度，行为一字不改）。
    失败只留痕，**返回 `(None, None, None)`**，调用方按"保持原值"处理
    —— 与从前"except 里什么都不赋"等价。
    """
    try:
        registry = parse_asset_tables(
            table_dir, hooks=load_hooks(project_root=root),
            project_root=root, walk=walk)
        diff = compare_registry_to_disk(root, registry, walk=walk,
                                        assets=assets)
        return registry, diff, (diff or {}).get('unregistered') or {}
    except Exception as e:                      # noqa: BLE001
        swallow(f'assets.asset_registry.sync_asset_tables.{tag}', e)
        return None, None, None


#: 资源表里"按文件一一对应"的四类（舞台/音频之外的都归这里）。
_ASSET_KINDS = ('images', 'music', 'sounds', 'voices')


def _junk_nested_paths(rows, mine_paths, nested_pref) -> list:
    """
    工具自己加过、**却指向嵌套副本**的那些 `Path`（纯函数）。

    "嵌套副本"= 项目根下另有一份带 `game/` 的树（`walk.nested_prefixes()`）。
    那些文件不属于本项目资源；行却是工具自己补登的（侧车记着）→ 该删。
    用户**自己写的**行不在此列（不在侧车里就不动）。
    """
    mine = {norm_asset_path(p) for p in (mine_paths or [])}
    if not mine or not nested_pref:
        return []
    out = []
    for _aid, row in (rows or {}).items():
        rel = str((row or {}).get('Path') or '')
        low = norm_asset_path(rel)
        if low in mine and any(low.startswith(p) for p in nested_pref):
            out.append(rel)
    return out


def _missing_paths(rows, skip, kind_key, walk, root) -> list:
    """
    `Path` 解析不到真实文件的行（纯查询；人工占位行豁免）。

    `skip` 是"上面已经删过的"（不重复报）。
    """
    from storygraph.story.excel_export import resolve_thumb_source

    out = []
    for _aid, row in (rows or {}).items():
        rel = str((row or {}).get('Path') or '').strip()
        if not rel or is_placeholder_row(row):
            continue                            # 占位：允许没有文件
        if rel in skip:
            continue                            # 上面已经删了
        if walk is not None:
            real = walk.match(rel)
        elif kind_key == 'images':
            real = resolve_thumb_source(rel, root)
        else:
            real = _resolve_audio_source(rel, root)
        if real is None:
            out.append(rel)
    return out


def _sync_prune_stage(root, table_dir, registry, walk):
    """
    ❶ **自动删**：`Path` 解析不到真实文件的行跟着走。

    两个例外（与用户 2026-10-05 的定案一致）：
      · **人工占位**（`Notes`/`Doc` 写了占位…）留着；
      · **工具自己加过、却指向"嵌套副本"** 的行一并删（只有侧车记着我加过的才删）。

    返回 `(removed_by_kind, index, added_paths)`；顺手把删掉的从侧车摘掉。
    """
    index = _read_autoscan_index(table_dir)
    added = index.get('added') or {}
    removed_by_kind = {}
    nested_pref = walk.nested_prefixes() if walk is not None else set()
    for kind_key in _ASSET_KINDS:
        rows = ((getattr(registry, kind_key, {}) or {})
                if registry is not None else {})
        if not rows:
            continue
        # ② 工具加过、却指向嵌套副本的行（判据见 `_junk_nested_paths`）
        junk_nested = _junk_nested_paths(rows, added.get(kind_key),
                                         nested_pref)
        if junk_nested:
            n = delete_rows_from_table(table_dir, kind_key, junk_nested,
                                       backup=True)
            if n:
                removed_by_kind[kind_key] = removed_by_kind.get(kind_key, 0) + n
                dead = set(junk_nested)
                added[kind_key] = [p for p in (added.get(kind_key) or [])
                                   if p not in dead]
        gone = _missing_paths(rows, set(junk_nested), kind_key, walk, root)
        if not gone:
            continue
        n = delete_rows_from_table(table_dir, kind_key, gone, backup=True)
        if n:
            removed_by_kind[kind_key] = removed_by_kind.get(kind_key, 0) + n
        if added.get(kind_key):
            dead = set(gone)
            added[kind_key] = [p for p in added[kind_key] if p not in dead]
    index['added'] = added
    return removed_by_kind, index, added


def _sync_relink_stage(root, table_dir, registry, walk):
    """
    ❷ **改指**：表里的行指向**嵌套副本**，而同一个资源在本项目 `game/` 里也有
    一份 → 把 `Path` 改指到本项目里的那一份。

    只有当 `walk` 认得嵌套前缀时才做（否则无从判断"副本"）。
    ⚠ 判据用**路径尾巴**、不能用 ID（表里老行的 ID 是 `_base` 这种，扫出来是
    `base`，对不上 —— 实测）。
    """
    relinked_by_kind = {}
    game_dir = root / 'game'
    if walk is None or not walk.nested_prefixes():
        return relinked_by_kind
    for kind_key in _ASSET_KINDS:
        table_rows = ((getattr(registry, kind_key, {}) or {})
                      if registry is not None else {})
        if not table_rows:
            continue
        updates = {}
        for aid, row in table_rows.items():
            rel = str((row or {}).get('Path') or '').strip().replace('\\', '/')
            if not rel or not walk.is_nested(rel):
                continue
            parts = [p for p in rel.split('/') if p not in ('', '.')]
            cand = None
            for i in range(1, len(parts)):       # 逐段剥前缀，找 `game/…`
                probe = '/'.join(parts[i:])
                if probe.lower().startswith('game/'):
                    cand = probe
                    break
            if not cand:
                continue
            new_real = walk.match(cand)
            if new_real is None:
                continue                         # 本项目里没有这一份：不动
            try:
                Path(new_real).relative_to(game_dir)
            except ValueError:
                continue
            updates[str(aid)] = cand
        if updates:
            n = update_table_paths(table_dir, kind_key, updates, backup=True)
            if n:
                relinked_by_kind[kind_key] = n
    return relinked_by_kind


def _sync_dedupe_stage(table_dir, registry):
    """
    ❸ **同一文件只留一行**：改指之后"原本指副本的那行"与"原本指本项目的那行"
    会指向同一个文件 → 必须合并，否则"一一对应"反过来被破坏（实测改指 1386 行
    会产生 ~1390 行重复）。占位行不动（它没有文件）。
    """
    deduped_by_kind = {}
    for kind_key in _ASSET_KINDS:
        rows = ((getattr(registry, kind_key, {}) or {})
                if registry is not None else {})
        if not rows:
            continue
        seen = {}
        drop_ids = []
        for aid, row in rows.items():
            if is_placeholder_row(row):
                continue                        # 占位行不动（它没有文件）
            key = norm_asset_path((row or {}).get('Path'))
            if not key:
                continue
            prev = seen.get(key)
            if prev is None:
                seen[key] = (aid, row)
                continue
            keep, drop = _prefer_row(prev, (aid, row))
            seen[key] = keep
            drop_ids.append(str(drop[0]))
        if not drop_ids:
            continue
        n = delete_rows_by_id(table_dir, kind_key, drop_ids, backup=True)
        if n:
            deduped_by_kind[kind_key] = n
    return deduped_by_kind


def _sync_append_stage(root, table_dir, assets, unreg, diff, walk, added):
    """
    ❹ **补登**：磁盘上有、表里没有的 → 追加（`append_rows_to_table`），并记进
    侧车（以后归工具管）。返回 `(added_by_kind, backup_names)`。

    ⚠ **按解析后的真实文件判重**（不是按字符串）：表里可能写
    `music/night.ogg`，扫出来是 `game/music/night.ogg` —— 字符串不等但是
    同一个文件；只比字符串会给它写第二行（实测）。
    """
    from storygraph.story.excel_export import resolve_thumb_source

    rows_by_kind = {
        'images': assets.get('images') or [],
        'music': (assets.get('audio') or {}).get('music') or [],
        'sounds': (assets.get('audio') or {}).get('sound') or [],
        'voices': assets.get('voices') or [],
    }
    have_resolved = (diff or {}).get('table_resolved') or {}
    added_by_kind = {}
    backups = []
    for kind_key, missing_paths in unreg.items():
        want = {norm_asset_path(p) for p in (missing_paths or []) if p}
        rows = [r for r in rows_by_kind.get(kind_key) or []
                if norm_asset_path(r.get('Path')) in want]
        if not rows:
            continue
        already = set(have_resolved.get(kind_key) or ())
        keep = []
        for r in rows:
            if walk is not None:
                real = walk.match(r.get('Path'))
            elif kind_key != 'images':
                real = _resolve_audio_source(r.get('Path'), root)
            else:
                real = resolve_thumb_source(r.get('Path'), root)
            if real is not None and str(Path(real).resolve()) in already:
                continue                         # 这个文件已经在表里了
            keep.append(r)
        if not keep:
            continue
        n = append_rows_to_table(table_dir, kind_key, keep,
                                 skip_paths=set(), backup=True)
        if n:
            added_by_kind[kind_key] = n
            added.setdefault(kind_key, [])
            for r in keep:
                rp = str(r.get('Path') or '')
                if rp and rp not in added[kind_key]:
                    added[kind_key].append(rp)   # 记进侧车：以后归我管
            bak = table_dir / (Path(_STANDARD_TABLES[kind_key]).stem
                               + '.bak.xlsx')
            if bak.is_file():
                backups.append(bak.name)
    return added_by_kind, backups


def _sync_report(*, table_dir, registry, diff, repaired, repair_error,
                 removed_by_kind=None, relinked_by_kind=None,
                 deduped_by_kind=None, added_by_kind=None, backup: str = ''):
    """带"删/改指/合并/补登"四项统计的返回体（原先两处各写一遍）。"""
    removed_by_kind = removed_by_kind or {}
    relinked_by_kind = relinked_by_kind or {}
    deduped_by_kind = deduped_by_kind or {}
    added_by_kind = added_by_kind or {}
    return {'ok': True, 'created': '', 'table_dir': str(table_dir),
            'added': sum(added_by_kind.values()), 'by_kind': added_by_kind,
            'removed': sum(removed_by_kind.values()),
            'removed_by_kind': removed_by_kind,
            'relinked': sum(relinked_by_kind.values()),
            'relinked_by_kind': relinked_by_kind,
            'deduped': sum(deduped_by_kind.values()),
            'deduped_by_kind': deduped_by_kind,
            'backup': backup,
            'unusable': _unusable_summary(registry),
            'scanned': (diff or {}).get('disk') or {},
            'reconcile': diff or {}, 'repaired': repaired,
            'repair_error': repair_error, 'registry': registry}


def sync_asset_tables(project_root, registry=None, asset_dir=None,
                      auto_append: bool = True, walk=None) -> dict:
    """
    **打开项目时的表 ↔ 磁盘对齐**（用户 2026-10-05 的目标）：

    > 每次打开项目文件，若没有表，建立表，若表已经存在，自动更新表中的资源，
    > 确保表与资源自动一一对应。

    行为（**只增不改**，写前备份）：

      ① 没有表 → 按项目内容**新建**（`ensure_asset_tables`）；
      ② 已有表 → 把"磁盘上有、表里没有"的图片/音乐/音效/语音**自动追加**
         （`append_rows_to_table(skip_paths=…, backup=True)`）—— 按 **Path**
         判重，所以是"一个文件一行"；**已存在的行一个字段都不动**
         （`Category` / `Notes` / `Tag` 里人改过的值必须活着）。

    返回：

        {'ok', 'created', 'table_dir', 'added', 'by_kind', 'backup',
         'scanned', 'reconcile'}

    ⚠ **不做的事**（都要用户明确动作）：删行（表里有、磁盘上没了）、
    改已有行的字段、把指向别的目录（另一份副本）的行搬过来 ——
    那些会动到用户整理过的数据。
    """
    root = Path(project_root)
    # ⚠ 本地 import 必须放在**函数最前面**：后面几处（删行判存、判重）
    # 都要用它，而 Python 会把"函数里任何位置 import 的名字"当成本地变量
    # —— 放在下面就会 `UnboundLocalError`（实测踩到）。
    from storygraph.story.excel_export import resolve_thumb_source
    made = ensure_asset_tables(root, asset_dir, walk=walk,
                               parse_check=registry is None)
    repaired = made.get('repaired') or []
    repair_error = made.get('repair_error') or ''
    if not made.get('ok'):
        return {'ok': False, 'created': '', 'table_dir': '', 'added': 0,
                'by_kind': {}, 'backup': '', 'error': made.get('error'),
                'repaired': repaired, 'repair_error': repair_error,
                'scanned': {}, 'reconcile': {}}
    table_dir = Path(made['path']) if made.get('path') else None
    if table_dir is None:
        return {'ok': False, 'created': '', 'table_dir': '', 'added': 0,
                'by_kind': {}, 'backup': '',
                'error': '找不到资源表目录', 'repaired': repaired,
                'repair_error': repair_error, 'scanned': {}, 'reconcile': {}}
    if made.get('created'):
        # 刚按项目内容建好（含"坏表挪走之后重建"）→ 表与磁盘本来就一致
        return {'ok': True, 'created': made['created'],
                'table_dir': str(table_dir), 'added': 0, 'by_kind': {},
                'backup': '', 'scanned': made.get('counts') or {},
                'reconcile': {}, 'repaired': repaired,
                'repair_error': repair_error,
                'registry': made.get('registry')}

    # 已经解析过一遍（`ensure_asset_tables` 读得动时会顺手带回来）——
    # 别再解析一次：真实项目 2890 行，一次解析不便宜。
    if registry is None and made.get('registry') is not None:
        registry = made.get('registry')

    if registry is None:
        try:
            registry = parse_asset_tables(
                table_dir, hooks=load_hooks(project_root=root),
                project_root=root)
        except Exception as e:                  # noqa: BLE001 - 读不动就当没漂移
            swallow('assets.asset_registry.sync_asset_tables.parse', e)
            registry = None
    # ⚠ **磁盘只扫一次**，对账和补登共用（原先各扫一次：真实项目 2 × 0.2 秒
    # 是小事，但它们各自还会再走一遍索引里的 5854 个文件）。
    assets = scan_assets(root, include_ui=True, walk=walk)
    diff = (compare_registry_to_disk(root, registry, walk=walk, assets=assets)
            if registry is not None else {})
    unreg = (diff or {}).get('unregistered') or {}
    # —— ❶ **自动删**：文件没了的行，跟着走 ——
    #
    # 用户 2026-10-05（看到"表里有 11 行指向的文件已经不在 → 已标为不可用"之后）：
    #
    # > 已经不在那这个东西实际上不需要啦！你自己自动删改表即可！
    # > 之前就说表跟资源一一对应！如果不可用直接删了就行！
    #
    # 所以现在**不再区分"谁加的行"**：只要 `Path` 解析不到真实文件就删。
    # 两个例外：
    #   · **人工占位**（`Notes`/`Doc` 写「占位」/placeholder/…）留着 —— 那是
    #     用户明说"这个还没有出图"，见上一轮那条"这个允许"；
    #   · 删之前一定备份 `*.bak.xlsx`；删不掉（例如表在 Excel 里锁着）就
    #     退回"标为不可用"，并在结果里报出来（`unusable`）。
    # —— ❶ **自动删**：文件没了的行，跟着走 ——
    #
    # 用户 2026-10-05（看到"表里有 11 行指向的文件已经不在 → 已标为不可用"之后）：
    #
    # > 已经不在那这个东西实际上不需要啦！你自己自动删改表即可！
    # > 之前就说表跟资源一一对应！如果不可用直接删了就行！
    #
    # 两个例外（人工占位留着；工具加过又指向嵌套副本的删掉）与备份规矩
    # 都在 `_sync_prune_stage` 里 —— 这里只负责编排（M4 重构）。
    removed_by_kind, index, added_paths = _sync_prune_stage(
        root, table_dir, registry, walk)
    if removed_by_kind:
        _write_autoscan_index(table_dir, index)
        # 删完要重新读表（内存里的注册表也过期了）
        _r, _d, _u = _reload_tables_and_diff(table_dir, root, walk, assets,
                                             'reload')
        if _r is not None:
            registry, diff, unreg = _r, _d, _u

    # —— ❷ **改指** + ❸ **合并重复** ——
    #
    # （为什么现在才敢自动改指：上一版试过，结果跟"自动补登"**打架** ——
    #  ping-pong：改指之后副本文件变成"未登记"→ 补登又加回来，未登记
    #  239 → 1644。现在 `scan_assets` 已经把嵌套副本**排除在项目资源之外**，
    #  两条规则终于共用同一个"什么算项目资源"的定义。）
    #
    # 两步都只在 `walk` 认得嵌套前缀时才做；判据与实现见各自的阶段函数
    # （M4 重构：只为拆复杂度，行为一字不改）。
    relinked_by_kind = _sync_relink_stage(root, table_dir, registry, walk)
    if relinked_by_kind:
        _r, _d, _u = _reload_tables_and_diff(table_dir, root, walk, assets,
                                            'relink')
        if _r is not None:
            registry, diff, unreg = _r, _d, _u
    if walk is not None and walk.nested_prefixes():
        deduped_by_kind = _sync_dedupe_stage(table_dir, registry)
        if deduped_by_kind:
            _r, _d, _u = _reload_tables_and_diff(table_dir, root, walk, assets,
                                                 'dedupe')
            if _r is not None:
                registry, diff, unreg = _r, _d, _u
    else:
        deduped_by_kind = {}

    if not auto_append or not unreg:
        baks = sorted({p.name for p in table_dir.glob('*.bak.xlsx')}
                      & {Path(_STANDARD_TABLES[k]).stem + '.bak.xlsx'
                         for k in removed_by_kind})
        return _sync_report(table_dir=table_dir, registry=registry,
                            diff=diff, repaired=repaired,
                            repair_error=repair_error,
                            removed_by_kind=removed_by_kind,
                            relinked_by_kind=relinked_by_kind,
                            deduped_by_kind=deduped_by_kind,
                            backup='、'.join(baks))

    # （`assets` 已经在上面扫过一次了，这里直接用 —— 别再扫第二遍。）
    # ❹ **补登**：磁盘上有、表里没有的 → 追加 + 记侧车。判重按**解析后的
    # 真实文件**（不是按字符串）—— 细节都在 `_sync_append_stage`（M4 重构）。
    added_by_kind, backups = _sync_append_stage(
        root, table_dir, assets, unreg, diff, walk, added_paths)
    if added_by_kind:
        index['added'] = added_paths
        _write_autoscan_index(table_dir, index)
    return _sync_report(table_dir=table_dir, registry=registry, diff=diff,
                        repaired=repaired, repair_error=repair_error,
                        removed_by_kind=removed_by_kind,
                        relinked_by_kind=relinked_by_kind,
                        deduped_by_kind=deduped_by_kind,
                        added_by_kind=added_by_kind,
                        backup='、'.join(sorted(set(backups))))


def _unusable_summary(registry) -> dict:
    """"表里有、文件没了"的条数（按类型）—— 给日志/界面看。"""
    out = {}
    for kind, attr in (('images', 'images'), ('music', 'music'),
                       ('sounds', 'sounds'), ('voices', 'voices')):
        n = len((getattr(registry, 'unusable', {}) or {}).get(attr) or {})
        if n:
            out[kind] = n
    return out


def update_table_paths(asset_dir, table_key: str, updates: dict,
                       backup: bool = True) -> int:
    """
    按 **ID → 新 Path** 改一行里的 `Path`（只改这一格），返回改了几行。

    为什么需要它（2026-10-05 实测）：用户的表里有 1386 行指向**嵌套副本**
    `sr_general/**`，而同一个资源在 `game/` 里**也有一份**。那种行会让
    ①"磁盘上这份文件没人登记"（补登记时 ID 撞车、被静默跳过），
    ②导出的引用写到 `sr_general/game/…`（Ren'Py 找不到）。
    把 `Path` **改指**到本项目 `game/` 里的同名文件是最小、最安全的修法：
    不删行、不动其它字段、不产生重复候选，写前照旧 `*.bak.xlsx`。
    """
    updates = {str(k): str(v) for k, v in (updates or {}).items() if k and v}
    if not updates:
        return 0
    fname = _STANDARD_TABLES.get(str(table_key or ''))
    if not fname:
        return 0
    path = Path(asset_dir) / fname
    if not path.is_file():
        return 0
    changed = 0
    try:
        wb = openpyxl.load_workbook(path)
        ws = wb.active
        headers = _sheet_headers(ws)
        if 'ID' not in headers or 'Path' not in headers:
            return 0
        id_col = headers.index('ID') + 1
        p_col = headers.index('Path') + 1
        from storygraph.excel.cells import set_text
        for r in range(2, ws.max_row + 1):
            aid = ws.cell(row=r, column=id_col).value
            if aid is None:
                continue
            new = updates.get(str(aid))
            if not new:
                continue
            if str(ws.cell(row=r, column=p_col).value or '') == new:
                continue
            set_text(ws, r, p_col, new)
            changed += 1
        if changed:
            if backup:
                _backup_table(path)
            wb.save(path)
    except Exception as e:                      # noqa: BLE001 - 改不动就当没改
        swallow('assets.asset_registry.update_table_paths', e)
        return 0
    return changed


def delete_rows_from_table(asset_dir, table_key: str, paths,
                           backup: bool = True) -> int:
    """
    从资源表里**删掉 Path 命中的行**，返回删掉的行数（写前默认备份）。

    只给"自动跟随现实"用：工具自己加进去的行，文件被删掉之后就跟着走
    （用户 2026-10-05："我认为表应当实时反应实际资源的情况，自动更新和增删"）。
    **人工录入的行绝不走这条**（由侧车 `_autoscan.json` 判归属，见
    `sync_asset_tables`）—— 占位行更是要留着。
    """
    keys = {norm_asset_path(p) for p in (paths or ()) if p}
    if not keys:
        return 0
    fname = _STANDARD_TABLES.get(str(table_key or ''))
    if not fname:
        return 0
    path = Path(asset_dir) / fname
    if not path.is_file():
        return 0
    removed = 0
    try:
        wb = openpyxl.load_workbook(path)
        ws = wb.active
        headers = _sheet_headers(ws)
        if 'Path' not in headers:
            return 0
        pcol = headers.index('Path') + 1
        for r in range(ws.max_row, 1, -1):       # 倒着删，行号不飘
            v = ws.cell(row=r, column=pcol).value
            if v and norm_asset_path(v) in keys:
                ws.delete_rows(r, 1)
                removed += 1
        if removed:
            if backup:
                _backup_table(path)
            wb.save(path)
    except Exception as e:                      # noqa: BLE001
        swallow('assets.asset_registry.delete_rows_from_table', e)
        return 0
    return removed


def delete_rows_by_id(asset_dir, table_key: str, ids, backup: bool = True) -> int:
    """
    按 **ID** 删行（`delete_rows_from_table` 是按 `Path` 删，删不掉"同一文件两行"）。

    为什么需要（2026-10-05 实测）：表里原本一行指 `game/images/x.png`、
    另一行指 `sr_general/game/images/x.png`（同一份资源的两个副本）。
    把它们**改指**到同一个文件之后，两行的 `Path` 就一样了 —— 这时按
    `Path` 删会把两行一起删掉 ✗，必须按 ID 精确删一行。
    """
    keys = {str(i) for i in (ids or ()) if i}
    if not keys:
        return 0
    fname = _STANDARD_TABLES.get(str(table_key or ''))
    if not fname:
        return 0
    path = Path(asset_dir) / fname
    if not path.is_file():
        return 0
    removed = 0
    try:
        wb = openpyxl.load_workbook(path)
        ws = wb.active
        headers = _sheet_headers(ws)
        if 'ID' not in headers:
            return 0
        id_col = headers.index('ID') + 1
        for r in range(ws.max_row, 1, -1):
            v = ws.cell(row=r, column=id_col).value
            if v is not None and str(v) in keys:
                ws.delete_rows(r, 1)
                removed += 1
        if removed:
            if backup:
                _backup_table(path)
            wb.save(path)
    except Exception as e:                      # noqa: BLE001
        swallow('assets.asset_registry.delete_rows_by_id', e)
        return 0
    return removed


def _prefer_row(a: tuple, b: tuple) -> tuple:
    """
    "同一文件两行"留哪一行：`(keep, drop)`。

    规则（确定性，先看谁更有信息）：
      1. **带 Tag 的**优先（立绘/背景那类行能直接参与 `show` 解析）；
      2. 再看 `Attributes` 非空；
      3. 最后按 ID 字典序 —— 稳定，不随表里行序变化。
    """
    (aid_a, row_a), (aid_b, row_b) = a, b

    def score(item):
        _aid, row = item
        row = row or {}
        return (1 if str(row.get('Tag') or '').strip() else 0,
                1 if str(row.get('Attributes') or '').strip() else 0)

    sa, sb = score(a), score(b)
    if sa != sb:
        return (a, b) if sa > sb else (b, a)
    return (a, b) if str(aid_a) <= str(aid_b) else (b, a)


def _autoscan_index_path(table_dir) -> Path:
    """侧车文件：记录**工具自己加过哪些行**（用于安全地自动删）。"""
    return Path(table_dir) / '_autoscan.json'


def _read_autoscan_index(table_dir) -> dict:
    """读侧车（坏了/没有 → 空索引 = 一切按人工处理，保守）。"""
    p = _autoscan_index_path(table_dir)
    try:
        if p.is_file():
            import json
            data = json.loads(p.read_text(encoding='utf-8'))
            if isinstance(data, dict) and isinstance(data.get('added'), dict):
                return data
    except Exception as e:                      # noqa: BLE001
        swallow('assets.asset_registry._read_autoscan_index', e)
    return {'version': 1, 'added': {}}


def _write_autoscan_index(table_dir, index: dict) -> None:
    p = _autoscan_index_path(table_dir)
    try:
        import json
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(index, ensure_ascii=False, indent=1),
                     encoding='utf-8')
    except Exception as e:                      # noqa: BLE001 - 侧车写不了就退回保守
        swallow('assets.asset_registry._write_autoscan_index', e)


def _write_script_tables(project_root, target) -> dict:
    """
    把**脚本里声明的**角色 / 变换也写进表（返回条数）。

    为什么必须补这一步：`scan_assets` 只能扫**文件**（图片/音频），
    而 `define e = Character("爱莲")`、`transform i11:` 这些**只存在于 .rpy 里**
    —— 真实项目里"变换 146"就是全靠它。只扫文件建出来的表，
    位置候选（`i11`）和说话人候选都会是空的。

    ⚠ 这里**函数内 import `story.*`**：`story.script_table` 在模块级 import 了
    `assets`，模块级反向 import 会成环（`assets → story → assets`）。
    """
    out = {'characters': 0, 'transforms': 0}
    try:
        from storygraph.parsing.discover import discover_scripts
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.story.script_table import registry_from_parse_result

        scripts = discover_scripts(project_root)
        if not scripts:
            return out
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=scripts, graph=None)
        reg = registry_from_parse_result(pr)
        chars = getattr(reg, 'characters', {}) or {}
        trans = getattr(reg, 'transforms', {}) or {}
        # ⚠ **已存在的表只追加，绝不整份重写**（2026-10-05 实测踩到）：
        # 重建 images 时如果顺手 `write_dict_to_excel(characters.xlsx, …)`，
        # 会把用户手工维护的角色表**整张覆盖**（实测：手写的 `hand_made`
        # 那行没了）。所以文件已在 → append-only（按 ID 跳过已有行）；
        # 文件不在 → 才新建。
        target_dir = Path(target)
        if chars:
            if (target_dir / 'characters.xlsx').is_file():
                append_rows_to_table(target_dir, 'characters',
                                     list(chars.values()))
            else:
                write_dict_to_excel(target_dir / 'characters.xlsx', chars,
                                    _TABLE_FIELDS['character'])
            out['characters'] = len(chars)
        if trans:
            if (target_dir / 'transforms.xlsx').is_file():
                append_rows_to_table(target_dir, 'transforms',
                                     list(trans.values()))
            else:
                write_dict_to_excel(target_dir / 'transforms.xlsx', trans,
                                    _TABLE_FIELDS['transform'])
            out['transforms'] = len(trans)
    except Exception as e:                      # noqa: BLE001 - 脚本解析失败也别挡住建表
        swallow('assets.asset_registry._write_script_tables', e)
    return out


def ensure_asset_tables(project_root, asset_dir=None, include_ui=True,
                        with_scripts: bool = True, walk=None,
                        parse_check: bool = True) -> dict:
    """
    **没有资源表就现场建一张**（扫描项目内容 → 写出 `*.xlsx`）。

    返回 `{ok, created, path, counts, error}`；`created` 非空 = 这次真的建了。

    用户 2026-10-05 实测报：

    > 我拿另一个复制看了一下测出 bug 了，首次加载导入时项目无法读到资源表，
    > 应当立即创建对应的资源表

    实测现场：复制出来的项目里 `game/asset_tables/` 是个**空目录**
    （xlsx 没跟过去）→ 卡片编辑器「打开项目」只报一句
    `读不到资源表：这个目录下找不到资源表（xlsx）（下拉候选会是空的）`
    → 立绘/音乐候选全空、缩略图全空，用户只能自己去建表（还得先知道表长什么样）。

    ⚠ 而"自动扫一份表"这件事**代码里早就有** —— 只是长在 `AssetEngine`
    的 `_ensure_asset_tables_exist()` 上，而那个引擎在当前 Qt 应用里
    **根本没有被构造**（`AssetEngine(` 在 `storygraph/ui_qt` 里一次都没出现）。
    于是"有需求、有实现、但没人调用"。这次把它提到 assets 层做**唯一真源**，
    编辑器这条路直接用它，引擎那边也改调它。

    `with_scripts`：是否把**脚本里声明的**角色 / 变换也写进表（`define x =
    Character(...)` / `transform i11:` —— 扫文件扫不出来）。
    默认 `True`（一张完整的表才有用）；**`AssetEngine` 会传 False** ——
    它自己有一套 `_sync_character_definitions`（还会打 `_from_script` 标记），
    表里先有 `characters.xlsx` 会让它走"表派"那条路、标记就没了
    （实测 `test_transforms_and_preview.py::…test_engine_writes_rich_character_row`）。
    """
    root = Path(project_root)
    try:
        found = find_asset_tables(root, asset_dir)
        if found is not None:
            # **有表 ≠ 读得出来**。2026-10-05 用户实测报：
            #
            #   [项目] 读不到资源表：这个目录下找不到资源表（xlsx），
            #          自动新建也失败了：未知原因
            #
            # 真相是表**在**、但解析抛异常（Excel 开着锁住 / 文件写坏），
            # 而这条路只说"找不到 + 未知原因" —— 既误导又没有出路。
            # 现在：先试解析；读得动就直接把注册表带回去（顺便省一次解析）；
            # 读不动就把坏文件挪到一边（`*.broken-<时间戳>.xlsx`，**不删**）
            # 再按项目内容重建 —— 能读的其它表（比如 `characters.xlsx`）不动。
            parse_error = ''
            registry_hint = None
            try:
                if parse_check:
                    reg = parse_asset_tables(
                        found, hooks=load_hooks(project_root=root),
                        project_root=root, walk=walk)
                else:
                    reg = registry_hint
                return {'ok': True, 'created': '', 'path': str(found),
                        'counts': {}, 'error': '', 'registry': reg,
                        'repaired': [], 'repair_error': ''}
            except Exception as e:              # noqa: BLE001 - 读不动 → 重建
                parse_error = f'{type(e).__name__}: {e}'
                swallow('assets.asset_registry.ensure_asset_tables.parse', e)
            repaired = _quarantine_broken_tables(found, parse_error)
            target = Path(found)
            assets = scan_assets(root, include_ui=include_ui, walk=walk)
            write_asset_excel(assets, target, split_by_category=False)
            extra = (_write_script_tables(root, target) if with_scripts
                     else {'characters': 0, 'transforms': 0})
            counts = {
                'images': len(assets.get('images') or []),
                'music': len((assets.get('audio') or {}).get('music') or []),
                'sounds': len((assets.get('audio') or {}).get('sound') or []),
                'voices': len(assets.get('voices') or []),
                'characters': extra.get('characters', 0),
                'transforms': extra.get('transforms', 0),
            }
            return {'ok': True, 'created': str(target), 'path': str(target),
                    'counts': counts, 'error': '', 'registry': None,
                    'repaired': repaired, 'repair_error': parse_error}
        if asset_dir:
            target = Path(asset_dir)
        elif (root / 'game').is_dir():
            # 项目自己的约定（真实项目就是 `game/asset_tables/`；UI 写表也写这儿）
            target = root / 'game' / 'asset_tables'
        else:
            target = root / 'asset_tables'
        assets = scan_assets(root, include_ui=include_ui)
        write_asset_excel(assets, target, split_by_category=False)
        # 角色 / 变换只能从**脚本声明**里来（`scan_assets` 扫的是文件）
        extra = (_write_script_tables(root, target) if with_scripts
                 else {'characters': 0, 'transforms': 0})
        counts = {
            'images': len(assets.get('images') or []),
            'music': len((assets.get('audio') or {}).get('music') or []),
            'sounds': len((assets.get('audio') or {}).get('sound') or []),
            'voices': len(assets.get('voices') or []),
            'characters': extra.get('characters', 0),
            'transforms': extra.get('transforms', 0),
        }
        return {'ok': True, 'created': str(target), 'path': str(target),
                'counts': counts, 'error': '', 'registry': None,
                'repaired': [], 'repair_error': ''}
    except Exception as e:                      # noqa: BLE001 - 建表失败不该让界面崩
        swallow('assets.asset_registry.ensure_asset_tables', e)
        return {'ok': False, 'created': '', 'path': '', 'counts': {},
                'error': f'{type(e).__name__}: {e}', 'registry': None,
                'repaired': [], 'repair_error': ''}


def _merge_existing(existing: dict, asset: dict, auto_fields: list) -> dict:
    """若资产已在 Excel 中存在，保留用户编辑字段，仅覆盖自动字段。"""
    aid = asset['ID']
    if aid in existing:
        merged = existing[aid].copy()
        for key in auto_fields:
            merged[key] = asset.get(key, '')
        return merged
    return asset


def write_asset_excel(assets_dict: dict, output_dir, split_by_category=False):
    """将 scan_assets 的结果写入 Excel（支持增量更新）。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 图片 ----------
    image_assets = assets_dict['images']
    if split_by_category:
        groups = defaultdict(list)
        for a in image_assets:
            groups[a['Category']].append(a)
        for cat, assets in groups.items():
            file_path = output_dir / f'images_{cat}.xlsx'
            existing = {r['ID']: r for r in read_excel_rows(file_path) if r.get('ID')}
            new_data = {}
            for a in assets:
                new_data[a['ID']] = _merge_existing(
                    existing, a, ['Path', 'Tag', 'Attributes', 'Category'])
            write_dict_to_excel(
                file_path, new_data,
                ['ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes'])
    else:
        file_path = output_dir / 'images.xlsx'
        existing = {r['ID']: r for r in read_excel_rows(file_path) if r.get('ID')}
        new_data = {}
        for a in image_assets:
            new_data[a['ID']] = _merge_existing(
                existing, a, ['Path', 'Tag', 'Attributes', 'Category'])
        write_dict_to_excel(
            file_path, new_data,
            ['ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes'])

    # ---------- 音乐 ----------
    music_file = output_dir / 'music.xlsx'
    existing = {r['ID']: r for r in read_excel_rows(music_file) if r.get('ID')}
    new_data = {}
    for a in assets_dict['audio']['music']:
        new_data[a['ID']] = _merge_existing(existing, a, ['Path', 'Loop', 'Volume'])
    write_dict_to_excel(
        music_file, new_data,
        ['ID', 'Path', 'Loop', 'Volume', 'Notes'])

    # ---------- 音效 ----------
    sound_file = output_dir / 'sounds.xlsx'
    existing = {r['ID']: r for r in read_excel_rows(sound_file) if r.get('ID')}
    new_data = {}
    for a in assets_dict['audio']['sound']:
        new_data[a['ID']] = _merge_existing(existing, a, ['Path'])
    write_dict_to_excel(
        sound_file, new_data,
        ['ID', 'Path', 'Notes'])

    # ---------- 语音 ----------
    voice_file = output_dir / 'voices.xlsx'
    existing = {r['ID']: r for r in read_excel_rows(voice_file) if r.get('ID')}
    new_data = {}
    for a in assets_dict.get('voices', []):
        new_data[a['ID']] = _merge_existing(existing, a, ['Path'])
    write_dict_to_excel(
        voice_file, new_data,
        ['ID', 'Path', 'Notes'])


# ===================== 钩子加载 =====================

def load_hooks(config_path=None, project_root=None):
    """
    加载自定义钩子模块。

    config_path=None 时按顺序搜索：
      1. project_root / 'asset_import_hooks.py'（推荐）
      2. CWD / 'asset_import_hooks.py'（兼容旧行为）
    均不存在则返回空字典。

    支持两个钩子：
      - pre_process_row(table_name, row) -> row
      - post_process_registry(registry) -> None
    """
    candidates = []
    if config_path is not None:
        candidates.append(Path(config_path))
    else:
        if project_root is not None:
            candidates.append(Path(project_root) / 'asset_import_hooks.py')
        candidates.append(Path('asset_import_hooks.py'))

    target = None
    for c in candidates:
        if c.exists():
            target = c
            break

    if target is None:
        return {}

    try:
        spec = importlib.util.spec_from_file_location(
            'asset_import_hooks', str(target)
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return {
            'pre_process_row': getattr(module, 'pre_process_row', None),
            'post_process_registry': getattr(module, 'post_process_registry', None),
        }
    except Exception as e:
        print(f"[asset_registry] 加载钩子失败: {e}")
        return {}


# ===================== Excel 解析 =====================

# 标准表定义：(registry 属性名, 文件名)
_STANDARD_TABLES = {
    'characters': 'characters.xlsx',
    'images': 'images.xlsx',
    'transforms': 'transforms.xlsx',
    'music': 'music.xlsx',
    'sounds': 'sounds.xlsx',
    'voices': 'voices.xlsx',
}

# 资源表写入时的字段顺序（用于"新建资产"追加一行）
_TABLE_FIELDS = {
    'character': ['ID', 'Name', 'Color', 'ImageTag', 'Notes'],
    'image': ['ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes'],
    # transform 增加 ATL/Params/Doc/Category：支持自定义变换（文档第四节），
    # 导出时按 ATL 生成 `transform id(params):` 代码块；Doc 是给文案看的
    # 一句话说明（表格下拉里选中它就显示这句）。
    'transform': ['ID', 'Path', 'ATL', 'Params', 'Doc', 'Category', 'Notes'],
    'music': ['ID', 'Path', 'Loop', 'Volume', 'Notes'],
    'sound': ['ID', 'Path', 'Notes'],
    'voice': ['ID', 'Path', 'Notes'],
}

# 资产类型 → 集合属性名：已收敛到 `storygraph.core.vocabulary.ASSET_TYPE_ATTR`
# （顶部以 `_TYPE_ATTR` 别名导入）。别再就地写第二份——2026-09-28 之前
# 这里、`validate._TYPE_ATTR`、`dsl.Symbols.has()` 内联 dict 各有一份。

def add_asset_to_registry(registry, asset_type, asset_id, path='',
                         asset_dir=None):
    """
    在注册表（与可选的资源表 .xlsx）里登记一个新资产。

    M6：从资产选择器"新建资产..."直接写回资源表，打通回写链路。
    返回是否成功；asset_type 非法或 asset_id 为空返回 False。
    """
    attr = _TYPE_ATTR.get(asset_type)
    if not attr or not asset_id:
        return False
    target = getattr(registry, attr)
    row = {'ID': asset_id, 'Path': path or ''}
    if asset_type == 'music':
        row['Loop'] = True
        row['Volume'] = 1.0
    if asset_type == 'transform':
        row['ATL'] = ''
        row['Params'] = ''
        row['Doc'] = ''
        row['Category'] = ''
    row['Notes'] = ''
    target[asset_id] = row

    if asset_dir is not None:
        # _STANDARD_TABLES 以 registry 属性名为键（如 'voices' 复数），
        # 而 asset_type 是单数（'voice'），必须用 attr 反查文件名，
        # 否则 voice 类型查不到文件名、静默跳过写回。
        fname = _STANDARD_TABLES.get(attr)
        if fname:
            p = Path(asset_dir) / fname
            try:
                if p.exists():
                    wb = openpyxl.load_workbook(p)
                    ws = wb.active
                    # 按文件真实表头对齐追加，避免列错位（老文件可能
                    # 只有 ID/Path/Notes，新文件才有 ATL/Params…）
                    headers = _sheet_headers(ws)
                    _asset_append_row(ws, [row.get(h, '') if h else '' for h in headers])
                else:
                    wb = openpyxl.Workbook()
                    ws = wb.active
                    fields = _TABLE_FIELDS[asset_type]
                    # ⚠ 新建表也要写**中文表头**（与 `write_dict_to_excel` 一致）：
                    # 从前这里写英文 `fields`，于是"哪条路径建的表"决定了表头
                    # 是中是英 —— 同一批表里两种表头，用户看着像坏了。
                    ws.append([ASSET_FIELD_LABELS.get(f, f) for f in fields])
                    _asset_append_row(ws, [row.get(h, '') for h in fields])
                wb.save(p)
            except Exception as e:
                print(f"[asset_registry] 写入资源表失败: {e}")
    return True


def _update_row_cells(ws, row_no: int, headers: list, row: dict,
                      keys) -> int:
    """
    更新指定行的若干列（只写**值确实不同**的单元格），返回改写的单元格数。

    单独抽出来是因为它只做"受控更新"：调用方给**列白名单**，
    白名单之外的列（人写的分类、备注…）一个都不碰。
    """
    changed = 0
    for key in keys:
        if key not in headers:
            continue
        col = headers.index(key) + 1
        new = row.get(key, '')
        cell = ws.cell(row=row_no, column=col)
        if (cell.value or '') != (new or ''):
            _asset_set_text(ws, row_no, col, new)
            changed += 1
    return changed


def append_rows_to_table(asset_dir, table_key: str, rows: list,
                         update_keys: tuple = (), skip_paths=None,
                         backup: bool = False) -> int:
    """
    把若干行写进资源表：**已存在的 ID 默认跳过**，返回写入的行数。

    这是"程序敲代码 → 表格跟着变"的落地动作：脚本里新写的 transform /
    角色 / image 声明，要真的出现在 transforms.xlsx / characters.xlsx 里，
    文案才看得见、选得到。真实项目里曾经一个 transform 都存不下来
    （只进了内存注册表，从不落盘），就是这个环节缺失。

    策略默认"只追加、不覆盖"：人填的说明/分类是权威，工具不该改它。

    `update_keys` 是唯一的例外：给定时，对**已存在的 ID** 也更新这几列。
    适用场景是"这几列以脚本为权威"——典型是 transform 的
    `ATL` / `Params` / `Doc`：脚本改了定义，表里必须跟着变，否则
    "程序改代码 → 表格自动反映"就是空话，而且被写坏的旧值会永久固化
    （2026-09-28 实测）。其它列仍然不动。

    `skip_paths`（2026-10-05 新增）：**再按 `Path` 判一次重**。资源表要的是
    "**一个文件一行**"（一一对应），而 ID 是文件名派生的 —— 同一个文件完全
    可能已经以另一个 ID 登记过（`RETURE.png` 与 `Frame 26_RETURE.png` 实测
    就有）。只按 ID 判重会把它再写一遍。

    `backup=True`：写之前把原表复制成 `*.bak.xlsx`（**默认关** —— 脚本同步
    那条路调用频繁，每次都备份太浪费；"打开项目自动对齐"那条路会打开它）。

    返回 = 新增行数 + 被改写的单元格数。
    """
    table_key = str(table_key or '')
    fname = _STANDARD_TABLES.get(table_key)
    if not fname or not rows:
        return 0
    asset_dir = Path(asset_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)
    path = asset_dir / fname
    added = updated = 0
    try:
        if path.exists():
            wb = openpyxl.load_workbook(path)
            ws = wb.active
            headers = _sheet_headers(ws)
            if not headers or all(h is None for h in headers):
                headers = list(rows[0].keys())
                ws.append(headers)
            row_of = {}
            id_col = headers.index('ID') + 1 if 'ID' in headers else 1
            for r in range(2, ws.max_row + 1):
                v = ws.cell(row=r, column=id_col).value
                if v:
                    row_of.setdefault(str(v), r)
            # 表里已经登记过的**路径**（`skip_paths` 之外再从表里现读一遍，
            # 免得调用方漏传）
            have_paths = set(skip_paths or ())
            if 'Path' in headers:
                pcol = headers.index('Path') + 1
                for r in range(2, ws.max_row + 1):
                    v = ws.cell(row=r, column=pcol).value
                    if v:
                        have_paths.add(norm_asset_path(v))
            for row in rows:
                aid = str(row.get('ID') or '')
                if not aid:
                    continue
                rp = norm_asset_path(row.get('Path'))
                if rp and rp in have_paths:
                    continue                    # 这个文件已经在表里了（一一对应）
                if aid in row_of:
                    # 已存在的行：默认跳过；给了 update_keys 才更新白名单列
                    if update_keys:
                        updated += _update_row_cells(
                            ws, row_of[aid], headers, row, update_keys)
                        continue
                    if not rp:
                        continue            # 没写 Path 的行：没有"另一个文件"
                    # ⚠ **ID 撞车 ≠ 同一个文件**（2026-10-05 实测）：
                    # `game/cc_preview/lan/angry.png` 生成的 ID 可能是
                    # `lan_angry`，而表里已有一行 `lan_angry` 指
                    # `game/images/characters/lan/angry.png`。老代码在这里
                    # **静默丢掉**这个文件 —— 于是它永远登记不上，体检里
                    # 永远显示"未登记 N 条"（用户看到的就是这个）。
                    # 同一个文件在前面已经按 Path 判重跳过了，走到这里
                    # 一定是**另一个文件** → 另起一个 ID 收下它。
                    base = aid
                    k = 2
                    while f'{base}_{k}' in row_of:
                        k += 1
                    aid = f'{base}_{k}'
                    row = dict(row, ID=aid)
                row_of[aid] = _asset_append_row(
                    ws, [row.get(h, '') if h else '' for h in headers])
                if rp:
                    have_paths.add(rp)
                added += 1
            if added or updated:
                if backup:
                    _backup_table(path)
                wb.save(path)
        else:
            wb = openpyxl.Workbook()
            ws = wb.active
            headers = list(rows[0].keys())
            ws.append(headers)
            seen = set(skip_paths or ())
            for row in rows:
                rp = norm_asset_path(row.get('Path'))
                if rp and rp in seen:
                    continue
                _asset_append_row(ws, [row.get(h, '') for h in headers])
                if rp:
                    seen.add(rp)
                added += 1
            wb.save(path)
    except Exception as e:                      # noqa: BLE001
        print(f"[asset_registry] 写入 {fname} 失败: {e}")
        return 0
    return added + updated


def norm_asset_path(value) -> str:
    """资源表里的 Path → 判重用的规范形式（斜杠统一、去首尾空白、小写）。"""
    s = str(value or '').strip().replace('\\', '/')
    while s.startswith('./'):
        s = s[2:]
    return s.lower()


#: 「这一行是**人工占位**」的标记词（写在 `Notes` 或 `Doc` 里，大小写不限）。
#:
#: 用户 2026-10-05：
#:
#> 表里有但实际没有的资源也不能用啊？？？（除非人工把它录入占位，这个允许）
#:
#: 所以缺失文件的行分两种：
#:   · **没有**占位标记 → 认定"资源被删了" → 该行**不可用**（不出现在候选、
#:     不给解析、编译时按缺资源处理）；工具自己加的行还会被自动移除；
#:   · **有**占位标记 → 用户明知文件还没有/不要了，留着占位 →
#:     仍然**可用**（能选、能写进卡片），只是编译时照旧按"缺资源"提示。
_PLACEHOLDER_MARKS = ('占位', 'placeholder', '待补', '计划', 'planned',
                      'todo', 'wip')


#: 兼容内部旧写法（本模块里以前叫 _norm_asset_path）。
_norm_asset_path = norm_asset_path


def is_placeholder_row(row) -> bool:
    """这一行是用户**手工录入的占位**吗（`Notes` / `Doc` 里有标记词）？"""
    row = row or {}
    text = ' '.join(str(row.get(k) or '') for k in ('Notes', 'Doc', '说明'))
    low = text.lower()
    return any(mark in low for mark in _PLACEHOLDER_MARKS)


def _row_file_exists(kind: str, row, project_root=None, walk=None) -> bool:
    """这一行的 `Path` 在磁盘上真的存在吗（`kind` 是 `image` / `music` …）？"""
    rel = str((row or {}).get('Path') or '').strip()
    if not rel:
        return False
    try:
        if walk is not None:
            return walk.match(rel) is not None
        if kind == 'image':
            from storygraph.story.excel_export import resolve_thumb_source
            return resolve_thumb_source(rel, project_root) is not None
        return _resolve_audio_source(rel, project_root) is not None
    except Exception as e:                      # noqa: BLE001 - 判不了就当存在
        swallow('assets.asset_registry._row_file_exists', e)
        return True


def _backup_table(path: Path) -> str:
    """把资源表备份成 `*.bak.xlsx`（返回备份路径；失败返回 ''）。"""
    try:
        import shutil
        bak = Path(path).with_suffix('.bak.xlsx')
        shutil.copyfile(path, bak)
        return str(bak)
    except Exception as e:                      # noqa: BLE001
        swallow('assets.asset_registry._backup_table', e)
        return ''


def parse_asset_tables(asset_dir, hooks=None, project_root=None,
                       walk=None) -> AssetRegistry:
    """
    解析资产表目录，构建 AssetRegistry。

    asset_dir:    存放 xlsx 的目录
    hooks:        load_hooks 的返回值
    project_root: 项目根目录；None 则默认 asset_dir.parent
    """
    asset_dir = Path(asset_dir)
    registry = AssetRegistry()
    if hooks is None:
        hooks = {}
    if project_root is None:
        project_root = asset_dir.parent

    pre_hook = hooks.get('pre_process_row')

    # ---------- 标准表 ----------
    for key, filename in _STANDARD_TABLES.items():
        file = asset_dir / filename
        if not file.exists():
            continue
        rows = read_excel_rows(file)
        for row in rows:
            if pre_hook is not None:
                try:
                    row = pre_hook(key, row)
                except Exception as e:
                    registry.warnings.append(
                        f"{filename}: pre_process_row 钩子异常 {e}")
            asset_id = row.get('ID')
            if not asset_id:
                registry.errors.append(f"{filename}: 缺少ID字段")
                continue
            target_dict = getattr(registry, key)
            if asset_id in target_dict:
                registry.errors.append(f"{filename}: ID '{asset_id}' 重复")
            target_dict[asset_id] = row
            custom_fields = {k: v for k, v in row.items()
                             if k.startswith('Custom_')}
            if custom_fields:
                row['_custom'] = custom_fields

    # ---------- 自定义表 ----------
    for file in asset_dir.glob('custom_*.xlsx'):
        table_name = file.stem.replace('custom_', '')
        rows = read_excel_rows(file)
        custom_dict = {}
        for row in rows:
            if 'ID' not in row:
                registry.warnings.append(f"{file.name}: 无ID列，跳过该行")
                continue
            custom_dict[row['ID']] = row
        registry.custom[table_name] = custom_dict

    # ---------- 后处理钩子 ----------
    post_hook = hooks.get('post_process_registry')
    if post_hook is not None:
        try:
            post_hook(registry)
        except Exception as e:
            registry.warnings.append(f"post_process_registry 钩子异常 {e}")

    validate_registry(registry, project_root, walk=walk)
    # 记住来路：资源图库要按 Path 去磁盘上取缩略图，得知道基准目录。
    registry.asset_dir = asset_dir
    registry.project_root = project_root
    return registry


# ===================== 校验 =====================

_COLOR_PATTERN = re.compile(r'^#(?:[0-9a-fA-F]{3}){1,2}$')


class FileWalkIndex:
    """
    **一次目录遍历**的产物：相对路径表 + 文件名表，用来 O(1) 匹配资源表里的 Path。

    为什么需要（2026-10-05 用户实测："给程序干未响应了！太卡了"）：

    真实项目 `sr_general` 量出来的数字：

      · 打开项目那条路原先要**走三遍**整棵树（图片一组、音频两组 rglob）；
      · 每走一遍 ~0.6 s，`scan_assets` 一共 **3.8 s**；
      · 对账 `compare_registry_to_disk` **12.4 s** —— 每一行都要
        `resolve_thumb_source`（逐个基准 `is_file()`）+ `Path.resolve()`
        （Windows 上 resolve 会摸文件系统），3123 行 + 3273 个磁盘文件
        加起来上万次系统调用；
      · 于是"打开项目"要几十秒，GUI 线程全程阻塞 → 未响应。

    有了它：走一遍树（0.6 s）建两张表，之后所有"这个相对路径在不在"
    都是**字典查找**，不碰磁盘。
    """

    def __init__(self, root):
        self.root = Path(root)
        self.by_rel = {}          # 'game/images/x.png'(小写) -> Path
        self.by_name = {}         # 'x.png' -> [相对路径, …]
        #: **嵌套项目**的前缀（小写、带尾 `/`）：`sr_general/game/…` 的 `sr_general/`
        self.nested = set()
        self._walk()
        self._find_nested_projects()

    def _find_nested_projects(self):
        """
        找出"自己带 `game/` 的子目录"= **另一份项目副本**。

        实测（用户 2026-10-05）：项目根下有个 `sr_general/`，里面是
        **同一份项目**的副本（表里 1386 行指向它）。这些文件若被当成
        "项目资源"扫进来，会以 `sr_general_angry` / `ling_angry` 这种
        **编出来的 ID** 写进表 —— 而 `ling_angry` 恰好就是
        "tag_attribute" 的写法，于是卡片里的 `ling_angry` 查到的是
        **那一行**（Tag 为空、图在 `cc_preview/` 里）→
        **立绘不显示、表情候选为空**。

        判据：路径里出现在**开头之外**的 `game/` 段（`sr_general/game/…`）。
        项目自己的 `game/` 永远在开头，不会被误判。
        """
        for rel in self.by_rel:
            parts = rel.split('/')
            for i, seg in enumerate(parts[1:], start=1):
                if seg == 'game':
                    self.nested.add('/'.join(parts[:i]) + '/')
                    break

    def nested_prefixes(self) -> set:
        """嵌套项目的前缀集合（扫描时整段跳过）。"""
        return self.nested

    def is_nested(self, rel_low: str) -> bool:
        """这一条相对路径是不是在**嵌套副本**里？"""
        low = str(rel_low or '').lower()
        for pref in self.nested:
            if low.startswith(pref):
                return True
        return False

    def _walk(self):
        import os
        root_str = str(self.root)
        try:
            # ⚠ 相对路径**用字符串拼**，不要 `Path.relative_to()`：
            # Windows 上一次 relative_to 实测 0.34 ms，5854 个文件就是 **2 秒**
            # （原先这里是"打开项目还要 4 秒"的大头）。
            stack = [(root_str, '')]
            while stack:
                cur, prefix = stack.pop()
                try:
                    with os.scandir(cur) as it:
                        for e in it:
                            try:
                                name = e.name
                                if e.is_dir(follow_symlinks=False):
                                    # 跳过 `tmp/`、`.git/`、`__pycache__/`、`build/`…
                                    # —— 它们永远不是游戏素材（扫描规则同源：
                                    # `_SKIP_SCAN_DIRS`），而且 `.git` 里可能有
                                    # 上万个对象文件，白走一遍就是几百毫秒。
                                    if name.lower() in _SKIP_SCAN_DIRS:
                                        continue
                                    stack.append((e.path, prefix + name + '/'))
                                elif e.is_file(follow_symlinks=False):
                                    key = (prefix + name).lower()
                                    self.by_rel[key] = Path(e.path)
                                    self.by_name.setdefault(
                                        name.lower(), []).append(key)
                            except OSError:
                                continue
                except OSError as e:            # noqa: BLE001 - 目录读不动就跳过
                    swallow('assets.asset_registry.FileWalkIndex._walk', e)
        except Exception as e:                  # noqa: BLE001
            swallow('assets.asset_registry.FileWalkIndex._walk', e)

    # ---------- 匹配 ----------

    def match(self, rel):
        """
        资源表里那一串 `Path` → 磁盘上的真实文件（找不到返回 None）。

        **候选顺序与 `resolve_thumb_source` 一致**（那是"唯一真源"的语义），
        只是把"逐个 `is_file()`"换成字典查找：

            base = 项目根：`text` / 去掉第一段 / `game/` + 去掉第一段 / `game/` + text

        ⚠ 一开始这里还有一条"**同名文件**全局唯一就认它"的兜底 —— 实测**太松**：
        `game/gui/dutui/frame 9/返回上一界面.png` 会被表里**别处**的同名文件
        认领，于是它被当成"已登记"、再也不会补进表（候选里就看不到它）。
        现在没有兜底；索引里没命中、且基准落在**项目外**（`root.parent` / cwd）
        时才退回磁盘探测 —— 那种行很少（真实项目 3123 行里 12 行）。
        """
        text = str(rel or '').strip().replace('\\', '/')
        if not text:
            return None
        while text.startswith('./'):
            text = text[2:]
        if text.startswith('/') or (len(text) > 1 and text[1] == ':'):
            return None                          # 绝对路径：索引不覆盖，不猜
        low = text.lower()
        parts = [p for p in low.split('/') if p not in ('', '.')]
        if not parts:
            return None
        cands = [low]
        if len(parts) > 1:
            cands.append('/'.join(parts[1:]))
            cands.append('game/' + '/'.join(parts[1:]))
        cands.append('game/' + low)
        for c in cands:
            hit = self.by_rel.get(c)
            if hit is not None:
                return hit
        return self._probe_outside(text, parts)

    def _probe_outside(self, text, parts):
        """
        索引没命中时，照顾"基准在项目外"的老行为（`root.parent` / cwd）。

        只在**未命中的行**上摸磁盘（几条 `is_file()`），顺序与
        `resolve_thumb_source` 的那两个基准一致。
        """
        try:
            bases = [self.root.parent, Path.cwd()]
        except Exception as e:                  # noqa: BLE001
            swallow('assets.asset_registry.FileWalkIndex._probe_outside', e)
            return None
        seen = set()
        for base in bases:
            cands = [base / text]
            if len(parts) > 1:
                cands.append(base / '/'.join(parts[1:]))
                cands.append(base / 'game' / '/'.join(parts[1:]))
            cands.append(base / 'game' / text)
            for c in cands:
                key = str(c).lower()
                if key in seen:
                    continue
                seen.add(key)
                try:
                    if c.is_file():
                        return c
                except OSError:
                    continue
        return None

    def files(self):
        """所有文件的相对路径（小写 posix）→ Path。"""
        return self.by_rel


def _mark_unusable(registry, project_root, walk=None):
    """
    标出"表里有、磁盘上却没有"的行 → `registry.unusable`。

    用户 2026-10-05：

    > 我认为表应当实时反应实际资源的情况，自动更新和增删！
    > 表里有但实际没有的资源也不能用啊？？？
    > （除非人工把它录入占位，这个允许）

    判据：`Path` 解析不到真实文件，**且**这一行没有人工占位标记
    （`is_placeholder_row`）。被标的行**留在表里**（对账、体检要看得见），
    但 `AssetIndex` 建索引时会跳过 → 候选下拉没有、引用解析不到、
    编译按缺资源处理（"不能用"）。
    """
    for kind, attr in (('image', 'images'), ('music', 'music'),
                       ('sound', 'sounds'), ('voice', 'voices')):
        for aid, row in (getattr(registry, attr, {}) or {}).items():
            if is_placeholder_row(row):
                continue                        # 人工占位：允许没有文件
            if _row_file_exists(kind, row, project_root, walk=walk):
                continue
            registry.unusable[attr][aid] = \
                f"文件不存在：{(row or {}).get('Path') or '（没写 Path）'}"


def validate_registry(registry: AssetRegistry, project_root, walk=None):
    """检查文件存在性、依赖引用、颜色格式等。"""
    project_root = Path(project_root)
    _mark_unusable(registry, project_root, walk=walk)

    for img_id, data in registry.images.items():
        p = data.get('Path')
        if p and not _row_file_exists('image', data, project_root, walk=walk):
            registry.warnings.append(f"图片 '{img_id}' 文件不存在: {p}")

    for type_key in ('music', 'sounds', 'voices'):
        for aid, data in getattr(registry, type_key).items():
            p = data.get('Path')
            if p and not _row_file_exists(type_key.rstrip('s'), data,
                                          project_root, walk=walk):
                registry.warnings.append(f"{type_key} '{aid}' 文件不存在: {p}")

    image_tags = {d.get('Tag') for d in registry.images.values() if d.get('Tag')}
    for char_id, data in registry.characters.items():
        if data.get('ImageTag') and data['ImageTag'] not in image_tags:
            registry.warnings.append(
                f"角色 '{char_id}' 的 ImageTag '{data['ImageTag']}' "
                f"未找到对应图片标签")
        if data.get('DefaultTransform') and \
                data['DefaultTransform'] not in registry.transforms:
            registry.warnings.append(
                f"角色 '{char_id}' 的 DefaultTransform "
                f"'{data['DefaultTransform']}' 未定义")

    for char_id, data in registry.characters.items():
        color = data.get('Color')
        if color and not _COLOR_PATTERN.match(str(color)):
            registry.warnings.append(
                f"角色 '{char_id}' 的颜色 '{color}' 格式无效")


# ===================== 脚本引用分析 =====================

# Ren'Py 自带的变换 / 转场 / 位置白名单：已收敛到
# `storygraph.core.vocabulary.RENPY_BUILTIN_TRANSFORMS`，顶部导入后
# 在本模块继续对外可见（validate / script_table / 测试都从这里取，
# 不破坏既有 import 面）。

# show / scene / hide 之后用来切分"图像名"与关键字的分界符
_SHOW_STOP_WORDS = frozenset({
    'at', 'as', 'with', 'behind', 'onlayer', 'zorder', 'expression',
})

_RE_SHOW_LINE = re.compile(
    r'^\s*(?P<verb>show|scene|hide)\s+(?P<rest>.*?)\s*$', re.MULTILINE)
_RE_AUDIO_LINE = re.compile(
    r'^\s*(?:play|queue|stop)\s+(?P<channel>music|sound|audio)\s+'
    r'["\']?(?P<name>[\w.]+)', re.MULTILINE)
_RE_AT_TRANSFORM = re.compile(r'\bat\s+(?P<name>[\w.]+)')
_RE_WITH_TRANSITION = re.compile(r'\bwith\s+(?P<name>[\w.]+)')
# voice 两种写法：字符串路径（voice "voice/a.ogg"）或裸变量/ID（voice v_001）。
# 之前这条正则路径完全没收 voice，导致语音资产永远显示"未使用"。
_RE_VOICE_LINE = re.compile(
    r'^\s*voice\s+(?:["\'](?P<q>[^"\']+)["\']|(?P<w>[\w.]+))', re.MULTILINE)


def is_dynamic_image_spec(spec: str) -> bool:
    """
    这个"图像名"是运行时才确定的表达式吗？

    Ren'Py 允许 `show expression Text("...") as x`、`scene expression
    holo("lan base")`，以及带 ATL 的 `image icon:` 块头。
    这些都不是静态资产引用，拿去查资源表必然查不到——以前会把它们
    报成"未登记"（真实项目里就是这几条）。
    """
    s = str(spec or '').strip()
    if not s:
        return True
    if s.startswith('expression') or s.startswith('at '):
        return True
    if s.endswith(':'):
        return True
    if '(' in s or ')' in s:
        return True
    return False


def is_dynamic_transform_spec(name: str) -> bool:
    """`with Dissolve(1.5)` 这种函数式转场不是 transform ID。"""
    s = str(name or '').strip()
    return bool(s) and ('(' in s or ')' in s or s.startswith('expression'))


def _image_spec_candidates(spec: str) -> list:
    """
    把一个脚本里的图像名展开成"可能在资产表里叫什么的候选列表"，
    按确定性从高到低排列。

    Ren'Py 的 `show bg room` 与文件 `bg_room.png` 是同一个东西，
    所以空格形式和下划线形式都要试；只有 tag（`show bg`）时退化为
    最宽泛的匹配，交给 resolve_reference 兜底。
    """
    if not spec:
        return []
    parts = spec.split()
    candidates = []
    # 完整名（下划线形式优先级最高：bg room -> bg_room）
    if len(parts) > 1:
        candidates.append('_'.join(parts))
    candidates.append(spec)
    # 逐段递减的后缀：bg room night -> bg_room -> bg
    for i in range(len(parts) - 1, 0, -1):
        candidates.append('_'.join(parts[:i]))
    # 去重保序
    seen = set()
    result = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _extract_image_spec(rest: str) -> str:
    """从 show/scene/hide 的剩余部分里切出图像名，遇到关键字即停。"""
    parts = []
    for tok in rest.split():
        low = tok.lower().rstrip(',')
        if low in _SHOW_STOP_WORDS:
            break
        parts.append(tok.rstrip(','))
    return ' '.join(parts)


def _normalize_spec(s) -> str:
    """
    归一化一个"图像名"用于比较。

    Ren'Py 里 `show eileen happy`、资产 ID `eileen_happy`、文件名
    `eileen-happy.png` 是同一个东西，所以下划线、连字符统一当空格，大小写无关。
    """
    s = str(s or '')
    for ch in ('_', '-'):
        s = s.replace(ch, ' ')
    return ' '.join(s.split()).lower()


def image_alias_index(registry: AssetRegistry) -> dict:
    """
    构建「图片别名 → 资产 ID 列表」映射，这是 P0 的核心。

    旧实现的错在于：资产 ID 取的是"文件名"，而脚本里写的是
    `tag + attribute`（`show eileen happy`），两者形状根本不同，
    于是 `eileen/` 目录下几十张表情图全部被误报成"未使用资产"。

    这里给每张图显式算出它可能被怎么称呼：
      ID、Tag、Attributes、`Tag Attributes`、以及去掉 tag 前缀的 Attributes。
    """
    index = defaultdict(list)
    for aid, row in (registry.images or {}).items():
        for alias in image_aliases(aid, row):
            if alias and aid not in index[alias]:
                index[alias].append(aid)
    return index


# 扫描资产时会给音频自动加的前缀。脚本里通常不带这些前缀
# （`play music night`），所以解析时要能剥掉。
_AUDIO_ID_PREFIXES = ('bgm_', 'bgm ', 'sfx_', 'sfx ', 'se_', 'se ',
                      'voice_', 'voice ', 'mus_', 'mus ', 'v_')


def audio_aliases(aid: str, row: dict) -> set:
    """
    一条音频资产的称呼集合（已归一化）。

    `bgm_night` 的别名 = {bgm night, night, rain…}；文件路径
    `music/rain.ogg` 的 stem 也算——扫描器加前缀是为了 ID 规范，
    不该让"脚本写的名字"和"表里的 ID"因此对不上。
    """
    out = set()
    if aid:
        out.add(_normalize_spec(aid))

    def _add_variants(name):
        if not name:
            return
        out.add(_normalize_spec(name))
        low = name.lower()
        for p in _AUDIO_ID_PREFIXES:
            if low.startswith(p) and len(name) > len(p):
                out.add(_normalize_spec(name[len(p):]))

    _add_variants(aid)
    path = row.get('Path')
    if path:
        _add_variants(Path(str(path)).stem)
    _add_variants(row.get('Name'))
    return {a for a in out if a}


def image_aliases(aid: str, row: dict) -> set:
    """单张图片的全部称呼（已归一化）。"""
    out = set()
    if aid:
        out.add(_normalize_spec(aid))

    tag = _normalize_spec(row.get('Tag'))
    attrs = _normalize_spec(row.get('Attributes'))
    name = _normalize_spec(row.get('Name'))

    if tag:
        out.add(tag)
    if attrs:
        out.add(attrs)
    if name:
        out.add(name)
    if tag and attrs:
        out.add(f'{tag} {attrs}')
        # Attributes 自带 tag 前缀时（eileen_happy），再去掉一层前缀
        if attrs.startswith(tag + ' '):
            out.add(attrs[len(tag) + 1:])
    return {a for a in out if a}


class AssetIndex:
    """
    一次构建、重复使用的**解析索引**。

    为什么必须有它：解析一个名字原本要遍历所有资产并现场算别名，
    于是"校验 N 条引用 × M 个资产"变成 O(N×M)。真实项目 2828 张图 ×
    4058 次解析 = 1800 万次别名计算，一次体检要 268 秒，UI 直接假死。
    索引建一次（O(M)），之后每次解析是哈希查找 + 二分前缀，O(log M)。

    索引是**快照**：资源表变了要重新 build（引擎/校验都在每轮开始时重建）。
    """

    def __init__(self, registry: AssetRegistry):
        self.ids = {}          # 原始 ID（全类型）
        self.id_tag = {}       # 图片 ID -> 归一化 Tag（'' = 这行没 Tag）
        self.audio = {}        # 音频别名（归一化）-> [id]
        self.image = {}        # 图片别名（归一化）-> [(id, is_loose)]
        self.prefix = []       # 排序后的 (别名, id)，用于前缀匹配（排除纯 tag）

        mapping = (('images', 'image'), ('music', 'music'),
                   ('sounds', 'sound'), ('voices', 'voice'),
                   ('transforms', 'transform'),
                   ('characters', 'character'))
        #: **不可用的行不建索引** —— 表里有、文件不在了的那些
        #: （用户 2026-10-05："表里有但实际没有的资源也不能用"）。
        #: 这是唯一的收口点：候选、校验、编译的引用解析全走这里。
        unusable = getattr(registry, 'unusable', None) or {}
        for attr, _kind in mapping:
            bad = set((unusable.get(attr) or {}).keys())
            for aid in (getattr(registry, attr, {}) or {}):
                if aid in bad:
                    continue
                self.ids[aid] = True

        for attr in ('music', 'sounds', 'voices'):
            bad = set((unusable.get(attr) or {}).keys())
            for aid, row in (getattr(registry, attr, {}) or {}).items():
                if aid in bad:
                    continue
                for alias in audio_aliases(aid, row or {}):
                    self.audio.setdefault(alias, [])
                    if aid not in self.audio[alias]:
                        self.audio[alias].append(aid)

        prefix_buf = set()
        bad_images = set((unusable.get('images') or {}).keys())
        for aid, row in (registry.images or {}).items():
            if aid in bad_images:
                continue                            # 文件没了 → 当它不存在
            row = row or {}
            tag = _normalize_spec(row.get('Tag'))
            attrs = _normalize_spec(row.get('Attributes'))
            self.id_tag[aid] = tag
            for alias in image_aliases(aid, row):
                loose = bool(alias) and alias == tag and attrs != alias
                self.image.setdefault(alias, [])
                entry = (aid, loose)
                if entry not in self.image[alias]:
                    self.image[alias].append(entry)
                if alias and not loose:
                    prefix_buf.add((alias, aid))
        self.prefix = sorted(prefix_buf)

    # ---------- 查询 ----------

    def _prefer_tag_attribute(self, cand: str):
        """
        `ling_angry` 这种**下划线写法**：如果它更像是 `tag attribute`，
        而精确命中的那一行又没有 Tag → 返回**带 Tag 的那一行**的 ID。

        只在这两个条件同时成立时才改判（保守）：
          · 精确 ID 那行的 Tag 为空（不是立绘/背景那一类有 tag 的资源）；
          · `tag + attribute` 能命中**带 Tag** 的行。
        其它情况一律返回 None（保持"精确 ID 优先"的老行为）。
        """
        if '_' not in cand or ' ' in cand:
            return None
        if self.id_tag.get(cand):
            return None                      # 精确那行自己有 Tag → 别抢
        low = cand.lower()
        for i in range(len(low) - 1, 0, -1):
            if low[i] != '_':
                continue
            tag, attr = low[:i], low[i + 1:]
            if not tag or not attr:
                continue
            for aid, is_loose in self.image.get(f'{tag} {attr}', ()):
                if is_loose:
                    continue
                if self.id_tag.get(aid):
                    return aid               # 带 Tag → 这才是真立绘
        return None

    def resolve(self, candidate: str) -> list:
        if not candidate:
            return []
        cand = str(candidate).strip()
        if not cand:
            return []
        if cand in self.ids:
            # ⚠ **别让"没有 Tag 的行"劫持 `tag_attribute` 写法**（2026-10-05
            # 用户实测："改一个表……立绘不显示，而且表情也调不了了"）。
            #
            # 卡片里存的是 `ling_angry`（工具自己的"空格→下划线"写法），
            # 而表里可能正好有一行 ID 就叫 `ling_angry` —— 它来自**另一份
            # 副本**（`sr_general/game/cc_preview/ling/angry.png`），Tag 是空的。
            # 精确 ID 命中排在最前面，于是这个"同名但不是立绘"的行赢了：
            #   · 预览按它的 Path 取图（`cc_preview` 里的立绘特写，或取不到）
            #   · 表情/位置候选按它的 Tag（空）算 → **一个都没有**
            # 修法：当下划线写法能解释成 `tag attribute` 且能命中**带 Tag 的
            # 行**、而精确 ID 那行**没有 Tag** 时，用前者。
            better = self._prefer_tag_attribute(cand)
            if better is not None:
                return [better]
            return [cand]

        # 脚本里音频经常写成"路径"（`play music "bgm/bgm02.mp3"`），
        # 表里存的却是 ID（`bgm_bgm02`）。所以路径要逐级退化成
        # 去目录名 → 去扩展名 再试，否则整章音乐全被报"未登记"。
        for norm in _spec_variants(cand):
            if not norm:
                continue
            audio = self.audio.get(norm)
            if audio:
                return list(audio)

            hits, loose = [], []
            for aid, is_loose in self.image.get(norm, ()):
                (loose if is_loose else hits).append(aid)
            if hits:
                return hits
            if loose:
                # 只写 tag（`scene bg`）时认一张代表，否则"未使用资产"恒为 0
                return loose[:1]

            found = self._prefix_ids(norm)
            if found:
                return found
        return []

    def _prefix_ids(self, norm: str) -> list:
        """前缀匹配（Ren'Py 的 `show tag attr` 语义），二分定位起止。"""
        import bisect
        start = bisect.bisect_left(self.prefix, (norm + ' ', ''))
        out = []
        for i in range(start, len(self.prefix)):
            alias, aid = self.prefix[i]
            if not alias.startswith(norm + ' '):
                break
            if aid not in out:
                out.append(aid)
        return out


def _spec_variants(name: str) -> list:
    """
    一个引用的可能写法（按精确度递减）：
      原始归一 → 去掉目录部分 → 去掉扩展名
    """
    s = str(name or '').replace('\\', '/').strip()
    out = []
    for v in (s, s.rsplit('/', 1)[-1]):
        n = _normalize_spec(v)
        if n and n not in out:
            out.append(n)
        if '.' in v and not v.startswith('.'):
            n2 = _normalize_spec(v.rsplit('.', 1)[0])
            if n2 and n2 not in out:
                out.append(n2)
    return out


def resolve_references(candidate: str, registry: AssetRegistry = None,
                       index: 'AssetIndex' = None) -> list:
    """
    把一个脚本里出现的名字解析成**一组**资产 ID。

    匹配顺序：全类型精确 ID → 音频别名（剥 bgm_/sfx_ 前缀）→ 图片别名
    → 图片前缀（`show eileen happy` 覆盖 `eileen happy *` 变体）。

    循环里调用请传 index（否则每次都会重建索引，真实项目上是几百倍差距）。
    """
    if not candidate:
        return []
    if index is None:
        if registry is None:
            return []
        index = AssetIndex(registry)
    return index.resolve(candidate)


def resolve_reference(candidate: str, registry: AssetRegistry = None,
                      index: 'AssetIndex' = None) -> Optional[str]:
    """单值版本：解析不了返回 None。需要覆盖整组变体时用 resolve_references。"""
    ids = resolve_references(candidate, registry, index)
    return ids[0] if ids else None


def extract_script_references(text: str) -> list:
    """
    抽取一段脚本里的资产引用。

    返回 [(kind, raw_name, line_no, candidates), ...]：
      kind ∈ {'image', 'music', 'sound', 'transform'}
      raw_name   用于报告的名字（图像就是完整 spec）
      candidates 这个名字"可能在资产表里叫什么"的候选，按确定性排序

    抽取阶段只做语法识别，不做资产表解析——后者交给
    resolve_reference，避免这里硬编码表的形状。

    注意候选是**整组**的：`show bg room` 会展开成
    ('bg_room', 'bg room', 'bg')，只要有一个能对上就算引用成立；
    若逐个独立判定，命中之外的候选都会被误报成断裂引用。
    """
    refs = []

    def line_of(pos):
        return text[:pos].count('\n') + 1

    for m in _RE_SHOW_LINE.finditer(text):
        spec = _extract_image_spec(m.group('rest'))
        if not spec or spec.lower() == 'expression':
            continue
        clean = spec.replace('"', '')
        # 运行时表达式 / 带 ATL 的块头 / 函数调用 → 不是静态资产引用
        if is_dynamic_image_spec(clean):
            continue
        cands = _image_spec_candidates(clean)
        refs.append(('image', clean, line_of(m.start()), tuple(cands)))

    for m in _RE_AUDIO_LINE.finditer(text):
        name = m.group('name')
        refs.append((m.group('channel'), name, line_of(m.start()), (name,)))

    for m in _RE_VOICE_LINE.finditer(text):
        name = m.group('q') or m.group('w')
        if name:
            refs.append(('voice', name, line_of(m.start()), (name,)))

    for m in _RE_AT_TRANSFORM.finditer(text):
        name = m.group('name')
        if name in RENPY_BUILTIN_TRANSFORMS:
            continue
        refs.append(('transform', name, line_of(m.start()), (name,)))

    for m in _RE_WITH_TRANSITION.finditer(text):
        name = m.group('name')
        if name in RENPY_BUILTIN_TRANSFORMS:
            continue
        refs.append(('transform', name, line_of(m.start()), (name,)))

    return refs


def references_from_statements(statements) -> list:
    """
    从 core.Statement 流抽取资产引用 —— **统一真源**（一.3）。

    返回 [(kind, raw_name, candidates, loc), ...]，与 extract_script_references
    同构，只是数据来源从"正则扫文本"升级为"读已解析的语句"。

    为什么要收敛到这里：以前有三套规则在跑
      · asset_registry.extract_script_references（正则扫原文）
      · asset_extensions.DependencyAnalyzer（读 registry.references）
      · script_table.find_unregistered_assets（自己归一 + 白名单）
    同一句 `show eileen happy` 在三处可能得出三种结论。现在抽取统一
    走 `_image_spec_candidates`，判定统一走 `resolve_references`，
    只保留"文本正则"这一条快速兜底路径（拿不到 Statement 时才用）。
    """
    from storygraph.core.statement import StatementKind

    refs = []

    def loc_of(st):
        src = getattr(st, 'source', None) or ('', 0)
        fname = Path(str(src[0])).name if src[0] else ''
        return f"{fname}:{src[1]}" if fname else ''

    for st in statements or ():
        kind = getattr(st, 'kind', None)
        payload = getattr(st, 'payload', None) or {}

        if kind in (StatementKind.SHOW, StatementKind.SCENE,
                    StatementKind.HIDE):
            spec = (payload.get('image') or '').strip()
            loc = loc_of(st)
            is_expr = bool(payload.get('is_expression'))
            if spec and not is_expr and not is_dynamic_image_spec(spec):
                clean = spec.replace('"', '')
                refs.append(('image', clean,
                             tuple(_image_spec_candidates(clean)), loc))
            # show 子句里的 `at xxx` / `with xxx` 也是变换引用
            for t in (payload.get('at') or []):
                t = str(t).strip()
                if t and t not in RENPY_BUILTIN_TRANSFORMS \
                        and not is_dynamic_transform_spec(t):
                    refs.append(('transform', t, (t,), loc))
            with_ = (payload.get('with_') or '').strip()
            if with_ and with_ not in RENPY_BUILTIN_TRANSFORMS \
                    and not is_dynamic_transform_spec(with_):
                refs.append(('transform', with_, (with_,), loc))

        elif kind == StatementKind.PLAY:
            channel = (payload.get('channel') or '').strip()
            name = (payload.get('name') or '').strip()
            if name:
                refs.append(('music' if channel == 'music' else 'sound',
                             name, (name,), loc_of(st)))

        elif kind == StatementKind.VOICE:
            name = (payload.get('name') or '').strip()
            if name:
                refs.append(('voice', name, (name,), loc_of(st)))

    return refs


def analyze_statement_references(statements, registry: AssetRegistry):
    """
    用 Statement 流填充 registry.references / unresolved。

    与 analyze_references（正则版）等价，但更准：它读的是已解析的结构，
    不会被字符串里碰巧出现的 `show` 骗到。拿不到 ParseResult 时才退回
    analyze_references。
    """
    if not isinstance(registry.references, defaultdict):
        registry.references = defaultdict(list, registry.references or {})
    if not isinstance(registry.unresolved, defaultdict):
        registry.unresolved = defaultdict(list, registry.unresolved or {})

    index = AssetIndex(registry)          # 建一次，循环里复用
    for kind, raw, cands, loc in references_from_statements(statements):
        asset_ids = []
        for cand in cands:
            asset_ids = resolve_references(cand, registry, index)
            if asset_ids:
                break
        if asset_ids:
            for aid in asset_ids:
                if loc and loc not in registry.references[aid]:
                    registry.references[aid].append(loc)
        else:
            bucket = registry.unresolved[kind]
            if (raw, loc) not in bucket:
                bucket.append((raw, loc))
    return registry


def analyze_references(script_files, registry: AssetRegistry):
    """
    扫描 .rpy 脚本中的资产引用，填充 registry.references。

    registry.references: {asset_id: ['file.rpy:line', ...]}
    只有解析到具体资产的才记入；解析不到的留在
    registry.unresolved（供"断裂引用"诊断，可按需忽略某些类型）。

    之前的实现只取 `show` 后的第一个词（tag），而资产 ID 是完整文件名，
    两边对不上，导致整库图片被判成"未使用"。
    """
    if registry.references is None:
        registry.references = defaultdict(list)
    elif not isinstance(registry.references, defaultdict):
        converted = defaultdict(list)
        for k, v in registry.references.items():
            converted[k] = list(v)
        registry.references = converted

    if not hasattr(registry, 'unresolved') or registry.unresolved is None:
        registry.unresolved = defaultdict(list)

    index = AssetIndex(registry)          # 建一次，循环里复用
    for fp in script_files:
        fp = Path(fp)
        if not fp.exists():
            continue
        try:
            text = fp.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            swallow('assets.asset_registry.analyze_references', e)
            continue

        for kind, raw, line_no, cands in extract_script_references(text):
            loc = f"{fp.name}:{line_no}"

            # 候选整组判定：用一个候选解析（可能命中多张图，如立绘的表情集）
            asset_ids = []
            for cand in cands:
                asset_ids = resolve_references(cand, registry, index)
                if asset_ids:
                    break

            if asset_ids:
                for aid in asset_ids:
                    if loc not in registry.references[aid]:
                        registry.references[aid].append(loc)
            else:
                bucket = registry.unresolved[kind]
                if (raw, loc) not in bucket:
                    bucket.append((raw, loc))


def describe_asset(asset_type: str, row: dict) -> str:
    """
    给人看的一句话说明（用于候选表的"说明"列、应用内选择器的详情面板）。

    目标：文案在 Excel 里点开下拉、或在应用里选中一条资产时，能立刻
    知道"这是什么"，而不是只看到一个可能是缩写的 ID。
    """
    row = row or {}

    def g(key):
        v = row.get(key)
        return '' if v is None else str(v).strip()

    if asset_type == 'character':
        parts = [g('Name') or g('ID')]
        if g('Color'):
            parts.append(g('Color'))
        if g('ImageTag'):
            parts.append(f"立绘 {g('ImageTag')}")
        if g('VoiceTag'):
            parts.append(f"语音 {g('VoiceTag')}")
        if g('Kind'):
            parts.append(f"kind={g('Kind')}")
        cnt = row.get('_dialogue_count')
        if isinstance(cnt, int) and cnt:
            parts.append(f'台词 {cnt}')
        note = g('Notes')
        if note and ':' in note and '\\' in note:
            parts.append('定义于 ' + note.split('\\')[-1])
        return ' · '.join(p for p in parts if p)

    if asset_type == 'image':
        # 面向文案：类目给中文，别让"other"这种内部枚举出现在表格里
        category_labels = {
            'background': '背景', 'cg': 'CG', 'ui': '界面',
            'character': '立绘', 'other': '其他',
        }
        parts = []
        if g('Category'):
            parts.append(category_labels.get(g('Category'), g('Category')))
        if g('Tag'):
            parts.append(f"tag={g('Tag')}")
        if g('Attributes'):
            parts.append(f"属性={g('Attributes')}")
        if g('Path'):
            parts.append(g('Path'))
        return ' · '.join(parts)

    if asset_type == 'transform':
        # 说明 > ATL 首行 > 兜底。文案最需要的是那句话。
        parts = []
        doc = g('Doc') or g('Notes')
        if doc and ':' in doc and ('\\' in doc or '/' in doc) and len(doc) < 80:
            doc = ''
        if doc:
            parts.append(doc.replace('\n', ' '))
        if g('Params'):
            parts.append(f"参数 {g('Params')}")
        if g('Category'):
            parts.append(g('Category'))
        if not parts:
            for ln in (g('ATL') or '').splitlines():
                s = ln.strip()
                if s and not s.startswith('#'):
                    parts.append(s)
                    break
        if g('ATL'):
            parts.append(f"ATL {len([l for l in g('ATL').splitlines() if l.strip()])} 行")
        return ' · '.join(parts)

    # music / sound / voice
    parts = []
    if g('Path'):
        parts.append(g('Path'))
    if g('Loop'):
        parts.append('循环')
    if g('Volume'):
        parts.append(f"音量 {g('Volume')}")
    return ' · '.join(parts)


# ===================== 引用体检（纯函数：只看 registry） =====================
#
# 这两个查询原先只长在 `AssetEngine` 上（`find_unused_assets` /
# `find_broken_references`），于是**只有"有一个真引擎"的地方能用** ——
# Qt 版的会话手上只有 registry，而 `app.asset_panel` 又要求传入的对象带这
# 两个方法，结果"未使用资产 / 断裂引用"这类体检在编辑器里一直没有入口。
# 实现只依赖 `registry.references` / `registry.unresolved`，所以下移到资源表
# 层：引擎与适配器都转发到这里（一份实现，两处调用）。

def unused_assets(registry) -> list:
    """没人引用的资产：`[{'category': 'images', 'asset_id': …}, …]`。"""
    if registry is None:
        return []
    referenced = set(getattr(registry, 'references', {}) or {})
    out = []
    for name in ('images', 'music', 'sounds', 'transforms'):
        for aid in (getattr(registry, name, None) or {}):
            if aid not in referenced:
                out.append({'category': name, 'asset_id': aid})
    return out


def broken_references(registry, include_transforms: bool = False) -> list:
    """
    脚本引用了、但资产表里查不到的名字。

    transform 默认**不算**断裂：脚本里 `at my_slide` 用的自定义变换，用户
    完全可以不登记到 transforms.xlsx（那里只放要复用和管理的），把它报成
    "断裂引用"只会制造噪音。
    """
    if registry is None:
        return []
    unresolved = getattr(registry, 'unresolved', {}) or {}
    grouped = defaultdict(list)
    for kind, entries in unresolved.items():
        if kind == 'transform' and not include_transforms:
            continue
        for raw, loc in (entries or ()):
            grouped[(kind, raw)].append(loc)
    out = [{'asset_id': raw, 'kind': kind, 'locations': list(locs)}
           for (kind, raw), locs in grouped.items()]
    out.sort(key=lambda b: (b['kind'], b['asset_id']))
    return out


# ===================== 补登缺失资源（合并写，绝不重写整表） =====================

def _norm_path_for_compare(path) -> str:
    """比对用路径：统一分隔符、去空格、小写（表里实测有 `bg_2 .png` 这种手误）。"""
    s = str(path or '').replace('\\', '/').strip().lower()
    return re.sub(r'\s+', '', s)


def register_missing_assets(project_root, asset_dir=None,
                            include_ui: bool = False) -> dict:
    """
    把**磁盘上有、资产表里没登记**的图片（以及音乐/音效/语音）**追加**进表。

    用户 2026-10-05：

    > 先查背景图为何没有被登记进去，然后，让背景图被登记进去。

    实测 `sr_general`：`game/images/bg` 下有 39 张，表里只认到 30 张；
    表里用下划线名（`bg_1`）而磁盘是点号名（`bg1.2.png`）—— 于是脚本里的
    `bg bg1_2` 在表里查不到，演出预览的背景只能靠磁盘回退兜着。

    为什么不能直接用 `write_asset_excel`：它是"**只写扫描结果**"的全量重写
    （2828 行会掉到 349 行）。这里改成**只追加缺失行**：
      · 已登记（路径或 ID 命中，路径比对会去掉空格/大小写差异）→ 跳过；
      · 其余 → 追加到对应表的末尾，并在写之前把原表备份成 `*.bak.xlsx`。

    图片的 `Attributes` 会做一次 `.` → `_` 归一（Ren'Py 里
    `image bg bg6_2 = "images/bg/bg6.2.png"` 就是这种对应关系），
    这样脚本里写的 `bg bg6_2` 才能在表里查到。
    返回 `{ok, added, skipped, backup, by_kind, rows}`。
    """
    project_root = Path(project_root) if project_root else None
    if project_root is None:
        return {'ok': False, 'error': '没有项目根目录', 'added': 0,
                'skipped': 0, 'backup': '', 'by_kind': {}, 'rows': []}
    tables_dir = Path(asset_dir) if asset_dir else \
        (project_root / 'game' / 'asset_tables')
    game = project_root / 'game'
    scan_root = game if game.is_dir() else project_root
    try:
        assets = scan_assets(scan_root, include_ui=include_ui)
    except Exception as e:                    # noqa: BLE001
        return {'ok': False, 'error': f'扫描失败：{e}', 'added': 0,
                'skipped': 0, 'backup': '', 'by_kind': {}, 'rows': []}

    plan = [
        ('images', 'images.xlsx',
         ['ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes'],
         list(assets.get('images') or []), True),
        ('music', 'music.xlsx', ['ID', 'Path', 'Loop', 'Volume', 'Notes'],
         list((assets.get('audio') or {}).get('music') or []), False),
        ('sounds', 'sounds.xlsx', ['ID', 'Path', 'Notes'],
         list((assets.get('audio') or {}).get('sounds') or []), False),
        ('voices', 'voices.xlsx', ['ID', 'Path', 'Notes'],
         list(assets.get('voices') or []), False),
    ]

    by_kind: dict = {}
    all_rows: list = []
    backups: list = []
    added_total = skipped_total = 0
    import shutil
    for kind, filename, headers, scanned, is_image in plan:
        target = tables_dir / filename
        if not scanned and not target.exists():
            continue
        try:
            existing = read_excel_rows(target) if target.exists() else []
        except Exception as e:                     # noqa: BLE001
            swallow('assets.asset_registry.register_missing_assets', e)
            existing = []
        have_paths = {_norm_path_for_compare(r.get('Path')) for r in existing}
        have_ids = {str(r.get('ID') or '').strip() for r in existing}
        fresh = []
        for row in scanned:
            if _norm_path_for_compare(row.get('Path')) in have_paths:
                skipped_total += 1
                continue
            if str(row.get('ID') or '').strip() in have_ids:
                skipped_total += 1
                continue
            row = dict(row)
            if is_image:
                row['Attributes'] = str(row.get('Attributes') or '').replace('.', '_')
                row['ID'] = str(row.get('ID') or '').replace('.', '_')
            fresh.append(row)
        by_kind[kind] = len(fresh)
        if not fresh:
            continue
        backup = ''
        try:
            tables_dir.mkdir(parents=True, exist_ok=True)
            if target.exists():
                backup_path = target.with_suffix('.bak.xlsx')
                shutil.copyfile(target, backup_path)
                backup = str(backup_path)
                backups.append(backup)
            wb = openpyxl.load_workbook(target) if target.exists() \
                else openpyxl.Workbook()
            ws = wb.active
            if ws.max_row < 1 or not any(c.value for c in ws[1]):
                for col, name in enumerate(headers, start=1):
                    set_text(ws, 1, col, name)
            for row in fresh:
                values = [row.get(h, '') for h in headers]
                append_text_row(ws, values)
                all_rows.append(row)
            wb.save(target)
            added_total += len(fresh)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': f'写 {filename} 失败：{e}',
                    'added': added_total, 'skipped': skipped_total,
                    'backup': backup, 'by_kind': by_kind, 'rows': all_rows}
    return {'ok': True, 'error': '', 'added': added_total,
            'skipped': skipped_total, 'backup': backups[0] if backups else '',
            'backups': backups, 'by_kind': by_kind, 'rows': all_rows,
            'tables_dir': str(tables_dir)}


# ===================== 命令行自测 =====================

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python asset_registry.py <扫描目录> [输出目录]")
        sys.exit(0)

    scan_root = Path(sys.argv[1])
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd() / 'asset_tables'

    print(f"扫描目录: {scan_root}")
    assets = scan_assets(scan_root, include_ui=False)
    write_asset_excel(assets, output_dir, split_by_category=False)
    print(f"已生成资产表到 {output_dir}")

    reg = parse_asset_tables(output_dir)
    print(f"图片: {len(reg.images)}, 音乐: {len(reg.music)}, 音效: {len(reg.sounds)}")
    if reg.errors:
        print("错误:", reg.errors)
    if reg.warnings:
        print("警告:", reg.warnings[:10],
              "..." if len(reg.warnings) > 10 else "")