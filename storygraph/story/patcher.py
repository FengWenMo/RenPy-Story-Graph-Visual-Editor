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

from storygraph.core.safeguard import swallow
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
    #: **新增**的拍真正插进去的行数（编辑器"插入卡片"走这条）
    inserted_lines: int = 0
    #: 新增的**拍数**（对位率的分母要扣掉它们，见 `coverage`）
    inserted_beats: int = 0
    #: **删掉**的行数（"增量删行"，见 `patch_export` 的删除通道）
    removed_lines: int = 0
    #: 校验不通过的原因（空 = 通过）。**非空时 `text` 不可用。**
    reasons: list = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """
        对位率 —— 分母**扣掉新增的拍**。

        新拍在源文件里本来就没有对应行（用户刚插进来的），拿它当"没对上"
        会让分母虚高：4 拍的小文件插 1 拍 → 3/4 = 75% < 90% 的门槛，
        于是"插入"永远被判失败（实机踩到）。真实章节 214 拍插 1 拍虽然能过，
        但同一件事不该看运气。
        """
        base = max(0, int(self.total_beats) - int(self.inserted_beats))
        if base <= 0:
            return 1.0 if not self.reasons else 0.0
        return self.matched_beats / base

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


def _insert_anchor(lines: list, at: int) -> tuple:
    """
    把"插到第 `at` 行之后"修正成**结构安全**的落点：`(at, pad_hint|None)`。

    `pad_hint` 是给调用方的缩进建议（`None` = 照老规矩借锚点行的缩进）。

    用户 2026-10-05 实测（可移植测试工程里的 `menu:`）：在**菜单选项拍**后面
    插一张卡，锚点行是 `"去屋顶看看":`（块头），紧贴着插出来的产物是

        menu:
            "去屋顶看看":
            "新台词"          ← 和选项同级，Ren'Py 要的是缩进块，直接不认
                jump ch01_roof

    所以锚点是块头时，一路退到**最外层那个包围块**（`menu:` 之于 `"选项":`），
    把新行插在**整个块之后**、并和块头同级 —— 这样产物合法，语义也还是
    "这一拍之后"。对 `if` / `python:` 这些块头一样成立（判据：行尾是 `:`）。
    """
    if at < 1 or at > len(lines):
        return at, None
    body = str(lines[at - 1] or '').strip()
    if not body.endswith(':') or body.startswith('#'):
        return at, None
    if body.startswith('label ') or body.startswith('label\t'):
        # `label ch01:` 是块头，但**插在它后面就是"这一章的第一拍"**（正是
        # 想要的落点）—— 不能一路退到 label 之外，那会跑到几百行以后去。
        # 缩进交给调用方的老规矩（借下一行）。
        return at, None
    outer = at - 1
    cur = len(_indent_of(lines[outer]))
    j = at - 2
    while j >= 0:
        ln = str(lines[j])
        if not ln.strip():
            break
        ind = len(_indent_of(ln))
        if ind >= cur:
            # 同级（另一个选项）或更深（上一个选项的块体）：都还在同一个
            # 包围块里 → 继续往上找
            j -= 1
            continue
        text = ln.strip()
        if not text.endswith(':') or text.startswith('label '):
            break                        # 不是包围块头 / label 是章边界
        outer = j
        cur = ind
        j -= 1
    base = len(_indent_of(lines[outer]))
    i = outer + 1
    while i < len(lines):
        ln = str(lines[i])
        if not ln.strip():
            break                        # 空行 = 块结束（不吞掉分隔空行）
        if len(_indent_of(ln)) > base:
            i += 1
            continue
        break
    return i, _indent_of(lines[outer])


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


def recover_source_ranges(beats: list, source_path,
                          apply_ranges: bool = True,
                          apply_inserts: bool = False,
                          dropped_out: list = None,
                          inserted_out: list = None) -> tuple:
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

    `apply_ranges=False` 时**只数 dropped、不碰行号** —— 给"拍上已经有
    行号"的调用方用（编辑器那条路）：它同样需要"表里删了拍"这个结论，
    但不需要（也不该）重算行号。

    `inserted_out`：把"**只有插入点、没有对应源拍**"的那些拍的索引收集进去。
    调用方据此把它们交给**插入通道**（而不是当替换处理）—— 用户 2026-10-05
    实测："表格里插一行 → 导出报告成功，产物里却没有那一句"。

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
                    if apply_ranges:
                        _apply_range(group[j1 + k], pool[i1 + k])
                    matched += 1
            elif tag == 'replace':
                # 成对的按位置给行号（正文改了、但对齐键相同的情况会走 equal；
                # 走到这里多半是说话人/舞台也动了 —— 按顺序硬配，
                # 后面的"对位守卫"与"整体校验"会把配错的挡下来）
                pair = min(i2 - i1, j2 - j1)
                for k in range(pair):
                    if apply_ranges:
                        _apply_range(group[j1 + k], pool[i1 + k])
                    matched += 1
                for k in range(j1 + pair, j2):
                    if apply_ranges or apply_inserts:
                        _set_insert_point(group[k],
                                          pool[i2 - 1] if i2 > i1 else None,
                                          inserted_out=inserted_out,
                                          index=j1 + k)
            elif tag == 'insert':
                prev = pool[i1 - 1] if i1 > 0 else None
                nxt = pool[i1] if i1 < len(pool) else None
                for k in range(j1, j2):
                    if apply_ranges or apply_inserts:
                        _set_insert_point(group[k], prev, nxt,
                                          inserted_out=inserted_out, index=k)
            elif tag == 'delete':
                # 源文件里有、表里没有 → 用户把这一拍删了。
                # `dropped_out` 给的是**这些被删的源拍本身**（带
                # `src_first/src_last`），调用方据此精确删行（2026-10-05）。
                dropped.append(name)
                if dropped_out is not None:
                    dropped_out.extend(pool[i1:i2])
    return matched, len(beats), len(dropped)


def _apply_range(beat, src_beat):
    beat.src_first = int(getattr(src_beat, 'src_first', 0) or 0)
    beat.src_last = int(getattr(src_beat, 'src_last', 0) or 0)


def _set_insert_point(beat, prev_src, nxt_src=None, inserted_out: list = None,
                      index: int = None):
    """
    表格里新增的拍：给一个**插入点**（`src_first == src_last`）。

    插在"前一拍的末行"之后；前面没有就插在"后一拍的起始行"之前。
    这样新写的台词落在**正确的位置**，而不是被扔到文件末尾。

    `inserted_out` 给定时，把这一拍的序号记进去 —— 调用方（`patch_export`）
    必须把它当**插入**处理，而不是"替换一行"：实测（真实 chapter1_3，
    表格里插一行）插入点正好是 `label chapter1_3:` 那一行时，替换路径的
    对位守卫判"这段不像这一拍"→ 静默跳过 → 产物里没有那句台词，
    却报告"增量成功"。
    """
    point = 0
    if prev_src is not None and getattr(prev_src, 'src_last', 0):
        point = int(prev_src.src_last)
    elif nxt_src is not None and getattr(nxt_src, 'src_first', 0):
        point = int(nxt_src.src_first) - 1
    beat.src_first = point
    beat.src_last = point
    if inserted_out is not None and index is not None:
        inserted_out.append(int(index))


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


def _range_looks_like(old_lines: list, beat, gen_lines: list = None,
                      exact_lines: list = None) -> bool:
    """
    原文这一段**确实属于这一拍**吗？

    这是"源文件被手工改过 → 行号失效"的守卫。没有它，错位的补丁会
    直接写到别人的行上（实测：整体下移 1 行，259 行被改写）。

    判据（满足一条即可）：台词在；说话人在；舞台指纹里有一个图像名在。
    什么都没得比（既没台词也没舞台）→ 放行（至少不比没有守卫更差）。

    **旁白被改过的情况**（2026-10-05 实机验证抓到，必须开口子）：
    `text` 是**当前**文本，而用户刚在编辑器里改过它 —— 原文里当然找不到
    新文本，于是这一拍被判"错位"跳过，产物里那句台词**静默保持原样**。
    （有说话人的台词因为能匹配说话人，所以一直没暴露这个问题。）
    所以对"没有别的可比信号"的拍（旁白：无说话人、无舞台）补一条判据：
    原文段与**新生成**段的形状高度相似（行数相同、相似度够高）就认。
    错位到别人的台词上时行数或相似度会明显掉下去，守卫仍然拦得住。
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
    # —— 旁白被改过：用"形状相似度"兜底（判据见上面的 docstring）——
    #
    # 注意比对的是**精确区间**（`exact_lines`），不是放开前后一行的窗口：
    # 窗口天然比生成结果长几行，行数一比就不等，这条判据会永远失效。
    if gen_lines and text and not speaker and not _stage_tokens(beat):
        try:
            base = exact_lines if exact_lines is not None else old_lines
            old_n = [_norm(ln) for ln in (base or []) if str(ln).strip()]
            gen_n = [_norm(ln) for ln in gen_lines if str(ln).strip()]
            if len(old_n) == len(gen_n) and old_n:
                ratio = difflib.SequenceMatcher(
                    None, '\n'.join(old_n), '\n'.join(gen_n)).ratio()
                if ratio >= 0.6:
                    return True
        except Exception:                    # noqa: BLE001
            swallow('patcher._range_looks_like')
    return False


#: 会让"删行"变危险的结构行：块头（删掉会留下悬空的块 / 压平缩进）
_BLOCK_HEAD_RE = re.compile(r'^(menu|if|elif|else|while|for|python|init|transform'
                            r'|image|define|screen|style|label)\b')


def _collapse(line: str) -> str:
    """把连续空白并成一个（只用于"是不是只差空白"这种判断）。"""
    return re.sub(r'[ \t]+', ' ', line or '').strip()


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
        if _collapse(body) == _collapse(pick):
            # **只差空白**（原文 `jc  "…"` 两个空格、生成器一个）：
            # 保留原文那一行 —— 用户要的是"改一处只动一行"，不是"顺手把我的
            # 排版也改了"。Ren'Py 两种写法等价，改它没有任何好处，
            # 却会让每次导出的 diff 多出一堆"看起来没改"的行。
            out.append(ln)
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


def _unescaped(line: str) -> str:
    """
    比对用的"语义形式"：去外层引号 + 去转义 + 空白归一。

    为什么需要（真实项目 chapter1_2 实测）：同一句话，原文写成
    **单引号无转义** `'file "c:\\\\a.py", line 24'`，生成器写成
    **双引号带转义** `"file \\"c:\\\\\\\\a.py\\", line 24"` —— Ren'Py 两者
    读出来一模一样，但逐字节不同。`_verify_patch` 的"生成内容不许丢"那条
    按逐字节比，就会把这一行判成丢失，于是**整章白白退回全量重生成**
    （注释被冲掉、还会因重新分组少一拍）。
    """
    s = _norm(line)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1]                       # 去掉成对的外层引号
    s = s.replace('\\"', '"').replace("\\'", "'")
    while '\\\\' in s:                    # 连续两个反斜杠折成一个
        s = s.replace('\\\\', '\\')
    return re.sub(r'[ \t]+', ' ', s).strip()


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
            except OSError as e:
                swallow('patcher._verify_patch', e)
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
        # 转义差异不算"内容丢了"：生成器会把台词里的 `"` / `\` 写成
        # Ren'Py 的 `\"` / `\\`，而原文可能是没转义的写法 —— 两种 Ren'Py 都
        # 读成同一句话。只按逐字节比会把这种行判成"生成内容找不到"，
        # 于是**整章白白退回全量**（真实项目 chapter1_2 就卡在这里一行上）。
        text_set.add(_unescaped(n))
        text_set.add(_unescaped(n).rstrip(':'))
    lost = []
    for _i, _first, _last, gen, _beat in blocks:
        for line in _content_lines(gen):
            if line not in text_set and _unescaped(line) not in text_set:
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
    if source_path is None or not source_path.exists():
        return PatchResult(generated_text, None, 0, len(beats or []), 0,
                           reasons=['没有源 .rpy，无法增量'])
    if not beats:
        # 与"没有源文件"分开报：这一章里没有可增量的拍（纯 init/python 文件），
        # 以前共用一句话，排查时看不出到底是哪种。
        return PatchResult(generated_text, None, 0, 0, 0,
                           reasons=['这一章没有解析出任何拍，无法增量'])
    if symbols is None and registry is not None:
        from storygraph.story.beat_render import make_symbols
        symbols = make_symbols(registry)
    try:
        original = source_path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        return PatchResult(generated_text, None, 0, len(beats), 0,
                           reasons=[f'源文件读不了：{exc}'])

    # 行号是运行期元数据（不入表）；从表里读出来的拍没有 → 现找回来
    #
    # **"新增的拍"也一样要处理**（用户 2026-10-05："你预料到增加新卡片或者
    # 删除原卡片的对照了吗？"）：编辑器里"插入一张卡片"的新拍**没有行号**，
    # 而下面的 `usable` 过滤掉了没有行号的拍 → 新卡片的内容**静默丢失**
    # （实测：插入一拍、导出后产物里根本没有那句台词，却报告"增量成功"）。
    # 所以这里给它们一个**插入锚点**（前一拍的末行），合并时按"插入"处理。
    insert_ids = {i for i, b in enumerate(beats)
                  if not int(getattr(b, 'src_first', 0) or 0)}
    dropped = 0
    dropped_src: list = []                   # 被删掉的**源拍**（带源码区间）
    if not any(getattr(b, 'src_first', 0) for b in beats):
        # 表格那条路：全都没行号 → 现算。对位给"新增行"的插入点也要收下来，
        # 交给**插入通道**处理（见 `_set_insert_point` 的 docstring：
        # 当替换处理时，插入点若正好落在 `label` 行上，那一行会被静默丢掉）。
        aligned_inserts: list = []
        _m, _t, dropped = recover_source_ranges(beats, source_path,
                                                dropped_out=dropped_src,
                                                inserted_out=aligned_inserts)
        insert_ids = set(aligned_inserts)
    else:
        # **"表里删了拍"必须每次查**，不能因为"拍上已经有行号"就跳过：
        # 编辑器那条路（载入章节 → 删一张卡片 → 导出）拍上都带行号，
        # 于是删掉的那一拍在源文件里被**静默保留**（2026-10-05 用真实项目
        # 验证抓到：214 拍的文件删 1 拍导出后仍是 214 拍、差异 0 行）。
        # 这里只**数**被删的拍 + 给新拍补插入点，不改已有行号。
        try:
            # `apply_inserts=False`：插入点**不用对位算** —— 对位可能把新拍
            # 放到"源拍序列的开头"（它没有对应行），锚点会落到 `label` 之前。
            # 下面用"前后邻居"这条更实在的规则算（实测把它放在 label 后面、
            # 前一句台词之后）。
            _m, _t, dropped = recover_source_ranges(
                beats, source_path, apply_ranges=False, apply_inserts=False,
                dropped_out=dropped_src)
        except Exception:                    # noqa: BLE001 - 查不了就当没有
            dropped = 0
        # 兜底：对齐可能把新拍当成"替换"（说话人一样 → 认为是同一拍改了词），
        # 于是它还是没行号。这时用**它在拍序列里的前后邻居**推锚点。
        #
        # 选点规则（实机踩过两个坑，都写在这里）：
        #   · 有"前一个带行号的拍"→ 插在它的**末行之后**；
        #   · 只能靠后一个拍 → 插在它**起始行的前一行**；
        #   · 锚点行的缩进为空（锚点是 `label`/注释这类顶层行）时，
        #     缩进改用**下一行**的 —— 否则新台词会顶格写成 `jc "…"`，
        #     把文件结构读坏（实测：产物解析后少了 208 句台词）。
        for i in sorted(insert_ids):
            b = beats[i]
            if int(getattr(b, 'insert_anchor', 0) or 0):
                continue                     # 上一轮已经算过锚点了，沿用
            if int(getattr(b, 'src_first', 0) or 0):
                continue
            prev = next((beats[k] for k in range(i - 1, -1, -1)
                         if int(getattr(beats[k], 'src_last', 0) or 0)), None)
            nxt = next((beats[k] for k in range(i + 1, len(beats))
                        if int(getattr(beats[k], 'src_first', 0) or 0)), None)
            anchor = 0
            if prev is not None and int(getattr(prev, 'src_last', 0) or 0):
                anchor = int(prev.src_last)
            elif nxt is not None and int(getattr(nxt, 'src_first', 0) or 0) > 1:
                anchor = int(nxt.src_first) - 1
            # **锚点不写进 `src_first`**（写进去就会踩下面这个坑）：
            # 写了之后这一拍就不再是"没有行号的拍"，**下一次导出**会被当成
            # "替换一行"处理 —— 实测（真实 chapter1_3）：插一张卡、连导两次，
            # 第二次报告"增量补丁：改动 0 行"、产物里那句**没了**。
            # 存成运行期锚点（`insert_anchor`，不进 Excel、不入表），
            # 每次导出都重新按"插入"处理。
            b.insert_anchor = anchor
            # 两个邻居都没有 → 留 0，下面按"找不到插入位置"退回全量
        if dropped and insert_ids and dropped <= len(insert_ids):
            # 对齐会把"新插了一拍"错认成"删了一拍 + 插了一拍"（同一件事的
            # 两面）：新拍本来就要单独插入，所以这里的 dropped 不算"删拍"，
            # 否则每次插入都会白白退回全量（注释全丢）。
            # `dropped_src` 是同一件事的另一半，要一起清掉 —— 否则那些
            # "看着像被删"的区间会和仍在的拍交叠，触发"删除不安全"。
            dropped = 0
            dropped_src = []

    from storygraph.story.beat_render import render_beat_to_rpy
    from storygraph.story.dsl import Audit
    audit = Audit(symbols) if symbols is not None else None

    orig_lines = original.splitlines()
    out_lines = list(orig_lines)
    blocks = []
    raw_gen: dict = {}                       # 带缩进的生成行（插入新拍时要用）
    for i, b in enumerate(beats):
        first = int(getattr(b, 'src_first', 0) or 0)
        last = int(getattr(b, 'src_last', 0) or 0)
        try:
            lines = [ln for ln in render_beat_to_rpy(b, indent=indent,
                                                     audit=audit)
                     if str(ln).strip()]
        except Exception:                    # noqa: BLE001
            lines = []
        raw_gen[i] = lines
        gen = [_norm(ln) for ln in lines]
        blocks.append((i, first, last, gen, b))

    matched = 0
    changed = 0
    stale = 0
    unlanded_total = 0
    lost_new = 0                              # "新增的内容没能落地"的拍数
    out_lines = list(orig_lines)
    # 从后往前改，避免前面的替换影响后面的行号（本版行数不变，
    # 但保持这个顺序更稳：以后要支持增删行时不用再想一遍）
    usable = [blk for blk in blocks if blk[1] and blk[2] >= blk[1]]
    usable.sort(key=lambda t: t[1], reverse=True)

    #: 原文里出现过的行（判定"这条内容是不是**新**的"用）
    orig_set = {_norm(ln) for ln in orig_lines}
    orig_set |= {_unescaped(x) for x in orig_set}

    changed += 0                              # （占位：下面插完再统计）
    seen_ranges: list = []
    for _i, first, last, gen, beat in usable:
        if _i in insert_ids:
            continue                          # 新增的拍 → 走下面的插入通道
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
        if not _range_looks_like(window, beat, gen, exact_lines=old):
            stale += 1
            if not gen:
                continue
            # 守卫不过但仍可能改 → 一律不动（不猜）。
            # **但"原文里根本没有这条内容"时不许沉默**：那说明这句是**新写的**
            # （插入的卡片 / 刚改的台词），一动不动 = 内容静默丢失。
            # 实测（真实 chapter1_3）：插一张卡、连导两次，第二次报告
            # "增量补丁：改动 0 行"、产物里那句没了 —— 因为第一次导出把锚点
            # 写进了 `src_first`，第二次它就被当成"替换一行"处理了。
            if any(ln not in orig_set and _unescaped(ln) not in orig_set
                   for ln in _content_lines(gen)):
                lost_new += 1
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

    # —— 被删掉的拍：**精确删掉它们的那几行** ——
    #
    # 用户 2026-10-05："为何删行无法进行增量操作？" → 这一版实现了。
    # 与"插入"同一套纪律：只动该动的那几行，凡是拿不准的一律放弃
    # （退回全量），并由下面的全局校验兜底。
    #
    # 放弃的情形（**不猜**）：
    #   · 区间越界 / 空；
    #   · 区间里含块头（`menu:` / `if` / `elif` / `else:` / `python:`）——
    #     删掉会留下悬空的块结构（上一版就是在这里把 `if` 体的缩进压平、
    #     语法直接坏掉）；
    #   · 区间与别人的区间交叠。
    removed_lines = 0
    inserted_lines = 0
    insert_failed = 0
    delete_failed = 0
    delete_ranges: list = []
    anchors: dict = {}
    for i in sorted(insert_ids):
        # 锚点：新增的拍优先用运行期锚点（`insert_anchor`），表格那条路用
        # 对位算出来的 `src_first`（`_set_insert_point`）。
        point = int(getattr(beats[i], 'insert_anchor', 0) or 0) or \
            int(getattr(beats[i], 'src_first', 0) or 0)
        rows = list(raw_gen.get(i) or [])
        if not point or not rows:
            insert_failed += 1                # 没有锚点（文件头？）→ 不能安全插
            continue
        anchors.setdefault(point, []).append(rows)
    if dropped_src:
        # 与**仍在的拍**的区间比对（交叠就不敢删：那几行不只属于被删的拍）
        taken = [(first, last) for (_i, first, last, _g, _b) in blocks if first]
        for b in dropped_src:
            first = int(getattr(b, 'src_first', 0) or 0)
            last = int(getattr(b, 'src_last', 0) or 0)
            if first < 1 or last < first or last > len(orig_lines):
                delete_failed += 1
                continue
            body = [_norm(x) for x in orig_lines[first - 1:last]]
            if any(_BLOCK_HEAD_RE.match(x) for x in body if x):
                delete_failed += 1            # 含块头 → 会删坏结构
                continue
            if any(first <= t_last and last >= t_first
                   for t_first, t_last in taken if t_first and t_last >= t_first):
                delete_failed += 1            # 与仍在的拍交叠 → 不敢删
                continue
            delete_ranges.append((first, last))

    # 插入 + 删除**一起按位置从后往前**做：先动后面的行，前面的行号才不受影响
    ops: list = []
    for first, last in delete_ranges:
        ops.append((last, 0, first, last))    # (位置, 类型 0=删, 起, 止)
    for point, rows in anchors.items():
        # 同一锚点上的多张新卡片保持拍序（`rows` 是"每张卡片的行"的列表）
        flat = [ln for group in rows for ln in group]
        ops.append((point, 1, point, flat))   # (位置, 类型 1=插, 点, 行)
    for _pos, kind, a, b in sorted(ops, reverse=True, key=lambda t: (t[0], t[1])):
        if kind == 0:
            del out_lines[a - 1:b]
            removed_lines += b - a + 1
        else:
            at = max(0, min(a, len(out_lines)))
            # **锚点是块头**（`menu:` / `"选项":` / `if …:`）时不能紧贴着插：
            # 那样新台词会掉进那个块里（实测见 `_insert_anchor` 的 docstring）。
            at, pad_hint = _insert_anchor(out_lines, at)
            # **缩进必须自己补**：`render_beat_to_rpy` 返回的是"相对行"
            # （不带基础缩进）—— 替换路径之所以看着没问题，是因为
            # `_replace_matched` 保留原文那一行的缩进；插入没有"原文行"可依，
            # 只能借**锚点行**的缩进（新卡片在结构上就是锚点的兄弟节点）。
            if pad_hint is not None:
                pad = pad_hint
            else:
                pad = _indent_of(out_lines[at - 1]) if at >= 1 else ''
            if not pad:
                # 锚点是 `label` / 注释这类顶层行 → 用**下一行**的缩进，
                # 否则顶格写出来的台词会把文件结构读坏。
                nxt_line = out_lines[at] if 0 <= at < len(out_lines) else ''
                pad = _indent_of(nxt_line) or indent
            out_lines[at:at] = [pad + ln for ln in b]
            inserted_lines += len(b)
    changed += inserted_lines + removed_lines

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
    if insert_failed:
        # 有新增的拍但**找不到锚点**（例如它是文件里的第一拍，
        # 前面没有任何源拍可依附）→ 不猜，退回全量。
        reasons.append(f'{insert_failed} 张新卡片找不到插入位置'
                       f'（已退回全量重生成）')
    if insert_ids and inserted_lines == 0 and insert_failed == 0:
        # **新增的卡片一行都没落进产物** = 静默丢失。用户 2026-10-05 实测：
        # 表格里插一行、锚点正好是 `label …:` 那一行时，替换路径的对位守卫
        # 判"这段不像这一拍"就跳过了，而"什么都没改"时又跳过了整体校验 →
        # 导出报告"增量成功"，产物里却没有那句台词。这种"看着成功、东西没了"
        # 比报错糟得多，所以这里**宁可退回全量**。
        reasons.append(f'{len(insert_ids)} 张新卡片一行都没落进产物'
                       f'（已退回全量重生成）')
    if lost_new:
        # 有**原文里根本没有的内容**没能落地（新写的台词 / 插入的卡片）。
        # 一动不动就是把它弄丢了 → 退回全量重生成，绝不报告成功。
        reasons.append(f'{lost_new} 处新增内容没能落进产物'
                       f'（已退回全量重生成）')
    if delete_failed:
        # 被删的拍里，有几拍**不敢删**（区间含块头 / 与别的拍交叠 / 越界）。
        # 少删一拍就会留下"表里没有、文件里还有"的幽灵台词，
        # 所以宁可整章退回全量。
        reasons.append(f'{delete_failed} 处删除不安全（含结构行或与其它拍交叠）'
                       f'（已退回全量重生成）')
    if text != original:
        reasons.extend(_verify_patch(text, original, beats, blocks))

    return PatchResult(text, source_path, matched, len(blocks), changed,
                       stale_beats=stale,
                       unlanded_lines=unlanded_total,
                       dropped_beats=dropped,
                       inserted_lines=inserted_lines,
                       inserted_beats=len(insert_ids),
                       removed_lines=removed_lines,
                       reasons=reasons)


__all__ = ['PatchResult', 'patch_export', 'recover_source_ranges']
