"""
storygraph.story.patcher
========================
**增量导出（受控补丁）**：只替换真正改过的行，其余**逐字节保留**。

用户 2026-09-28 的决定：

> 对原有的程序做增量的修改，而不完全生成一个新的程序，会不会更好呢？
> ……保留注释，全量修改出来的东西，根本就没办法看！！！

为什么必须要它（用用户的真实两章算出来的）：

    原文 571 行 → 全量产物 602 行，相似度 0.8764，76 个差异块
    丢 6 条 `stop music fadeout 1.0`、缺 5 条 play music、多 3+5+2 条 hide
    文件头注释 `# 1.3通过考验` 消失、transform 段重复两遍

全量重生成的代价是**结构性的**：它只写自己认识的东西，
原文的注释、写法、顺序一律重排。增量补丁则反过来 ——
**没动的行一个字节都不变**。


重写说明（2026-09-30）：这一版把"能跑"改成"**要么全对，要么完全不动**"
------------------------------------------------------------------------
上一版实测出了 5 个问题（真实 chapter1_3 + 合成脚本量出来的）：

  A 不改任何东西，产物居然 ≠ 原文（少 2 个空行、挪 1 行、掉 1 个行尾空格）
  B 只改一句台词，却改动了 5 行
  C 带 `if` / `menu` 的脚本：**缩进被压平 → 语法坏掉**
  D 区间错位（源文件被手工改过）**照样写入**，259 行被改写
  E Excel 导出路径拿不到行号 → 覆盖率 0%，功能全程是死的

根因与对策（每条都对应上面一个字母）：

  ① 替换行来自"**无符号表的单拍重渲染**"（与真实产物写法不一致，
     甚至会把 `ling happy` 写成中文 tag）→ 现在**一律用真实产物**
     （`render_beat_to_rpy` + 与全量渲染**同一套** registry/symbols）。
  ② 缩进靠"区间第一行"猜，嵌套块必错 → 现在**缩进只来自原文**：
     替换一行 = 保留原文那一行的缩进 + 换成生成器的**内容**；
     插行用**相邻原文行**的缩进。**缩进是结构，补丁不许碰。**
  ③ 没有校验 → 现在补丁后**重新解析**，确认"每拍该有的台词都在"
     且 `label` / `return` 数量没少；不过 → `ok=False`，
     调用方退回全量（**绝不写半对的产物**）。
  ④ 没有对位守卫 → 现在替换前先确认"原文这一段确实属于这一拍"
     （看得到它的台词/说话人/图像名），对不上就跳过、不猜。
  ⑤ 行号传不过来 → 新增 `recover_source_ranges()`：**解析源 .rpy，
     按内容把行号找回给表格拍**（只读源文件、不动表结构）。

另外：`改动行数` 现在只统计**内容真的变了**的行（只剩空格差异的算没改），
这样"什么都不改 → 产物逐字节等于原文"这条契约才成立。
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PatchResult:
    """一次增量导出的结果。"""

    text: str
    source_path: Path
    matched_beats: int          # 成功对上源行的拍数
    total_beats: int
    changed_lines: int          # 实际被替换的行数
    #: 区间对不上（源文件被手工改过 / 行号失效）→ 跳过、不猜
    stale_beats: int = 0
    #: 生成器产出了、但没能在这次替换里落地的行（注释/代码/结构行）
    unlanded_lines: int = 0
    #: 表里删掉、源文件里还留着的拍（删行这一轮不支持 → 直接退回全量）
    dropped_beats: int = 0
    #: 校验不通过的原因（空 = 通过）。**非空时 `text` 不可用。**
    reasons: list = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return (self.matched_beats / self.total_beats) if self.total_beats else 0.0

    @property
    def ok(self) -> bool:
        """
        只有"对位够 + 校验通过"才算真的走了增量。

        覆盖率的门槛是 0.9（低于这个数说明源文件与表对不上，
        退化成全量更稳）；**校验必须通过**，否则一律退回全量 ——
        宁可丢注释，也不能写出半对的脚本。
        """
        if not self.source_path or self.reasons:
            return False
        return self.coverage >= 0.9


def _body(line: str) -> str:
    """去掉缩进。"缩进属于结构，补丁不碰"，所以比对一律在去缩进后做。"""
    return (line or '').lstrip(' \t')


def _indent_of(line: str) -> str:
    line = line or ''
    return line[:len(line) - len(line.lstrip(' \t'))]


def _norm(line: str) -> str:
    """比对用的规范形式：去缩进 + 去行尾空白。

    行尾多一个空格（原文常见）不算"改动"——否则"什么都不改"也会
    判成改动，逐字节保真的契约就不成立了。
    """
    return _body(line).rstrip()


#: **绝不允许被补丁删掉**的行：控制流与结构。
#: 它们不属于任何一拍（`return` 由 label 级渲染负责，`label`/`if`/`menu`
#: 是结构），生成器的"这一拍的输出"里天然没有它们。
_PROTECTED_RE = re.compile(
    r'^\s*(return|label\s|jump\s|call\s|menu\s*:|if\s|elif\s|else\s*:'
    r'|while\s|for\s|pass\s*$|translate\s|init\b|define\s|default\s)')


def _is_protected(line: str) -> bool:
    return bool(_PROTECTED_RE.match(line or ''))


# ==================================================================
#  一、把行号找回给"表格拍"（表格里没有行号）
# ==================================================================

def _stage_tokens(beat) -> tuple:
    """这一拍的舞台"指纹"：图像名/退场名/背景。**不含台词正文**。"""
    out = []
    for raw in (getattr(beat, 'show', '') or '').split(';'):
        raw = raw.split('@')[0].strip()
        if raw:
            out.append(raw)
    for raw in (getattr(beat, 'hide', '') or '').split(';'):
        raw = raw.split('@')[0].strip()
        if raw:
            out.append('hide:' + raw)
    scene = (getattr(beat, 'scene', '') or '').strip()
    if scene:
        out.append('scene:' + scene)
    return tuple(sorted(out))


def _align_key(beat) -> tuple:
    """
    对齐用的身份键。**刻意不含台词正文** —— 正文正是会被编辑的东西。

    留着的是低翻车率的特征：章节 / 类型 / 说话人 / 舞台指纹。
    纯旁白拍（都一样）依赖**顺序**对齐，那是对的：顺序没变时，
    换掉的只是正文，正是我们要做的事。
    """
    return (str(getattr(beat, 'label', '') or ''),
            str(getattr(beat, 'kind', '') or ''),
            str(getattr(beat, 'speaker', '') or '').strip(),
            _stage_tokens(beat))


def recover_source_ranges(beats: list, source_path) -> tuple:
    """
    给**没有行号**的拍找回源码区间（就地写回 `src_first` / `src_last`）。

    为什么需要：`src_first/src_last` 是运行期元数据（不入 Excel），
    而"剧本表 → 导出 .rpy"这条路上，拍是从表里读出来的 → 行号全是 0 →
    增量补丁永远对不上位、永远退化（实测覆盖率 0%）。

    做法：**解析源 .rpy**，按 `label` 分组后做序列对齐（`difflib`），
    对齐上的直接把源拍的行号搬过来。这样

      · 表格里改了台词 → 行号照旧（正文不属于对齐键）
      · 表格里插了一拍 → 给一个**插入点**（前一拍的末行），位置是对的
      · 表格里删了一拍 → 记进"dropped"，让调用方退回全量（本轮不支持删行）

    只读源文件，**不改表结构**。返回 `(对上的拍数, 总拍数, 被删掉的拍数)`。
    """
    src = Path(source_path) if source_path else None
    if src is None or not src.exists() or not beats:
        return 0, len(beats or []), 0
    try:
        from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder
        from storygraph.story.beat_build import beats_from_parse_result
        parsed = ParseResultBuilder().build(RenPyParser().parse(src))
        src_beats = beats_from_parse_result(parsed)
    except Exception:                        # noqa: BLE001 - 解析失败就不找
        return 0, len(beats), 0

    # 按 label 分组（保持顺序）：跨 label 对齐没有意义，也对不准
    def group_by_label(items):
        order, buckets = [], {}
        for b in items:
            name = str(getattr(b, 'label', '') or '')
            if name not in buckets:
                buckets[name] = []
                order.append(name)
            buckets[name].append(b)
        return order, buckets

    _, new_groups = group_by_label(beats)
    _, src_groups = group_by_label(src_beats)

    matched = 0
    dropped: list = []
    for name, group in new_groups.items():
        pool = src_groups.get(name) or []
        if not pool:
            continue
        sm = difflib.SequenceMatcher(
            None, [_align_key(b) for b in pool],
            [_align_key(b) for b in group], autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == 'equal':
                for k in range(i2 - i1):
                    _apply_range(group[j1 + k], pool[i1 + k])
                    matched += 1
            elif tag == 'replace':
                # 成对的按位置给行号（正文改了、但对齐键相同的情况会走 equal；
                # 走到这里多半是说话人/舞台也动了 —— 按顺序硬配，
                # 后面的"对位守卫"与"整体校验"会把配错的挡下来）
                pair = min(i2 - i1, j2 - j1)
                for k in range(pair):
                    _apply_range(group[j1 + k], pool[i1 + k])
                    matched += 1
                for k in range(j1 + pair, j2):
                    _set_insert_point(group[k], pool[i2 - 1] if i2 > i1 else None)
            elif tag == 'insert':
                prev = pool[i1 - 1] if i1 > 0 else None
                nxt = pool[i1] if i1 < len(pool) else None
                for k in range(j1, j2):
                    _set_insert_point(group[k], prev, nxt)
            elif tag == 'delete':
                # 源文件里有、表里没有 → 用户把这一拍删了。
                # 本轮**不支持删行**，所以一个都不能少：如实报出来，
                # 让调用方退回全量（全量产物才是"表说了算"的那一份）。
                dropped.append(name)
    return matched, len(beats), len(dropped)


def _apply_range(beat, src_beat):
    beat.src_first = int(getattr(src_beat, 'src_first', 0) or 0)
    beat.src_last = int(getattr(src_beat, 'src_last', 0) or 0)


def _set_insert_point(beat, prev_src, nxt_src=None):
    """
    表格里新增的拍：给一个**插入点**（`src_first == src_last`）。

    插在"前一拍的末行"之后；前面没有就插在"后一拍的起始行"之前。
    这样新写的台词落在**正确的位置**，而不是被扔到文件末尾。
    """
    point = 0
    if prev_src is not None and getattr(prev_src, 'src_last', 0):
        point = int(prev_src.src_last)
    elif nxt_src is not None and getattr(nxt_src, 'src_first', 0):
        point = int(nxt_src.src_first) - 1
    beat.src_first = point
    beat.src_last = point


# ==================================================================
#  二、对位守卫 + **一对一**行内替换（行数永不变化）
# ==================================================================

#: 台词行：`说话人 "正文"`（说话人可缺，那就是旁白）
_SAY_RE = re.compile(r'^(?P<who>[A-Za-z_][\w.]*)?\s*"(?P<body>.*)"$')
#: 舞台/音频行：动词 + 目标
_VERB_RE = re.compile(
    r'^(?P<verb>show|hide|scene|play|stop|queue)\s+(?P<rest>.*)$')
_CHAN_RE = re.compile(r'^(?P<chan>music|sound|voice)\b')

_STAGE_VERBS = ('show', 'hide', 'scene')


def _parse_stage_body(body: str) -> tuple:
    """`show ling happy at left` 的 body → `(spec, at)`（spec = 图像名）。"""
    from storygraph.story.beat_model import _parse_native_stage_tail
    m = _VERB_RE.match(body.strip())
    if not m:
        return '', '', ''
    rest = m.group('rest').strip()
    if m.group('verb') in _STAGE_VERBS:
        # `show` / `hide` / `scene` 后面就是图像名（可能带 at/with）
        if m.group('verb') == 'scene':
            spec, at, with_ = _parse_native_stage_tail(rest)
            return spec, at, with_
        spec, at, with_ = _parse_native_stage_tail(rest)
        return spec, at, with_
    return rest, '', ''


def _line_key(body: str) -> tuple:
    """
    一行的**身份**：只有身份相同的两行才允许互相替换。

    这是"不改坏脚本"的核心。拍内行序会被生成器重排、写法会被规范化，
    所以**不能整段替换**（实测：整段替换会多出 24 行、或把 `if` 体缩进压平）。
    改成"按身份配对"之后：

      · 台词配台词，而且**说话人必须一样**（`lyj "…"` 不会去配 `jc "…"`）
      · `show ling …` 配 `show ling …`（tag 一样），`hide ye` 配 `hide ye`
      · `play music …` 配 `play music …`
      · 其余（注释 / 代码 / `if` / 菜单 / 未识别）一律 **`('other', 原文)`**：
        只和自己逐字相同的行配对 → **不会被替换**，也就不会被改坏

    认不出的一律保守（配对失败 = 原文一个字不动）。
    """
    s = (body or '').strip()
    m = _SAY_RE.match(s)
    if m:
        return ('say', (m.group('who') or '').strip())
    m = _VERB_RE.match(s)
    if m:
        verb, rest = m.group('verb'), m.group('rest')
        if verb in _STAGE_VERBS:
            spec = _parse_stage_body(s)[0]
            return (verb, spec.split()[0] if spec.split() else '')
        chan = _CHAN_RE.match(rest.strip())
        return (verb, chan.group('chan') if chan else rest.strip())
    return ('other', s)


def _same_thing_unless_rewritten(old_body: str, new_body: str) -> bool:
    """
    内容不同时，这一行**允许被替换**吗？

    只放行"同一件事、参数变了"：

      · 台词：说话人一样 → 可以换正文（这就是"改台词"）
      · 立绘：tag 一样、且**图像名的词数一致** → 可以换属性/位置
        （词数不一致说明生成器把 `ling close_happy` 改写成了
         `ling_close_happy` —— 那是"把能跑的脚本改坏"，**不许换**）
      · 其余：不换
    """
    kind = _line_key(old_body)[0]
    if kind == 'say':
        return _line_key(old_body) == _line_key(new_body)
    if kind in _STAGE_VERBS:
        if _line_key(old_body) != _line_key(new_body):
            return False
        spec_old, _a, _w = _parse_stage_body(old_body)
        spec_new, _a2, _w2 = _parse_stage_body(new_body)
        tok_old, tok_new = spec_old.split(), spec_new.split()
        if tok_old == tok_new:
            return True                      # 图像名没变，只是属性/位置变了
        # 词数差 1 且逐词下划线压缩 → 是生成器的"规范化"，不是作者的改动
        return len(tok_old) == len(tok_new)
    return False


def _range_looks_like(old_lines: list, beat) -> bool:
    """
    原文这一段**确实属于这一拍**吗？

    这是"源文件被手工改过 → 行号失效"的守卫。没有它，错位的补丁会
    直接写到别人的行上（实测：整体下移 1 行，259 行被改写）。

    判据（满足一条即可）：台词在；说话人在；舞台指纹里有一个图像名在。
    什么都没得比（既没台词也没舞台）→ 放行（至少不比没有守卫更差）。
    """
    blob = '\n'.join(old_lines or [])
    if not blob.strip():
        return True                          # 空区间 = 新增拍的插入点
    text = (getattr(beat, 'text', '') or '').strip()
    if text and text in blob:
        return True
    speaker = (getattr(beat, 'speaker', '') or '').strip()
    if speaker and re.search(rf'(?<![\w]){re.escape(speaker)}(?![\w])', blob):
        return True
    for token in _stage_tokens(beat):
        name = token.split(':', 1)[-1]
        if name and name in blob:
            return True
    if not text and not _stage_tokens(beat):
        return True
    return False


def _replace_matched(old_lines: list, gen_bodies: list) -> tuple:
    """
    原文区间 → 新区间：**只做一对一替换，行数永不变化**。

    返回 `(新行列表, 真正改动数, 未落地的生成行)`。

    为什么行长不变是最重要的不变量（2026-09-30 实测）：
    只要允许"插入/删除"，缩进、空行、结构就全会被牵动 ——
    上一版在 `if` 体里把 8 空格压成 4 空格（**语法直接坏掉**），
    还因为"生成器把注释提到拍首"而多插了 24 行。
    一对一替换则连"什么都不改 → 产物逐字节等于原文"这条契约都天然成立。
    """
    pool: dict = {}
    for g in gen_bodies:
        pool.setdefault(_line_key(g), []).append(g)
    used: set = set()
    out: list = []
    changed = 0
    landed: list = []

    for ln in old_lines:
        body = _norm(ln)
        key = _line_key(body)
        cand = pool.get(key) or []
        pick = None
        for idx, g in enumerate(cand):
            if (key, idx) not in used:
                pick, slot = g, idx
                break
        if pick is None:
            out.append(ln)                   # 生成器里没有对应的行 → 一个字不动
            continue
        used.add((key, slot))
        landed.append(_norm(pick))
        if body == _norm(pick):
            out.append(ln)                   # 内容一致 → 保留原文那一行（逐字节）
            continue
        if not _same_thing_unless_rewritten(body, pick):
            out.append(ln)                   # 不是"同一件事" → 不许换
            continue
        out.append(_indent_of(ln) + _norm(pick))   # **缩进只来自原文**
        changed += 1

    # 生成器里"没能在本区间落地"的行（通常是注释/代码/结构行）——
    # 它们不属于这次替换范围，如实报出来，便于判断有没有漏改。
    unlanded = [g for key, items in pool.items()
                for idx, g in enumerate(items) if (key, idx) not in used]
    return out, changed, unlanded


# ==================================================================
#  三、整体校验：补丁后的文本必须"还认得出每一句话"
# ==================================================================

def _content_lines(gen_bodies: list) -> list:
    """生成块里的**内容行**（台词 / 舞台 / 音频）——注释与代码不算。"""
    out = []
    for ln in gen_bodies or ():
        n = _norm(ln)
        if not n:
            continue
        if _line_key(n)[0] in ('say', 'show', 'hide', 'scene',
                               'play', 'stop', 'queue'):
            out.append(n)
    return out


def _verify_patch(text: str, original: str, beats: list, blocks: list) -> list:
    """
    补丁后的文本能解析、且**该有的东西一样不少**吗？返回不通过的原因列表。

    这是最后一道闸：任何一条不过，`ok` 就是 False，调用方退回全量。
    宁可丢注释（全量的代价），也绝不写出半对的脚本。

    四查：
      ① 能解析回来
      ② 每一句该有的台词都在（多重集包含）
      ③ **生成器要的每一条内容行都在产物里** —— 真实数据实测 416/416 条
         都满足，所以这条不会误伤；而"区间错位改错了行"会被它挡住
      ④ `label` / `return` 数量不许比原文少（结构不许被啃掉）
    """
    reasons = []
    try:
        from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder
        from storygraph.story.beat_build import beats_from_parse_result
        from pathlib import Path as _P
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.rpy', delete=False,
                                         encoding='utf-8') as fh:
            fh.write(text)
            tmp = _P(fh.name)
        try:
            parsed = ParseResultBuilder().build(RenPyParser().parse(tmp))
            new_beats = beats_from_parse_result(parsed)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
    except Exception as exc:                 # noqa: BLE001
        return [f'补丁结果解析失败：{exc}']

    # ② 台词不丢
    have: dict = {}
    for b in new_beats:
        if (b.text or '').strip():
            key = (b.label or '', b.speaker or '', b.text.strip())
            have[key] = have.get(key, 0) + 1
    missing = []
    for b in beats:
        t = (b.text or '').strip()
        if not t:
            continue
        key = (b.label or '', b.speaker or '', t)
        if have.get(key, 0) <= 0:
            missing.append(t[:24])
        else:
            have[key] -= 1
    if missing:
        reasons.append(f'{len(missing)} 句台词在补丁结果里找不到（例：'
                       f'{missing[0]!r}）')

    # ③ 生成器要的内容行不许丢（错位改错行会在这里现形）
    #
    # 比对要**容忍结尾冒号**：菜单选项在源文件里是 `"A":`，而生成器
    # 只产 `"A"`（冒号是 `menu:` 结构的语法）。不放这一档就会把
    # 正常脚本误判成"源码区间对不上"。
    text_set = set()
    for ln in text.splitlines():
        n = _norm(ln)
        text_set.add(n)
        text_set.add(n.rstrip(':'))
    lost = []
    for _i, _first, _last, gen, _beat in blocks:
        for line in _content_lines(gen):
            if line not in text_set:
                lost.append(line)
    if lost:
        reasons.append(f'{len(lost)} 条生成内容在补丁结果里找不到（例：'
                       f'{lost[0][:40]!r}）——多半是源码区间对不上原文')

    # ④ 结构不许少
    for pattern, label in ((r'^\s*label\s', 'label'),
                           (r'^\s*return\s*$', 'return')):
        before = len(re.findall(pattern, original, re.M))
        after = len(re.findall(pattern, text, re.M))
        if after < before:
            reasons.append(f'{label} 数量从 {before} 变成 {after}')
    return reasons


# ==================================================================
#  四、主入口
# ==================================================================

def patch_export(beats: list, source_path, generated_text: str,
                 registry=None, symbols=None, indent: str = '    ') -> PatchResult:
    """
    把 `generated_text` 增量补进 `source_path`，返回合并后的全文。

    **本函数绝不返回"半对"的产物**：任何一项校验不过，`ok` 就是 False、
    `reasons` 说明原因，调用方应当退回全量重生成。
    ⚠ `ok=False` 时 **`text` 不可用**（它只是"尝试过的中间结果"）。

    `registry` / `symbols` 必须与生成 `generated_text` 时**同一套**，
    否则单拍渲染的写法会和真实产物不一致（`ling happy` vs 中文 tag）
    —— 那就是"把能跑的脚本改坏"。没给 symbols 会从 registry 现推，
    与 `render_beats_to_rpy` 的做法一致。
    """
    source_path = Path(source_path) if source_path else None
    if source_path is None or not source_path.exists() or not beats:
        return PatchResult(generated_text, None, 0, len(beats or []), 0,
                           reasons=['没有源 .rpy，无法增量'])
    if symbols is None and registry is not None:
        from storygraph.story.beat_render import make_symbols
        symbols = make_symbols(registry)
    try:
        original = source_path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        return PatchResult(generated_text, None, 0, len(beats), 0,
                           reasons=[f'源文件读不了：{exc}'])

    # 行号是运行期元数据（不入表）；从表里读出来的拍没有 → 现找回来
    dropped = 0
    if not any(getattr(b, 'src_first', 0) for b in beats):
        _m, _t, dropped = recover_source_ranges(beats, source_path)

    from storygraph.story.beat_render import render_beat_to_rpy
    from storygraph.story.dsl import Audit
    audit = Audit(symbols) if symbols is not None else None

    orig_lines = original.splitlines()
    out_lines = list(orig_lines)
    blocks = []
    for i, b in enumerate(beats):
        first = int(getattr(b, 'src_first', 0) or 0)
        last = int(getattr(b, 'src_last', 0) or 0)
        try:
            gen = [_norm(ln) for ln in
                   render_beat_to_rpy(b, indent=indent, audit=audit)
                   if str(ln).strip()]
        except Exception:                    # noqa: BLE001
            gen = []
        blocks.append((i, first, last, gen, b))

    matched = 0
    changed = 0
    stale = 0
    unlanded_total = 0
    out_lines = list(orig_lines)
    # 从后往前改，避免前面的替换影响后面的行号（本版行数不变，
    # 但保持这个顺序更稳：以后要支持增删行时不用再想一遍）
    usable = [blk for blk in blocks if blk[1] and blk[2] >= blk[1]]
    usable.sort(key=lambda t: t[1], reverse=True)

    seen_ranges: list = []
    for _i, first, last, gen, beat in usable:
        if first < 1 or last > len(orig_lines):
            continue
        if any(first <= r_last and last >= r_first
               for r_first, r_last in seen_ranges):
            continue
        old = orig_lines[first - 1:last]
        # ① 对位守卫：原文这一段必须真属于这一拍（防"源被手工改过"）。
        #    判定允许**多看前后一行**：解析器有少量 off-by-one 的怪癖
        #    （例：菜单标题拍的行号指向 `menu:` 那一行，正文在下一行），
        #    太严会把正常表判成"源文件被改过"。**替换仍然只动精确区间。**
        window = orig_lines[max(0, first - 2):min(len(orig_lines), last + 1)]
        if not _range_looks_like(window, beat):
            stale += 1
            if not gen:
                continue
            # 守卫不过但仍可能改 → 一律不动（不猜）
            seen_ranges.append((first, last))
            continue
        seen_ranges.append((first, last))
        matched += 1                          # 区间对上了（哪怕不需要改）
        if not gen:
            continue                          # 这一拍生成器没输出内容
        if [_norm(ln) for ln in old] == gen:
            continue                          # 内容一致 → 一个字节都不动
        new_seg, n_changed, unlanded = _replace_matched(old, gen)
        unlanded_total += len(unlanded)
        if new_seg == old:
            continue
        changed += n_changed
        out_lines[first - 1:last] = new_seg

    text = '\n'.join(out_lines)
    if original.endswith('\n'):
        text += '\n'

    # ② 整体校验：认不出每一句话就作废（调用方退回全量）
    #
    # 注意：对位守卫失败**不作为硬性失败** —— 逐拍守卫天然会误伤
    # （解析器行号怪癖 / 只要那一拍本来就没什么可比的内容）。
    # 真正把关的是下面两条：系统性错位会把覆盖率打到 0.9 以下，
    # 而**内容校验**会直接指出"生成器要的那一行不见了"，
    # 两者任一触发 → ok=False → 调用方退回全量。**绝不写半对的产物。**
    reasons = []
    if dropped:
        # 表里删掉的拍，源文件里还留着 —— 增量这一轮不支持删行，
        # 所以**不能装作没看见**（否则文件与表永远不一致）。
        # 退回全量：全量产物才是"表说了算"的那一份。
        reasons.append(f'表里删掉了 {dropped} 拍，本轮增量还不支持删行'
                       f'（已退回全量重生成）')
    if text != original:
        reasons.extend(_verify_patch(text, original, beats, blocks))

    return PatchResult(text, source_path, matched, len(blocks), changed,
                       stale_beats=stale,
                       unlanded_lines=unlanded_total,
                       dropped_beats=dropped,
                       reasons=reasons)


__all__ = ['PatchResult', 'patch_export', 'recover_source_ranges']
