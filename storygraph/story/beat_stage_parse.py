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
from dataclasses import dataclass

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

def show_part_info(part: str) -> dict:
    """
    一条 `show` 子句（`split_stage_items` 的一段）→ 简单判断。

    返回 `{'part','image','at','kind'}`；`kind` 是：

      · `simple` —— 紧凑 `图@变换` 或纯图名（**能安全改写**）；
      · `complex` —— 含 `behind/with/as/at/onlayer/zorder` 等修饰
        （**拒绝**自动改写：那是"不猜语义"）；
      · `expression` —— `show expression …`（图像名是表达式，拒绝改写）。

    为什么在 `story` 层（2026-10-31）：原来只有 `app.stage._show_part_info`
    一份，用来给"尺寸面板改 at"判断"这一条能不能改"。现在 `story.at_merge`
    （合并回源）要判**同一件事** —— 两份各写一遍的后果不是多几行，而是
    "面板说能改、合并却改不动"这种两个出口两种待遇。所以规则搬到这里，
    `app.stage` 那个私有名转调它（保持老调用点不动）。

    `kind='complex'` 时 `image` 是 `classify_show_words` 认出的目标名
    （认不出就是 `''`）—— 只用来判断"是不是这个对象"，不参与改写。
    """
    from storygraph.core.safeguard import swallow

    part = str(part or '').strip()
    if not part:
        return {}
    # 行尾冒号 = 这一条 show 后面跟着 ATL 块（`split_stage_items` 不拆它，
    # `_split_show_items` 剥下来记在 `block` 上）。这里也剥：留着它的话
    # `@at` 会解析成 `half:` —— 改写时就会吃掉那个冒号，ATL 块变无主缩进，
    # 引擎报 `expects a non-empty block`。剥下来的标记放进 `block`，
    # 改写方（`set_item_at`）负责拼回去。
    block = part.endswith(':')
    if block:
        part = part[:-1].rstrip()
        if not part:
            return {}
    if part.startswith('expression') and (
            len(part) == len('expression')
            or part[len('expression')].isspace()):
        return {'part': part, 'image': '', 'at': '', 'kind': 'expression',
                'block': block}
    words = part.split()
    if any(w in SHOW_CLAUSE_KEYWORDS or w in SHOW_INTRO_KEYWORDS
           for w in words):
        try:
            target = classify_show_words(part, intros=()).target or ''
        except Exception as e:                 # noqa: BLE001 - 认不出就按复杂处理
            swallow('story.beat_stage_parse.show_part_info', e)
            target = ''
        return {'part': part, 'image': str(target), 'at': '',
                'kind': 'complex', 'block': block}
    if '@' in part:
        image, _, at = part.partition('@')
        return {'part': part, 'image': image.strip(), 'at': at.strip(),
                'kind': 'simple', 'block': block}
    return {'part': part, 'image': part, 'at': '', 'kind': 'simple',
            'block': block}


#: `set_item_at` 的结局（机器读；**措辞由调用方给** —— 尺寸面板与合并桥
#: 面对的是同一件事，但两处要说的话不一样）。
AT_OK = 'ok'
#: 这一处本来就写着这个值（不改，不算失败）
AT_NOOP = 'noop'
#: 这一拍没有这个对象（调用方该说"这里没有它"）
AT_MISSING = 'missing'
#: 复杂子句（含 `behind/with/as/at`…）：不猜语义
AT_COMPLEX = 'complex'
#: `show expression …`：图像名是表达式，分不出边界
AT_EXPRESSION = 'expression'
#: 拍级 `at` / `transform` 会盖住这一条 —— 只改这一条改不动它
AT_BLOCKED = 'blocked'


@dataclass(frozen=True)
class ItemAtRewrite:
    """`set_item_at` 的结果（`status` 见上面的常量）。"""

    status: str = AT_MISSING
    #: 改写后的整个 `show` 字段（不是 `AT_OK` 时**原样返回**原文）
    text: str = ''
    #: 这一处**原本**生效的 at（`AT_OK` / `AT_NOOP` 时有效）
    before_at: str = ''
    #: 命中的是第几条子句（`AT_MISSING` 时 -1）
    index: int = -1

    @property
    def ok(self) -> bool:
        return self.status in (AT_OK, AT_NOOP)

    @property
    def changed(self) -> bool:
        return self.status == AT_OK


def set_item_at(text: str, tag: str, at: str, beat=None) -> ItemAtRewrite:
    """
    把 `show` 字段里**这一条**的 `@at` 改成 `at`（`at=''` = 去掉 at）。

    这是"改一条 show 的变换"的**唯一实现**（2026-10-31）：尺寸面板改源、
    合并回源改那几处、以后别的入口，走的都是它 —— 因为"哪一条算数"这件事
    只许有一份答案：

      · 同一个 tag 写了多条时，**最后一条**算数（Ren'Py 侧就是后 show 的赢）；
      · `kind='complex'` / `expression` 一条都不改（返回对应 `status`）。

    与 `app.stage._bg_show_edit` / `_layer_show_edit` 的关系：那两个还负责
    "没有这条时**补一条**"（`show bg X` 的「从这一拍起」模式），补条目的
    逻辑留在这里不做 —— 这个函数**只改已有的，绝不新增/删除子句**
    （返回 `AT_MISSING` 就是没有）。合并回源要的正是这条保证：它是"把
    写法对齐"，不是"给用户加语句"。

    ⚠ 拍级 `at` / `transform`（Stage 高级区里那一列）与 item 的 at 是
    **叠加**关系（`item_at` 的优先级）：item 自己没写 at 时，拍级的值会
    继续生效 —— 要"去掉 at"时只改 item 是改不动的（返回 `AT_BLOCKED`），
    决不含糊地报成功。要写入一个值则不受影响（item 的 `@at` 压过拍级）。

    `beat` 是可选的：给了才做上面那条拍级检查（不给就只能判 item 自己
    那一层 —— 单测与"只看字符串"的调用方不需要 Beat）。
    """
    raw = str(text or '')
    tag = str(tag or '').strip()
    at = str(at or '').strip()
    parts = list(split_stage_items(raw))
    if not tag or not parts:
        return ItemAtRewrite(status=AT_MISSING, text=raw)
    hit = -1
    before_at = ''
    block = False
    for i, part in enumerate(parts):
        info = show_part_info(part)
        words = str(info.get('image') or '').split()
        if not words or words[0] != tag:
            continue
        if info.get('kind') == 'expression':
            return ItemAtRewrite(status=AT_EXPRESSION, text=raw)
        if info.get('kind') == 'complex':
            return ItemAtRewrite(status=AT_COMPLEX, text=raw)
        hit = i
        before_at = str(info.get('at') or '').strip()
        block = bool(info.get('block'))
    if hit < 0:
        return ItemAtRewrite(status=AT_MISSING, text=raw)
    if beat is not None and not before_at and not at:
        # 要的是"原样"（去掉 at），可这条 item 本来就没写 at —— 拍级
        # `at` / `transform` 会顶上（`item_at` 的优先级）。这种情况**只改
        # item 改不动**，报 `AT_BLOCKED`；拍级有值却报 noop 就是撒谎。
        #
        # 反过来（要写入一个值）不受影响：item 自己写了 `@at` 就压过拍级。
        level = (str(getattr(beat, 'at', '') or '').strip()
                 or str(getattr(beat, 'transform', '') or '').strip())
        if level:
            return ItemAtRewrite(status=AT_BLOCKED, text=raw,
                                 before_at=level, index=hit)
    if before_at == at:
        return ItemAtRewrite(status=AT_NOOP, text=raw, before_at=before_at,
                             index=hit)
    part = parts[hit]
    body = part[:-1].rstrip() if block else part.strip()
    base = body.rpartition('@')[0].rstrip() if '@' in body else body.strip()
    new_part = (f'{base}@{at}' if at else base) + (':' if block else '')
    new_text = '; '.join([*parts[:hit], new_part, *parts[hit + 1:]])
    return ItemAtRewrite(status=AT_OK, text=new_text, before_at=before_at,
                         index=hit)


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
