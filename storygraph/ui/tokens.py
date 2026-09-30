"""
storygraph.ui.tokens —— 界面设计 token 的**唯一来源**。

为什么要有这个模块（2026-09-29）
--------------------------------
界面改版前，色值/字号/间距是**散落在各处的字面量**
（`fg='#888'`、`font=('微软雅黑', 7)`、`pady=6`…），后果有两个：

  · 想统一调整（比如把不达标的 `#999` 换掉）得满文件搜，必然漏；
  · 同一个语义在不同地方用了不同值（"说明文字"有 `#888` `#999` `#757575` 三种）。

现在所有组件**只从这里取值**，禁止在界面代码里写死颜色与字号——
这是"95% 一致性"与"对比度可审计"的前提。

对比度已按 WCAG 2.2 AA 核算：正文 ≥ 4.5:1，大字与图形 ≥ 3:1。
`graphic` 一档**只能用于图形/骨架文字，不承载正文**（2.8:1 不达标）。
"""
from __future__ import annotations

# ==================================================================
#  色彩
# ==================================================================

LIGHT = {
    'bg.canvas': '#FFFFFF',
    'bg.surface': '#F7F8FA',
    'bg.card': '#FAFAFA',
    'bg.cardHover': '#F0F6FC',
    'bg.skeleton': '#ECECEC',
    'bg.skeletonAlt': '#E0E0E0',
    'bg.disabled': '#F5F5F5',

    'border.default': '#E0E0E0',
    'border.strong': '#90CAF9',
    'border.dashed': '#BDBDBD',

    'brand.primary': '#1976D2',
    'brand.primaryHover': '#1565C0',
    'brand.onPrimary': '#FFFFFF',
    'brand.subtle': '#E3F2FD',

    'text.primary': '#212121',
    'text.secondary': '#616161',
    'text.tertiary': '#757575',
    'text.graphic': '#9E9E9E',        # 仅图形，勿承载正文

    'state.error': '#C62828',
    'state.errorBg': '#FDECEA',
    'state.warning': '#B45309',
    'state.warningBg': '#FEF3E2',
    'state.success': '#2E7D32',
    'state.info': '#1B7A4B',          # 继承提示；旧值 #0A5 只有 3.1:1
    'state.infoBg': '#EAF6EE',

    'listbox.selectBg': '#1976D2',
    'listbox.selectFg': '#FFFFFF',

    'scroll.trough': '#F0F0F0',
    'scroll.thumb': '#BDBDBD',
    'scroll.thumbHover': '#9E9E9E',

    # —— 演出预览：它画的是**游戏画面**（不是编辑器界面），所以两套主题
    #    用同一份值（游戏里那块屏幕不跟着编辑器换主题）。放这里是为了
    #    遵守"界面代码不许写死色值"这条契约，而不是因为要分主题。
    'stage.letterbox': '#101418',      # 舞台底（没有背景图时的黑边）
    'stage.placeholder': '#2B3A55',    # 找不到图时的占位块
    'stage.placeholderOutline': '#4A90D9',
    'stage.speaking': '#1B3B6F',       # 正在说话的那张
    'stage.speakingOutline': '#7CC4FF',
    'stage.label': '#8AB4F8',          # 背景 / 图层角标
    'stage.labelAlt': '#C58AF9',
    'stage.text': '#EAEAEA',
    'stage.textDim': '#9AA4B2',
    'stage.dialogueBg': '#1B2230',
    'stage.speakerBg': '#2A3550',
    'stage.warn': '#FFB74D',

    'toast.bg': '#2C2C2C',
    'toast.fg': '#FFFFFF',
}

DARK = {
    'bg.canvas': '#1E1E1E',
    'bg.surface': '#232323',
    'bg.card': '#262626',
    'bg.cardHover': '#2E3A46',
    'bg.skeleton': '#333333',
    'bg.skeletonAlt': '#3D3D3D',
    'bg.disabled': '#2A2A2A',

    'border.default': '#3A3A3A',
    'border.strong': '#64B5F6',
    'border.dashed': '#4A4A4A',

    'brand.primary': '#64B5F6',
    'brand.primaryHover': '#90CAF9',
    'brand.onPrimary': '#102A43',
    'brand.subtle': '#1E3A5F',

    'text.primary': '#E8E8E8',
    'text.secondary': '#ADADAD',
    'text.tertiary': '#9A9A9A',
    'text.graphic': '#7A7A7A',

    'state.error': '#EF9A9A',
    'state.errorBg': '#3A2222',
    'state.warning': '#FFB74D',
    'state.warningBg': '#3A2E1A',
    'state.success': '#81C784',
    'state.info': '#7CB342',
    'state.infoBg': '#22301F',

    'listbox.selectBg': '#2F5F8F',
    'listbox.selectFg': '#FFFFFF',

    'scroll.trough': '#2A2A2A',
    'scroll.thumb': '#4A4A4A',
    'scroll.thumbHover': '#5A5A5A',

    # 演出预览：画的是游戏画面，两套主题共用（见 LIGHT 里的说明）
    'stage.letterbox': '#101418',
    'stage.placeholder': '#2B3A55',
    'stage.placeholderOutline': '#4A90D9',
    'stage.speaking': '#1B3B6F',
    'stage.speakingOutline': '#7CC4FF',
    'stage.label': '#8AB4F8',
    'stage.labelAlt': '#C58AF9',
    'stage.text': '#EAEAEA',
    'stage.textDim': '#9AA4B2',
    'stage.dialogueBg': '#1B2230',
    'stage.speakerBg': '#2A3550',
    'stage.warn': '#FFB74D',

    'toast.bg': '#3C3C3C',
    'toast.fg': '#FFFFFF',
}

THEMES = {'light': LIGHT, 'dark': DARK}

#: 当前主题（`set_theme('dark')` 切换；组件下次重绘时生效）
_current = 'light'


def set_theme(name: str):
    """切换主题。只影响后续取值，不做全局重绘。"""
    global _current
    if name in THEMES:
        _current = name


def theme() -> str:
    return _current


def t(key: str, theme_name: str = None) -> str:
    """
    取色值。缺 key 时返回洋红 `#FF00FF` —— **显眼的失败**，
    好过悄悄用一个看起来正常的颜色把问题藏起来。
    """
    table = THEMES.get(theme_name or _current) or LIGHT
    return table.get(key, '#FF00FF')


# ==================================================================
#  字体（tkinter 用 pt；96dpi 下 1pt ≈ 1.33px）
# ==================================================================

FONTS = {
    #: 8pt ≈ 11px —— **最小字号**（旧代码里的 7pt≈9px 偏小，已取消）
    'caption': ('微软雅黑', 8),
    'captionStrong': ('微软雅黑', 8, 'bold'),
    'body': ('微软雅黑', 9),
    'bodyStrong': ('微软雅黑', 9, 'bold'),
    'section': ('微软雅黑', 10, 'bold'),
    'panelTitle': ('微软雅黑', 12, 'bold'),
    #: 台词比其它输入大一档 —— 它是这张卡的主角
    'text': ('微软雅黑', 10),
    'mono': ('Consolas', 9),
}

# ==================================================================
#  间距 / 圆角 / 动效
# ==================================================================

SPACE = {'xs': 4, 'sm': 8, 'md': 12, 'lg': 16}
RADIUS = {'card': 8, 'control': 4, 'chip': 10}
STROKE = {'hair': 1, 'focus': 2, 'focusOffset': 2}

MOTION = {
    'fast': 150,            # hover / 选中：只改颜色，不动几何
    'skeleton': 400,        # 骨架呼吸间隔
    'skeletonLoops': 3,     # 最多闪 3 次后停在静态
    'toast': 8000,          # Toast 停留 8 秒（可撤销窗口）
    'debounce': 120,        # 缩略图防抖
}

# ==================================================================
#  组件尺寸
# ==================================================================

CARD = {
    'w': 100, 'h': 176,
    'pad': 6,
    'thumbW': 88, 'thumbH': 118,
    'nameH': 18,
    'chipH': 20, 'chipRowH': 22,
    'closeW': 16, 'closeHit': 24,
    'radius': 8,
    'gap': 8,
}
CARD_COMPACT = {
    'w': 76, 'h': 132,
    'pad': 6,
    'thumbW': 64, 'thumbH': 86,
    'nameH': 16,
    'chipH': 18, 'chipRowH': 20,
    'closeW': 14, 'closeHit': 22,
    'radius': 8,
    'gap': 6,
}

CONTROL = {'h': 28, 'minW': 64, 'iconW': 24, 'iconVisual': 16}
TOOLBAR = {'h': 40, 'toastH': 36, 'toastW': 320}
LABEL_W = 72          # 字段标签列宽

#: 滚动容器的尺寸与步长。
#: `unit` 是"滚轮一格的像素数"（`yscrollincrement`）；一次滚 3 格 ≈ 60px，
#: 比"一屏 1/10"的小碎步跟手，又不至于一滚就飞过去。
SCROLL = {'w': 14, 'unit': 20, 'notch': 3}

__all__ = ['LIGHT', 'DARK', 'THEMES', 'set_theme', 'theme', 't',
           'FONTS', 'SPACE', 'RADIUS', 'STROKE', 'MOTION',
           'CARD', 'CARD_COMPACT', 'CONTROL', 'TOOLBAR', 'LABEL_W',
           'SCROLL']
