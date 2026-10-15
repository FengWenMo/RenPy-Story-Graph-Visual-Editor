"""
`beat_model` 的**立绘 / 舞台解析族**（2026-10-12 从 `beat_model.py` 搬出）
========================================================================

为什么单独一个模块：`beat_model.py` 3115 行、顶层函数 75 个 / 1591 行。
按 `.pip-tmp/family_scan.py` 量的族闭包，立绘/舞台这一族与其它族交叉最少：

    show/stage  初选 13 → 闭包 16 个 / 302 行   ← 先切它
    code        初选 13 → 闭包 20 个 / 482 行
    audio       初选  8 → 闭包  9 个 / 137 行

⚠ `beat_render_code` 引用的 `_split_show_items` / `_split_hide_items` /
`show_item_part` 等正好在这一族里 —— 搬完由 `beat_model` **转出**，
所以引用方一行都不用改。

⚠ 搬家纪律：**逐字照搬**（连注释与空行）—— `git diff` 一眼能看出"只是搬家"。
"""

from __future__ import annotations

import re

from storygraph.core.vocabulary import SHOW_CLAUSE_KEYWORDS

_NATIVE_TAIL_RE = re.compile(
    r'^(?P<spec>.+?)(?:\s+at\s+(?P<at>[\w.]+))?'
    r'(?:\s+with\s+(?P<with>[\w.]+))?$')

#: 图像名**到哪里为止** —— **唯一真源**在 `core.vocabulary`（2026-10-15 收重复）。
_SHOW_ARG_KEYWORDS = SHOW_CLAUSE_KEYWORDS

def _clause_image(clause: str) -> str:
    """从一个 show 子句（已去掉前导 'show '）里取出图像名。"""
    parts = []
    for w in clause.split():
        if w in _SHOW_ARG_KEYWORDS:
            break
        parts.append(w.rstrip(','))
    return ' '.join(parts)

def _split_show_items(text: str) -> list:
    """
    `bg@left; eileen happy; cg cg02 at t behind ye`
      -> [{'clause': '', 'image': 'bg', 'at': 'left'},
          {'clause': '', 'image': 'eileen happy', 'at': ''},
          {'clause': 'cg cg02 at t behind ye', 'image': 'cg cg02', 'at': ''}]

    只认 `图像@变换`（紧凑式）与"含修饰关键字的完整子句"两种；其余当纯图像名。

    **`expression` 形态单独走一条路**（2026-10-05 修）：`show expression`
    的图像名本身是个 Python 表达式（`holo("lan base")`），里面可能带空格、
    括号、引号，所以 `@位置` 那种紧凑编码**表达不了**它 —— 必须像别的
    修饰符一样解析成 `{expression, image, at, as, with}`，否则位置信息
    （`at i11`）会在写表时丢掉（真实项目 `script.rpy` 实测 5 条）。
    """
    items = []
    for part in (text or '').split(';'):
        part = part.strip()
        if not part:
            continue
        if part.startswith('expression') and (
                part == 'expression' or part[len('expression')].isspace()):
            items.append(_parse_show_expression(part))
        elif '@' in part:
            image, _, at = part.partition('@')
            image = image.strip()
            # `expression X@at` 进来时（`show_item_part` 的编码）走表达式的路
            if image.startswith('expression') and (
                    image == 'expression'
                    or image[len('expression')].isspace()):
                item = _parse_show_expression(image)
                item['at'] = at.strip()
                items.append(item)
                continue
            items.append({'clause': '', 'image': image, 'at': at.strip()})
        elif any(w in _SHOW_ARG_KEYWORDS for w in part.split()):
            items.append({'clause': part, 'image': _clause_image(part),
                          'at': ''})
        else:
            items.append({'clause': '', 'image': part, 'at': ''})
    return items

def _parse_show_expression(part: str) -> dict:
    """
    `expression holo("lan base") at i11 as qing with dissolve`
      → {clause: '', is_expression: True, image: 'holo("lan base")',
         at: 'i11', as_: 'qing', with_: 'dissolve'}

    关键词的**识别要跳过引号**：表达式里完全可以出现 `at` / `as` 这样的词
    （`Text("at as")`），按空格硬切会切错。找不到关键词时整个剩余部分
    就是表达式本体。
    """
    rest = part[len('expression'):].strip()
    at = as_ = with_ = ''
    spec_chars = []
    i = 0
    n = len(rest)
    while i < n:
        ch = rest[i]
        if ch in '"\'':
            # 跳过整个字符串字面量（含转义）
            j = i + 1
            while j < n:
                if rest[j] == '\\':
                    j += 2
                    continue
                if rest[j] == ch:
                    break
                j += 1
            spec_chars.append(rest[i:j + 1])
            i = j + 1
            continue
        if ch == ' ':
            m = re.match(r'\s+(at|as|with)\s+', rest[i:])
            if m:
                word = m.group(1)
                i += m.end()
                j = i
                while j < n and not rest[j].isspace():
                    j += 1
                value = rest[i:j]
                i = j
                if word == 'at' and not at:
                    at = value
                elif word == 'as' and not as_:
                    as_ = value
                elif word == 'with' and not with_:
                    with_ = value
                continue
            spec_chars.append(ch)
            i += 1
            continue
        spec_chars.append(ch)
        i += 1
    return {
        'clause': '',
        'is_expression': True,
        'image': ''.join(spec_chars).strip(),
        'at': at,
        'as_': as_,
        'with_': with_,
    }

def _split_hide_items(text: str) -> list:
    """`ye; lan@dissolve` -> [{'image': 'ye', 'with': ''},
                             {'image': 'lan', 'with': 'dissolve'}]"""
    items = []
    for part in (text or '').split(';'):
        part = part.strip()
        if not part:
            continue
        if '@' in part:
            image, _, w = part.partition('@')
            items.append({'image': image.strip(), 'with': w.strip()})
        else:
            items.append({'image': part, 'with': ''})
    return items
