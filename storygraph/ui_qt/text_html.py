"""
storygraph.ui_qt.text_html
==========================
Ren'Py 台词 →（预览用）HTML：**一份**转换器。

为什么单独一个模块（2026-11-01 解导入环）：`stage_preview` 拍预览图时要用
同一个转换器（"与卡片预览**同一份**"），从前它是函数内
`from storygraph.ui_qt.main_window import renpy_text_to_html` —— 而
`main_window` 顶层导入 `stage_thumb`、`stage_thumb` 顶层导入 `stage_preview`，
于是 `main_window → stage_thumb → stage_preview → main_window` 成一个环。
这个转换器只依赖 `re`，搬到叶子模块后环就断了。

`main_window` 仍然 re-export 这个名字（老调用点与测试都不用动）。
"""

from __future__ import annotations

import re

#: Ren'Py 文字标签：`{color=#f00}` / `{/color}` / `{i}` / `{w=0.5}` …
_RENPY_TAG_RE = re.compile(r'\{([^{}]*)\}')
#: 预览认得的颜色值（只放行"看着像颜色"的东西 —— 预览是 HTML）
_PREVIEW_COLOR_RE = re.compile(r'^(?:#[0-9a-fA-F]{3,8}|rgba?\([\d\s.,%]+\))$')


def renpy_text_to_html(text: str) -> tuple:
    """
    Ren'Py 台词 →（预览用 HTML，是否含标签）。

    只认最常用的几种：`color` / `b` / `i` / `u` / 绝对 `size`。
    其余标签（`{w}` / `{p}` / `{nw}` / `{alpha=…}`…）**剥掉不显示**
    （它们不改变"截一张静态图看到的样子"），`[变量]` 原样留字。
    先转义再拼标签：文本里出现 `<` 也只是普通字符。
    """
    from html import escape as _esc

    src = str(text or '')
    if '{' not in src:
        return _esc(src), False
    out = []
    pos = 0
    has_tag = False
    for m in _RENPY_TAG_RE.finditer(src):
        out.append(_esc(src[pos:m.start()]))
        pos = m.end()
        tag = m.group(1).strip()
        if not tag:
            continue
        closing = tag.startswith('/')
        name = (tag[1:] if closing else tag).strip().lower()
        base = name.split('=', 1)[0].strip()
        if base in ('b', 'i', 'u'):
            out.append(f'</{base}>' if closing else f'<{base}>')
            has_tag = True
        elif base == 'color':
            if closing:
                out.append('</span>')
            else:
                val = name.split('=', 1)[1].strip().strip('"\'')
                if _PREVIEW_COLOR_RE.match(val):
                    out.append(f'<span style="color:{val}">')
                # 颜色不是合法值 → 不产出 style（但标签本身算数：
                # 预览会出来，让人看得见"这句带样式"，而 Ren'Py 不报错）
                has_tag = True
        elif base == 'size':
            if closing:
                out.append('</span>')
            else:
                val = name.split('=', 1)[1].strip()
                if val.isdigit():
                    out.append(f'<span style="font-size:{int(val)}px">')
                has_tag = True
    out.append(_esc(src[pos:]))
    return ''.join(out), has_tag


__all__ = ['renpy_text_to_html']
