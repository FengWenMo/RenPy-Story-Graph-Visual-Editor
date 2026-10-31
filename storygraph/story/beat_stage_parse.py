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

from storygraph.core.vocabulary import (SHOW_CLAUSE_KEYWORDS,
                                        SHOW_INTRO_KEYWORDS,
                                        classify_show_words,
                                        split_stage_items)

_NATIVE_TAIL_RE = re.compile(
    r'^(?P<spec>.+?)(?:\s+at\s+(?P<at>[\w.]+))?'
    r'(?:\s+with\s+(?P<with>[\w.]+))?$')

#: `show` 片段里"这段话里有没有子句/引入型关键字"的**边界集**
#: （唯一真源在 `core.vocabulary`）—— `_split_show_items` 用它做分支判断。
#: ⚠ 2026-10-16：切分本体已经交给 `classify_show_words`；
#:   `_SHOW_ARG_KEYWORDS` 那个"同一份集合的第二个名字"**已删**。
_SHOW_CHUNK_WORDS = SHOW_CLAUSE_KEYWORDS | SHOW_INTRO_KEYWORDS


def _clause_image(clause: str) -> str:
    """从一个 show 子句（已去掉前导 'show '）里取出**图像名**。

    ⚠⚠ 2026-10-16：**切分本体交给 `core.vocabulary.classify_show_words`** ——
    从前这里自己写了一遍"遇到关键字就 break"的循环，与 `renpy_parser` /
    `excel_export` / `asset_registry` / `learn_statement` 各写一遍是同一个
    口径的六份拷贝（用户排期 C 项）。

    ⚠ `intros=()` 是**刻意**的（口径与从前逐字相同）：本函数切的是
    "舞台片段"（`beat.show` 那一格，可能是 `screen msg` 这种写法），
    不是"资产引用"。引入型关键字（`screen` / `layer` / `expression`）
    只有在**资产那一侧**才表示"没有图像名"（`asset_registry` 那站自己看
    `intro_kind`）；这里照旧把 `screen msg` 整段当一个名字，避免悄悄改口径。
    """
    return classify_show_words(clause, intros=()).target


def _clause_value_text(value) -> str:
    """
    `classify_show_words` 的一个子句值 → item 里的**字符串**字段。

    ⚠ `at` / `behind` 分类器给的是 **list**（多目标：`at a, b` / `behind x, y`），
      其余子句（`with` / `as` / `onlayer` / `zorder`）给 str。
      多目标之间用 `', '` 连接 —— Ren'Py 的 `at` 是**逗号分隔**的变换列表，
      空格连接会少掉语义（与 `beat_model._join_at_targets` 同一条规矩）。
      单目标 / 缺省统一返回 `''`，老调用方的 `.strip()` 不会炸。
    """
    if isinstance(value, (list, tuple)):
        return ', '.join(str(v).strip() for v in value if str(v).strip())
    return str(value or '').strip()


def _clause_values(part: str) -> dict:
    """
    复杂子句里**结构化列承载得了**的那几个值（`at` / `with` / `as` / `behind`）。

    为什么要有（2026-10-30，P1）：`stage_snapshot._apply_show_verb` 只认 item
    的 `at`；从前 clause 分支的 `at` 恒为 `''`，于是
    `show cg cg02 at cg38402160 behind ye` 这类复杂子句的变换**整条丢失**
    （真机 `sr_general` 实测 23 帧受影响，立绘/CG 只能按原图贴左上）。

    ⚠ **不另写切分**：切分本体仍是 `core.vocabulary.classify_show_words`
      （与 `_clause_image` 同一个 `intros=()` 口径，所以 `screen msg` 照旧
      整段算图像名）。这里只把它的返回形状统一成 item 的字符串字段。
    """
    clauses = classify_show_words(part, intros=()).clauses
    return {
        'at': _clause_value_text(clauses.get('at')),
        'with_': _clause_value_text(clauses.get('with')),
        'as_': _clause_value_text(clauses.get('as')),
        'behind': _clause_value_text(clauses.get('behind')),
    }


def _split_show_items(text: str) -> list:
    """
    `bg@left; eileen happy; cg cg02 at t behind ye`
      -> [{'clause': '', 'image': 'bg', 'at': 'left'},
          {'clause': '', 'image': 'eileen happy', 'at': ''},
          {'clause': 'cg cg02 at t behind ye', 'image': 'cg cg02', 'at': 't',
           'with_': '', 'as_': '', 'behind': 'ye'}]

    只认 `图像@变换`（紧凑式）与"含修饰关键字的完整子句"两种；其余当纯图像名。

    ⚠ 复杂子句的 `clause` **仍然是原文**（渲染/去重的既有契约：整条归
      「舞台（高级）」列，见 `beat_model.show_item_part` / `show_cell_parts`）；
      `at` / `with_` / `as_` / `behind` 只是**另外**填给舞台快照看的结构化值
      （2026-10-30 修：从前 `at` 恒为空，`show … at … behind …` 的变换丢失）。

    **`expression` 形态单独走一条路**（2026-10-05 修）：`show expression`
    的图像名本身是个 Python 表达式（`holo("lan base")`），里面可能带空格、
    括号、引号，所以 `@位置` 那种紧凑编码**表达不了**它 —— 必须像别的
    修饰符一样解析成 `{expression, image, at, as, with}`，否则位置信息
    （`at i11`）会在写表时丢掉（真实项目 `script.rpy` 实测 5 条）。
    """
    items = []
    for part in split_stage_items(text):
        part = part.strip()
        if not part:
            continue
        # **行尾冒号 = 这一条 show 后面跟着 ATL 块**（2026-10-31）。
        #
        # 冒号不能留在 `at` 值里：`at right:` 会让舞台几何去找一个叫
        # `right:` 的变换（永远找不到），预览就静默丢掉这个位置。
        # 但也不能扔：它是"后面有块"的语法标记，扔了导出就少一个冒号，
        # 块体变成无主缩进，引擎报 `expects a non-empty block`。
        # 所以**剥下来单独记在 `block` 上**，`show_item_part` 再拼回去 ——
        # 单元格里那个字符串因此**往返保真**（写出去什么样，读回来什么样）。
        block = part.endswith(':')
        if block:
            part = part[:-1].rstrip()
            if not part:
                continue
        if part.startswith('expression') and (
                part == 'expression' or part[len('expression')].isspace()):
            item = _parse_show_expression(part)
        elif '@' in part:
            image, _, at = part.partition('@')
            image = image.strip()
            # `expression X@at` 进来时（`show_item_part` 的编码）走表达式的路
            if image.startswith('expression') and (
                    image == 'expression'
                    or image[len('expression')].isspace()):
                item = _parse_show_expression(image)
                item['at'] = at.strip()
            else:
                item = {'clause': '', 'image': image, 'at': at.strip()}
        elif any(w in _SHOW_CHUNK_WORDS for w in part.split()):
            item = {'clause': part, 'image': _clause_image(part),
                    **_clause_values(part)}
        else:
            item = {'clause': '', 'image': part, 'at': ''}
        if block:
            item['block'] = True
        items.append(item)
    return items


def item_at(item, beat=None) -> str:
    """
    一条 show item **真正生效**的 `at`（空 = 这一条不带变换）。

    优先级（Ren'Py 侧就是这么叠加的，也是 `stage_snapshot` 一直用的口径）：

        item 自己的 `at`  >  拍级 `at`  >  拍级 `transform`

    为什么单独抽出来（2026-10-31）：`stage_snapshot._apply_show_verb` 内联写过
    一遍，`story.override_advice` 要数"哪几处单独改了尺寸"又需要同一份答案。
    两份各算一遍的后果不是"多一行代码"，而是**数出来的处数和实际会盖回去的
    处数不一致** —— 报告里的数字必须和写入动的是同一批行
    （`docs/超清CG与BG_零侵入导入_2026-10-31.md` §5.5 最后一条判据）。

    `beat=None` 时只认 item 自己那一层（纯解析用）。
    """
    at = str((item or {}).get('at') or '').strip()
    if at or beat is None:
        return at
    return (str(getattr(beat, 'at', '') or '').strip()
            or str(getattr(beat, 'transform', '') or '').strip())

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
    for part in split_stage_items(text):
        part = part.strip()
        if not part:
            continue
        if '@' in part:
            image, _, w = part.partition('@')
            items.append({'image': image.strip(), 'with': w.strip()})
        else:
            items.append({'image': part, 'with': ''})
    return items
