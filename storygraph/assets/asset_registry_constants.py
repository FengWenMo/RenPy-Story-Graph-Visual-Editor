"""
`asset_registry` 的**共享常量表**（2026-10-11 从 `asset_registry.py` 搬出）
========================================================================

为什么单独一个模块：`asset_registry.py` 3614 行（模块级函数 2711 行 / 87 个），
想把函数族搬走时，**三个候选族的传递闭包都被同一批常量拉大**
（实测 xlsx 表读写族：初选 6 -> 闭包 100 个定义 / 2352 行）。
于是先搬这层叶子，后面每一族的闭包都会跟着变小。

⚠ 搬家纪律：**逐字照搬**（连注释与空行）—— `git diff` 一眼能看出"只是搬家"。
"""

from __future__ import annotations

import re
from storygraph.core.vocabulary import (SHOW_CLAUSE_KEYWORDS,
                                        SHOW_INTRO_KEYWORDS)
from storygraph.excel.cells import append_text_row, set_text
import re

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

ASSET_FIELD_LABELS = {
    'ID': '编号',
    'Path': '真实文件',
    'Tag': '角色标签',
    'Attributes': '表情',
    'Category': '类别',
    'Notes': '备注',
    #: 角色别名（纯文字导入用，多值：`MOM|母亲|老妈`）——见 `_TABLE_FIELDS` 的说明
    'Aliases': '别名',
    'Loop': '循环',
    'Volume': '音量',
    'ATL': 'ATL',
    'Params': '参数',
    'Doc': '说明',
}

_asset_set_text = set_text

_asset_append_row = append_text_row

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

_UI_SUFFIXES = ('gui', 'ui', 'hud', 'center', 'screen', 'menu', 'button',
                'icon', 'skin', 'widget', 'panel', 'frame', 'slider', 'bar')

_CHARACTER_CONTAINER_WORDS = frozenset({
    'character', 'characters', 'char', 'chars', 'sprite', 'sprites',
    'portrait', 'portraits', 'figure', 'figures', 'actor', 'actors',
    'tachie', 'standing',
})

_GENERIC_IMAGE_ROOTS = frozenset({
    'images', 'image', 'img', 'imgs', 'graphics', 'assets', 'art', 'pics',
    'pictures',
})

_BG_WORDS = frozenset({'bg', 'bgs', 'background', 'backgrounds', 'backdrop',
                       'backdrops', 'scene', 'scenes'})

_CG_WORDS = frozenset({'cg', 'cgs', 'event', 'events', 'cutscene',
                       'cutscenes', 'illust', 'illusts', 'illustration',
                       'illustrations', 'gallery', 'galleries'})

_BG_NAME_RE = re.compile(r'^(bg(?!m)|backdrop|background)([a-z\d_\-. ]|$)')

_CG_NAME_RE = re.compile(r'^(cg|cutscene|event)([\d_\-. ]|$)')

_UI_NAME_RE = re.compile(
    r'^(start|main|title|titles|menu|settings|config|preference|load|save'
    r'|gameover|game_over|credits|splash|pause|confirm|dialog)'
    r'([\d_\-. ]|$)')

_BG_NAME_HINTS = ('bg_', '_bg', 'background', 'backdrop')

_CG_NAME_HINTS = ('cg_', '_cg', 'cutscene')

_UI_NAME_HINTS = ('ui_', '_ui', 'icon_', '_icon', 'button_', '_button',
                  'btn_', '_btn', 'gui_', '_gui')

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

_SKIP_SCAN_DIRS = frozenset({
    'tmp', 'temp', 'temporary', '.git', '.svn', '.hg', '__pycache__',
    'node_modules', '.venv', 'venv', 'env', 'saves', 'cache', '.cache',
    '.pytest_cache', '.mypy_cache', '.idea', '.vscode', 'build',
    '.workbuddy', 'logs', 'log',
})

_GENERIC_IMAGE_DIRS = frozenset({
    'game', 'images', 'image', 'img', 'imgs', 'assets', 'asset', 'graphics',
    'sprite', 'sprites', 'characters', 'character', 'char', 'chars',
    'pics', 'pic', 'pictures', 'picture', 'figure', 'figures', 'art',
    'portrait', 'portraits', 'avatar', 'avatars', 'story', 'data',
})

_MUSIC_DIR_NAMES = {'music', 'bgm', 'bgm_music', 'ost', 'soundtrack',
                    'bgm_music_box'}

_SOUND_DIR_NAMES = {'sound', 'sounds', 'sfx', 'se', 'effect', 'effects',
                    'ampl', 'noise'}

_SOUND_NAME_HINTS = ('sfx', 'se_', '_se', 'sound', 'effect', 'click', 'hit',
                     'voice', 'button', 'ui_', 'impact', 'step', 'door',
                     'noise', 'beep', 'alarm', 'switch')

_MUSIC_NAME_HINTS = ('bgm', 'music', 'theme', 'ost', 'song', 'loop',
                     'soundtrack')

_ASSET_KINDS = ('images', 'music', 'sounds', 'voices')

_STANDARD_TABLES = {
    'characters': 'characters.xlsx',
    'images': 'images.xlsx',
    'transforms': 'transforms.xlsx',
    'music': 'music.xlsx',
    'sounds': 'sounds.xlsx',
    'voices': 'voices.xlsx',
}

_TABLE_FIELDS = {
    'character': ['ID', 'Name', 'Color', 'ImageTag', 'Aliases', 'Notes'],
    'image': ['ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes'],
    # transform 增加 ATL/Params/Doc/Category：支持自定义变换（文档第四节），
    # 导出时按 ATL 生成 `transform id(params):` 代码块；Doc 是给文案看的
    # 一句话说明（表格下拉里选中它就显示这句）。
    'transform': ['ID', 'Path', 'ATL', 'Params', 'Doc', 'Category', 'Notes'],
    'music': ['ID', 'Path', 'Loop', 'Volume', 'Notes'],
    'sound': ['ID', 'Path', 'Notes'],
    'voice': ['ID', 'Path', 'Notes'],
}

_PLACEHOLDER_MARKS = ('占位', 'placeholder', '待补', '计划', 'planned',
                      'todo', 'wip')

_COLOR_PATTERN = re.compile(r'^#(?:[0-9a-fA-F]{3}){1,2}$')

#: show/scene/hide 的剩余部分里"图像名**到哪里为止**"。
#: **唯一真源**在 `core.vocabulary`（2026-10-15 收重复）。
#:
#: ⚠⚠ 这一份从前把 `expression` 加进来了、却**漏了 `screen` 与 `layer`** ——
#: 于是 `show screen msg_dialogue` / `hide screen map` 会被切出
#: `screen msg_dialogue` 当图像名 → 变成一条**假断链**。
#: 真机实测（2026-10-15）：**正则兜底那条路**因此产出 **110 条**假断链
#: （`unresolved['image']` 126 条里 110 条是 `screen …`），而那条路是无条件
#: 跟跑的（`build.collect_report` / `asset_engine._rebuild_references`
#: 都做"Statement 优先 + 正则补覆盖不到的地方"）—— 所以这是真缺陷、不是理论问题。
#: 现在三个引入型关键字一起收进来（`SHOW_INTRO_KEYWORDS`），与规范集同源。
#:
#: ⚠⚠ **2026-10-16（用户排期 C 项）**：它从前还是
#: `_extract_image_spec` 里那句"遇到它就 break"的**切分口径**；那份循环已经删掉，
#: 切分本体交给 `core.vocabulary.classify_show_words`（分词用 `tokenize_show_words`）。
#: 这一份现在只回答**一个问题**："这个词根本不可能是一个参数值/图像名"——
#: 也就是"取词边界"。**别再拿它去 split() 目标**（那正是这次要根治的重复）。
#: 判据：`tests/test_show_clause_words.py`。
_SHOW_STOP_WORDS = SHOW_CLAUSE_KEYWORDS | SHOW_INTRO_KEYWORDS

_RE_SHOW_LINE = re.compile(
    r'^\s*(?P<verb>show|scene|hide)\s+(?P<rest>.*?)\s*$', re.MULTILINE)

_RE_AUDIO_LINE = re.compile(
    r'^\s*(?:play|queue|stop)\s+(?P<channel>music|sound|audio)\s+'
    r'["\']?(?P<name>[\w.]+)', re.MULTILINE)

_RE_AT_TRANSFORM = re.compile(r'\bat\s+(?P<name>[\w.]+)')

_RE_WITH_TRANSITION = re.compile(r'\bwith\s+(?P<name>[\w.]+)')

_RE_VOICE_LINE = re.compile(
    r'^\s*voice\s+(?:["\'](?P<q>[^"\']+)["\']|(?P<w>[\w.]+))', re.MULTILINE)

_AUDIO_ID_PREFIXES = ('bgm_', 'bgm ', 'sfx_', 'sfx ', 'se_', 'se ',
                      'voice_', 'voice ', 'mus_', 'mus ', 'v_')
