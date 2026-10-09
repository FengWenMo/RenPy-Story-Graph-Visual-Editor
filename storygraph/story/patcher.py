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
     **调用方拒绝写盘**（**绝不写半对的产物**，也绝不因此改成全量覆盖 ——
     全量会丢掉 `default` / `init python` 等不进表的东西，
     见 `docs/P0_静默全量覆盖_2026-10-03.md`）。
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
from collections import Counter
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
    #: 拍内**长出来**的行数（给控制流卡填台词 / 给某一拍加 show）
    grown_lines: int = 0
    #: 拍内**跟着删掉**的行数（清掉某一拍的 show，行数不变就删不掉 → 静默错产物）
    shrunk_lines: int = 0
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

        覆盖率的门槛是 0.9（低于这个数说明源文件与表对不上）。

        **`ok=False` 的意思是"这次别写盘"，不是"改用全量再写"**
        （2026-10-05 改正，见 `docs/P0_静默全量覆盖_2026-10-03.md`）。

        这里原来写的是「退化成全量更稳」—— 那个前提是**错的**：
        全量生成只认得"拍"，`default` / `define` / `init python` /
        `transform` / `screen` 一行都留不下。所以"退回全量"丢的不是注释，
        是**游戏能不能起来**。真实事故形态就是这里被当成"更稳"，
        于是覆盖掉了用户手写的定义文件。

        现在调用方（`EditorSession.export` / `GenerationGuard`）见到
        `ok=False` 一律**拒绝写盘**并报出原因；全量只许用于**新建文件**，
        或者调用方显式 `force=True`。
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

    留着的是低翻车率的特征：章节 / 类型 / 说话人 / **条件** / 舞台指纹。
    纯旁白拍（都一样）依赖**顺序**对齐，那是对的：顺序没变时，
    换掉的只是正文，正是我们要做的事。

    ⚠ **条件必须进键**（2026-10-05 修，O1 取证时抓到的真缺陷）：
    同一章里两个分支的旁白拍在"类型/说话人/舞台"上完全一样，`difflib`
    在**删掉一个**之后会把剩下那个配到**后面那一行**上 —— 实测

        if love >= 5:          ← 这一拍还在（条件 love >= 5）
            lyj "好感够高"
        else:
            lyj "好感不够"      ← 这一拍被删了

    删完之后"好感够高"那一拍拿到的是 **else 那一行**的行号 →
    补丁把台词写进 `else` 分支（**换了个分支 = 静默改剧情**），
    而测试只断言"被删的台词不在了"，所以一直没人发现。
    条件是对齐里**稳定且能区分分支**的特征（改台词不会动它），
    进键之后这种"跨分支配错"就不可能发生。
    """
    return (str(getattr(beat, 'label', '') or ''),
            str(getattr(beat, 'kind', '') or ''),
            str(getattr(beat, 'speaker', '') or '').strip(),
            str(getattr(beat, 'condition', '') or '').strip(),
            _stage_tokens(beat))


def _recover_ranges_for_groups(new_groups, src_groups, apply_ranges, apply_inserts, inserted_out, dropped_out, dropped):
    """按 label 分组做内容对齐（SequenceMatcher）→ 给每拍行号 / 标记插入点 / 记被删的拍。返回配上的拍数（M4 搬出，行为一字不改）。"""
    matched = 0
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
    return matched

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

    # —— 编辑器那条路（`apply_ranges=False`）：**按行区间对身份，不靠内容对齐** ——
    #
    # 为什么不能靠内容对齐（2026-10-05 真实 chapter0_0 / chapter1_1 实测）：
    # 拍上本来就带着**准确的行号**（解析时记下的），却拿 `_align_key`
    # （label/kind/speaker/舞台指纹，**不含台词正文**）去重新对齐 —— 旁白连成
    # 一片时 SequenceMatcher 会挑错元素：用户删的是第 152 行那一拍，
    # 它却报"第 156 行那一拍被删了"，而那一拍**明明还在**
    # → "被删的拍与在册拍交叠" → 判定删除不安全 → 整章退回全量（注释全丢）。
    #
    # 判据其实很简单：源拍的行区间还**有人认领**就是还在，没人认领就是被删了。
    if not apply_ranges:
        claimed = set()
        for b in beats:
            f = int(getattr(b, 'src_first', 0) or 0)
            l = int(getattr(b, 'src_last', 0) or 0)
            if f:
                claimed.add((f, l))
        all_src = [sb for pool in src_groups.values() for sb in pool]
        unclaimed = []
        if claimed and all_src:
            for sb in all_src:
                f = int(getattr(sb, 'src_first', 0) or 0)
                l = int(getattr(sb, 'src_last', 0) or 0)
                if not (f and (f, l) in claimed):
                    unclaimed.append(sb)
        # **只在"少数源拍没人认领"时才信行号**：大面积对不上说明行号过期
        # （源文件被手工改过 / 整体错位）—— 那时必须退回内容对齐，
        # 否则会把它当成"用户删了整章"，一路删下去（实测过：全部 +1 偏移
        # 会让这里报"每一拍都被删了"）。
        if claimed and all_src and len(unclaimed) <= max(2, len(all_src) // 5):
            n_dropped = len(unclaimed)
            if dropped_out is not None:
                dropped_out.extend(unclaimed)
            return len(beats), len(beats), n_dropped

    matched = 0
    dropped: list = []
    matched = _recover_ranges_for_groups(new_groups, src_groups,
                                            apply_ranges, apply_inserts,
                                            inserted_out, dropped_out,
                                            dropped)
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

#: 台词行：`说话人 "正文"`（说话人可缺，那就是旁白）。
#: **单引号也算**（2026-10-05 真实 chapter1_2 实测）：原文可能写成
#: `'file "c:\\a.py", line 24'`（单引号包、内部双引号不转义），
#: 生成器写成 `"file \"c:\\\\a.py\", line 24"` —— 只认双引号时这两行
#: 「身份」不同 → 配不上对 → 生成的那行被当成**新内容插进去**，文件里就多一句。
_SAY_RE = re.compile(
    r'^(?P<who>[A-Za-z_][\w.]*)?\s*(?P<q>"|\')(?P<body>.*)(?P=q)$')
#: 舞台/音频行：动词 + 目标
#:
#: ⚠ `voice` 必须在这里（2026-10-05 修 p0）：它**不在**列表里时
#: `voice "v_ling_01"` 会被 `_SAY_RE` 当成"说话人叫 voice 的台词"
#: —— 开着句位配对时就会和真台词互相顶掉（见 `_line_key` 的说明）。
_VERB_RE = re.compile(
    r'^(?P<verb>show|hide|scene|play|stop|queue|voice)\s+(?P<rest>.*)$')
_CHAN_RE = re.compile(r'^(?P<chan>music|sound|voice)\b')

_STAGE_VERBS = ('show', 'hide', 'scene')

#: Ren'Py 的**关键字**：不可能出现在"说话人"位上（角色变量不许叫这些名字）。
#:
#: 用途只有一个：**句位配对（`ignore_speaker`）不许把它们当成"说话人变了"**
#: —— `extend "…"` / `menu "标题":` 的形状与 `lyj "台词"` 一样，一旦退化成
#: `('say','')` 就会和真台词互相顶掉（`voice "x"` 已经踩过一次，见 `_line_key`）。
_KEYWORD_SPEAKERS = frozenset({
    'extend', 'voice', 'play', 'stop', 'queue', 'show', 'hide', 'scene',
    'menu', 'nvl', 'window', 'pause', 'return', 'jump', 'call', 'label',
    'if', 'elif', 'else', 'while', 'for', 'python', 'init', 'define',
    'default', 'transform', 'screen', 'style', 'image', 'camera', 'with',
    'at', 'as', 'behind', 'onlayer', 'zorder', 'pass', 'break', 'continue',
})


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


#: 菜单选项行：`"选项":` / `'选项':` —— **后面可以跟行尾注释**。
#:
#: 为什么单开一条（2026-10-05 修 O6 追到的根）：单拍渲染（`render_beat_to_rpy`
#: 的选项那一支）写出来的是**去掉冒号**的 `"选项"`，而源文件里是 `"选项":`
#: （冒号是 `menu:` 块的结构语法）。配对/改写时这两者必须是"同一件事"，
#: 否则 —— 实测两条都很坏 —— 选项行带行尾注释时生成行会被当"新增内容"
#: **插进去**、旧行又留着 → **重复行**；小幅改选项文案时既不落也不报
#: → **静默 no-op**。
#:
#: ⚠ 行尾注释也一起吃进来：选项行带注释时（`"去左边":  # 备注`）行**不是**
#: 以冒号结尾的，只看"结尾是不是冒号"的写法认不出来（第一版就是这么漏的）。
_OPTION_ROW_RE = re.compile(
    r'^(?P<lit>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')\s*:\s*(?:#.*)?$')


def _content_of(line: str) -> str:
    """
    这一行的**内容部分** —— 去掉行尾注释（`#` 在引号里不算注释）。

    为什么要去掉再算身份（2026-10-05，修 O6 时**连带**抓到的真缺陷）：
    渲染器会把行尾注释贴回同一行（`e "台词"  # 备注`），而 `_SAY_RE` 要求
    整行恰好是 `说话人 "正文"` —— 带注释的台词行**永远认不出自己是台词**：
    配对失败 → 生成行走"新增内容"通道被**插进去**、旧行又因为"台词不许删"
    留着 → **同一句台词出现两遍**（`hell_edit_rounds` 轴 B 第 7 轮实测：
    `e "第1轮台词"  # 备注：开场` 旁边多出一条同样的行）。

    实现在**同一包**的 `atl._strip_comment`（引号感知）—— 不另写一份。
    认不出形状的行仍然逐字比（含注释），保持保守。
    """
    from storygraph.story.atl import _strip_comment
    return _strip_comment(line or '')


def _line_key(body: str, ignore_speaker: bool = False) -> tuple:
    """
    配对用的身份键（见 `_replace_matched` 的说明）。

      · 台词：`('say', 说话人)` —— 说话人一样才允许换正文
      · 舞台：`('show', tag)` / `('hide', tag)` / `('scene', tag)`
      · 音频：`('play', 通道)` …
      · 代码：`('code', 目标)`（`$ love += 1` ↔ `$ love += 2` —— 迁移后
        允许"改了脚本逻辑"这一行跟着换，见 `_rewrite`）
      · 其余：`('other', 原文)` —— 只和自己逐字相同的行配对

    台词配台词，而且**说话人必须一样**（`lyj "…"` 不会去配 `jc "…"`）。
    `ignore_speaker=True` 时台词键退化成 `('say', '')` —— **句位配对**：
    只用在"行号已被证明新鲜"的那条路上（`trust_ranges`），因为那时区间
    本来就属于这一拍，用户把旁白改成台词（或换了说话人）不该导致配不上。
    默认 False：认不出的一律保守（配对失败 = 原文一个字不动）。

    ⚠ 身份一律在**去掉行尾注释**之后算（见 `_content_of`）：注释不是内容，
    而渲染器会把注释贴回同一行 —— 不去掉的话带注释的台词行认不出自己是台词。
    """
    s = (body or '').strip()
    bare = _content_of(s)
    # ⚠ **动词优先于"说话人 + 引号"**（2026-10-05 修 p0）：
    # `voice "v_ling_01"` / `play sound "door"` 这种"动词 + 带引号的目标"
    # 形状与 `lyj "台词"` **一模一样**，`_SAY_RE` 会把 `voice` 当说话人
    # → 键算成 `('say', 'voice')`。从前无害（说话人不同 → 配不上），可一旦
    # 开了句位配对（`ignore_speaker`，行号新鲜时），**所有台词键都变成
    # `('say','')`** → 音频行与真台词互相配对 —— 实测把
    # `m "还行，就是雨下了一整晚。"` 整行**换成了** `voice "v_ling_01"`。
    #
    # Ren'Py 里动词是关键字（角色变量不许叫 `voice`/`play`），所以
    # "先认动词"不只更安全，也更接近语言本身的语义。
    m = _VERB_RE.match(bare)
    if m:
        verb, rest = m.group('verb'), m.group('rest')
        if verb in _STAGE_VERBS:
            spec = _parse_stage_body(bare)[0]
            return (verb, spec.split()[0] if spec.split() else '')
        chan = _CHAN_RE.match(rest.strip())
        if chan:
            return (verb, chan.group('chan'))
        # 目标**去掉引号**再当身份：`voice v_ling_01` 与 `voice "v_ling_01"`
        # 是同一件事（生成器会补引号）—— 不归一的话它们配不上对，
        # 生成器那行会被当"新内容"插进去 → 文件里多一条 `voice`。
        return (verb, rest.strip().strip('"\'').split()[0]
                if rest.strip().strip('"\'').split() else '')
    m = _SAY_RE.match(bare)
    if not m:
        # **菜单选项行**（`"选项":`，可带行尾注释）—— 见 `_OPTION_ROW_RE`：
        # 摘掉冒号之后它就是一条**没有说话人的台词行**，与生成器写的
        # `"选项"` 同键。只认"裸引号 + 冒号"这一种形状，绝不把
        # `menu "标题":`（说话人位上是 `menu`）误判成台词。
        mo = _OPTION_ROW_RE.match(s)
        if mo is not None:
            m = _SAY_RE.match(mo.group('lit'))
            if m is not None and (m.group('who') or '').strip():
                m = None
    if m:
        who = (m.group('who') or '').strip()
        # ⚠ **关键字不许被"句位配对"吞掉**（2026-10-05 修 p0）：
        # `extend "…"` / `menu "标题":` 这类行的"说话人位"上是 Ren'Py 的
        # **关键字**，不是角色名。`ignore_speaker` 只管"说话人换了也能配"，
        # 绝不该让它们退化成 `('say','')` 去和真台词配对
        # （同一形状的坑已经踩过一次：`voice "x"` 顶掉了 `m "台词"`）。
        if ignore_speaker and who not in _KEYWORD_SPEAKERS:
            who = ''
        return ('say', who)
    if bare.startswith('$'):
        return ('code', _code_target(bare))
    return ('other', s)


def _same_thing_unless_rewritten(old_body: str, new_body: str,
                                relax_speaker: bool = False) -> bool:
    """
    内容不同时，这一行**允许被替换**吗？

    只放行"同一件事、参数变了"：

      · 台词：说话人一样 → 可以换正文（这就是"改台词"）
      · 立绘：tag 一样、且**图像名的词数一致** → 可以换属性/位置
        （词数不一致说明生成器把 `ling close_happy` 改写成了
         `ling_close_happy` —— 那是"把能跑的脚本改坏"，**不许换**）
      · 其余：不换

    `relax_speaker=True`（2026-10-05，**只在行号已被证明新鲜时**）：
    台词↔台词一律放行，**说话人变了也算**。用户实测那一刀就是
    "把一句旁白改成 `lyj "…"` 并换掉正文" —— 说话人与正文同时变了，
    严格判据下"不是同一件事"→ 不换 → 新台词落不了地 → 整份拒绝：

        1 句台词在补丁结果里找不到（例：'AI真的拥有自我意识吗？我并不这么觉得'）

    而那一行**就是这一拍自己的行**（行号是解析真迹）—— 该做的就是换掉它。
    """
    kind = _line_key(old_body)[0]
    if kind == 'say':
        if _line_key(old_body) == _line_key(new_body):
            return True
        if not relax_speaker:
            return False
        # 两边都是**同一槽位上的台词行**才放行（不能拿旁白去换一段舞台）
        return _line_key(new_body)[0] == 'say'
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


def _body_lines(gen_lines: list) -> list:
    """
    生成结果里**属于这一拍自己**的那几行 —— 去掉开头的结构行（`if …:`）。

    为什么需要（2026-10-05 修增量 O1）：条件拍的生成结果是
    `if 条件:` + 块体，而**块头不是拍**（`if` 是拍上的条件，它的行不在拍的
    区间里）。拿"整拍"去和区间比行数，就永远是"生成 2 行、区间 1 行" ——
    实测 `mini_game_dfinder.rpy`：改 `if` 体里一句旁白，对位守卫判"这一段不像
    这一拍"，于是整章退回全量。

    ⚠ 只去**开头连续的**结构行，且只认 `if` / `elif` / `else` ——
    `menu "标题":` 那种是**菜单拍自己的内容**（标题就在这一行上），不能去；
    `python:` 头也在拍的区间里（块体一起被吸收），同样不能去。
    """
    out = list(gen_lines or ())
    while out and re.match(r'^(if|elif|else)\b', _norm(out[0])):
        out.pop(0)
    return out


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
    from storygraph.story.beat_model import MENU_NO_CAPTION, KIND_MENU_OPTION
    text = (getattr(beat, 'text', '') or '').strip()
    if getattr(beat, 'kind', '') == KIND_MENU_OPTION:
        # **选项行的身份是"它是一条选项行"**（2026-10-05 实测修，与下面
        # 裸 `menu:` 那条同族）。
        #
        # 选项行的**文案正是用户改的那一位** —— `"去左边":` 改成
        # `"第3轮选项":` 之后，新文案在原文里根本不存在；而这一拍没有
        # 说话人、没有舞台，形状相似度那条兜底也被**注释对齐**拖到 0.6 以下
        # （原文 `"去左边":               # 备注：左选项` 那一长串空格 vs
        # 生成器的两个空格）→ 判"错位" → 文案不落地 → `lost_new` →
        # 整章退回全量。症状与用户报的那条一样："只改一句，为什么全量"。
        #
        # 判据（引号 + 冒号，可带行尾注释，复用 `_OPTION_ROW_RE`）只回答
        # "这一段是不是一条选项行"：**只改文案那种改动**因此能落地，
        # 而错位到`menu:` / 台词 / 空行上的区间照样拦得住。
        return any(_OPTION_ROW_RE.match(str(ln).strip())
                   for ln in (old_lines or []))
    if text == MENU_NO_CAPTION:
        # **无标题菜单的身份是那一行 `menu:`，不是占位符**（2026-10-05 实测修）。
        #
        # `Beat.text` 在"菜单没有标题"时是占位符 `\x00menu`（见
        # `MENU_NO_CAPTION`），而源文件里写的是 `menu:` —— 拿占位符去原文里
        # 找永远找不到，于是**每一拍都少认一个**：带着裸 `menu:` 的章节里，
        # 这一拍恒定判"错位"，对位率被它拖到 (n-1)/n。章节小的时候就掉到
        # 90% 门槛之下 → `patch_export` 整份作废 → 原地导出被拒
        # （用户看到的还是"只能全量导出"那种失败）。
        #
        # 实测形状（`tools/hell_edit_rounds.py` 轴 B 第 1 轮，7 拍 → 86%）：
        # 只改一张卡的立绘，导不进去；把 force 去掉让它浮出来才看见。
        #
        # 为什么不是"占位符当没有内容、直接放行"：那会把这一拍的区间校验
        # 整个关掉。渲染这一拍写出来的就是 `menu:` 那一行，所以要求原文段里
        # 真有 `menu` 才是对的（错位的区间照样拦得住）。
        return bool(re.search(r'(?m)^[ \t]*menu\b', blob))
    if text and text in blob:
        return True
    speaker = (getattr(beat, 'speaker', '') or '').strip()
    if speaker and re.search(rf'(?<![\w]){re.escape(speaker)}(?![\w])', blob):
        return True
    # **原生语句 / 代码行也是身份**（2026-10-05 补）：控制流那种卡
    # （内容就是 `jump X` / `call X from Y`，没有台词也没有舞台）以前一条
    # 判据都命中不了 —— 用户往卡里**新填一句台词**时，守卫判"这段不像这一拍"，
    # 于是新内容既不落地也不报错（`lost_new` → 整章退回全量）。
    # 这些行本来就字面写在区间里，能对上就是对的区间，很安全。
    for attr in ('raw', 'code'):
        body = (getattr(beat, attr, '') or '').strip()
        for ln in body.splitlines():
            if ln.strip() and ln.strip() in blob:
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
            # 生成结果去掉**开头的结构行**再比行数（见 `_body_lines`）：
            # 条件拍的 `if …:` 头不属于拍的区间，算进去就永远对不上。
            gen_n = [_norm(ln) for ln in _body_lines(gen_lines)
                     if str(ln).strip()]
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


def _squash(line: str) -> str:
    """
    把**所有**空白都去掉（"是不是只差空白"的更强形式）。

    为什么还需要它（2026-10-05 真实数据）：作者写 `xqlz"pass."` /
    `yhj"结。"`（说话人后**没有**空格），生成器写 `xqlz "pass."` ——
    `_collapse` 只把"一串空白"折成一个，**不会**把"没有空白"和"有一个空格"
    看成同一件事，于是这一行被当成"参数变了"改写掉：
    `没改却动了文件`，每个 `$`/说话人行都成一个 diff 块。
    空白在 Ren'Py 里对这两处写法没有语义差别，所以按"去掉全部空白"比。
    """
    return re.sub(r'\s+', '', line or '')


#: 允许"因为生成器不再产出它"而**整行删掉**的动词（舞台 / 音频）。
#: 台词、注释、`jump`/`call`、结构行一律不在内 —— 那些不许被静默删掉。
_DROPPABLE_VERBS = ('show', 'hide', 'scene', 'play', 'stop', 'queue')

#: 生成器标注"这个引用在资源表里找不到"用的注释标记。
#: `pipeline/compile.py` 的约定是：**新生成 / 改动的行**才写这种注释，
#: "**源文件里原有的行按原样保留**" —— 增量补丁必须守住这条。
_MISSING_MARK = '缺少资源'

_REF_TOKEN_RE = re.compile(r'[\w./\\-]{2,}')


def _is_missing_note(body: str) -> bool:
    """这一行是不是 `# TODO: 缺少资源 …` 这种"说明型注释"？"""
    return _body(body).lstrip().startswith('#') and _MISSING_MARK in (body or '')


def _mentions(a: str, b: str) -> bool:
    """两行是不是在说**同一个引用**（共用一段够长的词 / 路径片段）？"""
    return bool(set(_REF_TOKEN_RE.findall(a or ''))
                & set(_REF_TOKEN_RE.findall(b or '')))


def _code_target(s: str) -> str:
    """`$ love += 1` → `love`；`$ renpy.transition(…)` → `renpy.transition`。"""
    body = _body(s).lstrip('$').strip()
    for sep in ('(', '[', '=', '+', '-', '*', '/', ':', ' ', '\t'):
        i = body.find(sep)
        if i >= 0:
            body = body[:i]
    return body.strip()


def _head_of(body: str) -> tuple:
    """
    这一行"讲的是哪件事"的**粗粒度身份** —— 增删行的准入判据。

    为什么要它：生成器里的一条行如果**没能在原文里配对成功**，有两种可能：

      · 它是**新内容**（用户刚加的）→ 应该插进去；
      · 它是**同一件事的变体**（`call X from Y` 被渲染成 `call X`、
        说话人从 A 改成 B、代码行被规范化）→ 插进去就成了**重复行**。

    实测踩到过第二种：控制流文件里原文是 `call chapter1_2 from _call_chapter1_2`、
    生成器写 `call chapter1_2`（`from` 子句由渲染尾缀补）→ 按"没配上就插"
    会写出一条重复的 `call`。所以增删前先比身份：区间里已经有同一件事的行
    → 一律不许增、不许删（老实报 unlanded，让调用方退回全量）。
    """
    s = _norm(body)
    bare = _content_of(s)                 # 注释不是内容（见 `_content_of`）
    # ⚠ **动词优先**（与 `_line_key` 同一条规矩，2026-10-05 修 p0）：
    # `voice "v_ling_01"` 的"说话人"其实是关键字，不是角色名。不这么排的话，
    # 原文 `voice v_ling_01` 与生成器补了引号的 `voice "v_ling_01"` 身份不同
    # → 被判成"新内容" → 明明一个字都没改，也报"1 处新增内容没能落进产物"
    # → 整份拒绝（`test_ui_qt` 加立绘那条实测踩到）。
    m = _VERB_RE.match(bare)
    if m:
        rest = m.group('rest').split()
        return (m.group('verb'),
                rest[0].strip('"\'') if rest else '')
    m = _SAY_RE.match(bare)
    if m:
        who = (m.group('who') or '').strip()
        if who in _KEYWORD_SPEAKERS:
            # 关键字行（`extend "…"` / `menu "标题":`）：归一到"哪件事"这一层，
            # 别把整句正文当身份（同一件事的两种写法要能认出是同一件）。
            return ('other', who)
        # 台词按**正文**比：换说话人＝同一句话，别把旧行留着又插一条新的
        return ('say', m.group('body'))
    if bare.startswith('$'):
        return ('code', _code_target(bare))
    return ('other', ' '.join(s.split()[:2]))   # `call X from Y` / `jump X` …


def _droppable_line(line: str) -> bool:
    """
    这一行可以因为"生成器不再产出它"而被删掉吗？

    只放行**舞台 / 音频 / `$` 代码**这三类：

      · 用户把某一拍的 `show` 清掉 → 文件里那一行必须跟着走，
        否则"表里没有、文件里还有"（实测：清掉 show 之后补丁报成功，
        产物里 sprite 还在 → **静默错产物**）；
      · 台词不许删（那是删卡片该干的事，走删除通道）；
      · 注释绝不许删（注释是这个功能存在的理由）；
      · `jump`/`call`/结构行不许删（控制流与块结构）。
    """
    body = _body(line).rstrip()
    if not body:
        return False
    if body.startswith('$'):
        return True                           # `$ code`
    key = _line_key(body)
    return key[0] in _DROPPABLE_VERBS


def _narration_slot_replaced(beat, first: int, last: int, old: list, gen: list,
                             index: int, blocks: list, strict_of: dict) -> bool:
    """
    这一拍是不是"**旁白被整句换掉**"？（正文对不上，但位置确实还是它）

    为什么需要（2026-10-05 真实 chapter1_2 实测）：旁白改成一句全新的话之后，
    `_range_looks_like` 的每一条判据都失效（正文没了、没有说话人、没有舞台），
    于是这一拍被当成"源文件被手工改过"跳过 —— 新台词不落地 →
    `lost_new` → **整章退回全量重生成、手写注释被冲掉**。

    判据（只对"纯台词拍"开口子，宁可漏认也不乱认）：
      · 是旁白（有正文、没有说话人），而且没有舞台/`raw`/代码；
      · 旧段与新段的**非空行数相同**（位置与形状都对得上）；
      · 旧段里**正好有一行台词**（这一拍就占着这一个台词槽）；
      · **左右邻居都自洽**（各自"精确区间认得出自己"）——
        源文件整体错位时，邻居这一关过不去，所以不会写错行。

    通过之后只允许**一对一替换**（`allow_edit=False`），不许在这里增删行。
    """
    text = (getattr(beat, 'text', '') or '').strip()
    if not text or (getattr(beat, 'speaker', '') or '').strip():
        return False
    if _stage_tokens(beat) or (getattr(beat, 'raw', '') or '').strip() \
            or (getattr(beat, 'code', '') or '').strip():
        return False
    if len([ln for ln in old if _norm(ln)]) != len(
            [ln for ln in _body_lines(gen) if str(ln).strip()]):
        # 生成结果去掉**开头的结构行**（`if …:`）再比行数：块头不是这一拍的
        # 内容（见 `_body_lines`），算进去就永远是"生成 2 行、区间 1 行"，
        # 于是 `if` 体里改一句旁白必然退回全量（O1 实测 dfinder）。
        return False
    if len([ln for ln in old if _SAY_RE.match(_norm(ln))]) != 1:
        return False
    neighbours = []
    # **只看紧邻的两块**（2026-10-05 修 O1）。
    #
    # 从前这里是"**文件里所有**别的块都算邻居"，于是"任何一块认不出自己"
    # 就把这条兜底整体否掉 —— 真实项目 40 个文件里有 5 个因此永远退回全量
    # （例如 `script.rpy`：唯一一块 `strict=False` 的是 60 行以外那张
    # `menu:` 拍，它的区间只有块头行、本来就认不出自己）。
    #
    # 这条判据的本意（见 docstring）是"源文件**整体错位**时邻居会一起失守"
    # —— 错位是整体性的，紧邻两块一样过不去，所以只看紧邻并不削弱它。
    before = after = None
    for blk in blocks:
        j, f, l = blk[0], blk[1], blk[2]
        if j == index or not f or l < f:
            continue
        if l < first and (before is None or l > before[2]):
            before = blk
        elif f > last and (after is None or f < after[1]):
            after = blk
    for blk in (before, after):
        if blk is not None:
            neighbours.append(blk[0])
    if not neighbours:
        return False
    return all(strict_of.get(j, False) for j in neighbours)


def _flat_editable_range(old_lines: list) -> bool:
    """
    这一段是不是"平整的兄弟行"（可以安全地增删整行）？

    三个条件缺一不可：
      · 没有块头（`menu:` / `if` / `elif` / `else:` / `python:` / `label` …）；
      · 没有块首（行尾是 `:` 的，如菜单选项 `"A":`）；
      · 非空行的缩进**只有一种**（有嵌套 = 增删会破坏结构）。

    不满足就退回"一对一替换、行数不变"的老行为 —— 增删一定发生在
    平整区间里，这是这一版敢动行数的唯一理由。
    """
    indents = set()
    for ln in old_lines or ():
        body = _body(ln).rstrip()
        if not body:
            continue
        if _BLOCK_HEAD_RE.match(_norm(ln)) or body.endswith(':'):
            return False
        indents.add(_indent_of(ln))
    return len(indents) <= 1


def _rewrite_line(old_line, pick, relax_speaker):
    """原文那一行 → 生成器那一行（或保留原文）。返回 (行, 是否改了)。"""
    body = _norm(old_line)
    pick_body = _norm(pick)
    if body == pick_body:
        return old_line, False
    # **菜单选项行**：冒号与行尾注释都不是内容 —— 只比引号里的正文。
    # （正文没变 → 保留原文那一行：作者的引号写法 / 注释 / 空格都不动。
    #   不这么做的话，一次无关编辑会把整章选项的单引号改成双引号：
    #   真实 `script.rpy` 实测 —— 改一句旁白动了 8 行。）
    mo_old = _OPTION_ROW_RE.match(body)
    mo_new = _OPTION_ROW_RE.match(pick_body)
    if mo_old is not None and mo_new is not None and _unescaped(
            mo_old.group('lit')) == _unescaped(mo_new.group('lit')):
        return old_line, False
    # **`elif` / `else` 头不许被"展平"改写**（2026-10-05，增量 O1）。
    #
    # 整份渲染会把 `elif b:` / `else:` 展平成 `if not (a) and (b):`
    # （A2 契约：IR 是语句流不是语句树），于是条件拍的行区间里那一行
    # 与生成结果**逐字不同**。可它不是这一拍的内容 —— 只是**块头**：
    # 用户改的是台词，凭什么顺手把作者的 `elif` 改掉？
    # （实测 `pomodorro.rpy`：改一句台词动了 6 行，多出来的就是展平。）
    #
    # 条件**真被改过**时怎么办：`_verify_patch` 有一条"条件多重集必须
    # 一致"的判据兜着 —— 展平推迟到全量重生成，而不是在这里悄悄改。
    if _norm(old_line).startswith(('elif ', 'else:')) \
            and _norm(pick).startswith('if '):
        return old_line, False
    if _collapse(body) == _collapse(pick_body) \
            or _squash(body) == _squash(pick_body):
        # **只差空白**（原文 `jc  "…"` 两个空格、生成器一个；或者
        # 原文 `xqlz"pass."` 没空格、生成器写 `xqlz "pass."`）：
        # 保留原文那一行 —— 用户要的是"改一处只动一行"，不是"顺手把我的
        # 排版也改了"。Ren'Py 两种写法等价，改它没有任何好处，
        # 却会让每次导出的 diff 多出一堆"看起来没改"的行。
        return old_line, False
    if _unescaped(body) == _unescaped(pick_body):
        # **只差引号 / 转义**（原文 `'file "c:\\a.py", line 24'`、
        # 生成器 `"file \"c:\\\\a.py\", line 24"`）：Ren'Py 读出来是同一句，
        # 同样保留原文那一行 —— 这也是"没改就不许动文件"的一部分。
        return old_line, False
    # **原文那一行只是"生成结果 + 行尾注释"时也不许动**（2026-10-05 修 O1）。
    #
    # 一拍渲染成多行时，它自己的行尾注释本来就贴不回去（见不变量 78 的
    # "有意为之"）——于是 `$ time = 1#1500` 的生成结果是 `$ time = 1`，
    # 而下面那条"代码行按目标换正文"的通道会拿它去**改**这一行：
    # 作者的注释被删掉，而且这次编辑根本没碰它。真实项目 `pomodorro.rpy`
    # 三处（改一句台词却动了 6 行 → 触发"像退回全量却没说"的判据）。
    #
    # 判据：去掉**行尾注释**后与生成结果相同 → 保留原文（注释也是内容）。
    if body.startswith(_norm(pick)) \
            and body[len(_norm(pick)):].lstrip().startswith('#'):
        return old_line, False
    # 代码行：按**目标**配上了就允许换正文（这就是"改了一行脚本逻辑"）。
    # 以前代码行只和逐字相同的行配对，改一个数字就配不上 →
    # 新内容既不落地也不报错（`$` 不属于内容行）→ **静默保持旧代码**。
    if body.startswith('$') and _norm(pick).startswith('$'):
        return _indent_of(old_line) + _norm(pick), True
    if not _same_thing_unless_rewritten(body, pick, relax_speaker):
        return old_line, False            # 不是"同一件事" → 不许换
    return _indent_of(old_line) + _norm(pick), True   # 缩进只来自原文


def _is_new_content_row(body: str, old_heads, old_squash, file_squash) -> bool:
    """
    这条生成行是**真新内容**吗？（准入判据）

    三条都要过：
      · 身份（`_head_of`）在旧段里不存在 —— 不是"同一件事没配上"；
      · 去空白后，旧段里没有任何一行**包含**它 / 被它包含 ——
        防止"写法不同但同一句话"被插成重复行。
        实测踩到：原文 `yhj"结。"   ###注意：…`（行尾带作者注释）
        与生成 `yhj "结。"` 就是这种关系，只比身份判不出来；
      · **整份原文里也没有这一行**（去空白后完全相同）—— 这条专治
        "行号整体错位"：错位时宽松守卫仍可能放行，而被"多出来"的那一行
        其实就写在文件的别处，插进去就是**重复行**（实测：整体 +1 偏移
        会让 `show ling happy at i11` 被插第二遍）。
    """
    s = _squash(body)
    if not s or _head_of(body) in old_heads:
        return False
    # `# TODO: 缺少资源 …` 是**说明**，不是内容：它说的东西原文里已经有了
    # （`play music "bgm/x.mp3"` 被资源表拒了）→ 不许插。
    # 反过来说：不许拿它去顶掉/删掉作者原来那一行。
    # 实测（真实 chapter1_4/1_1/1_3）：不做这一条，"什么都不改"的导出
    # 会把 12~16 行 `play music` 变成 `# TODO` 注释 —— 音乐全没了，
    # 也直接违反 `pipeline/compile.py` 写明的"源文件里原有的行按原样保留"。
    if _is_missing_note(body) and any(_mentions(body, o)
                                      for o in old_lines):
        return False
    for so in old_squash:
        if s in so or so in s:
            return False
    if file_squash and s in file_squash:
        return False
    return True


def _may_drop_line(line, gen_heads, gen_squash, gen_bodies):
    """这一行能不能因为"生成器不再产出它"而删掉？（两条准入都过才删）"""
    if not _droppable_line(line):
        return False
    so = _squash(line)
    if not so or _head_of(_norm(line)) in gen_heads:
        return False
    # 生成器只是**写了条 TODO 说这个引用找不到**时，作者原来那一行
    # 必须原样留着（见 `_is_new_content` 的说明）。
    if any(_is_missing_note(g) and _mentions(g, line)
           for g in gen_bodies):
        return False
    for sg in gen_squash:
        if so in sg or sg in so:
            return False
    return True


def _replace_matched(old_lines: list, gen_bodies: list,
                     allow_edit: bool = True, file_squash=None,
                     relax_speaker: bool = False) -> tuple:
    """
    原文区间 → 新区间。

    返回 `(新行列表, 改动行数, 未落地的生成行, 新增行数, 删除行数)`。

    **两条纪律**（这一版新增了后一条，用户 2026-10-05："继续实现，清除结构债"）：

      ① **配对替换**：台词配台词（说话人必须一样）、`show ling …` 配
         `show ling …`、`play music …` 配 `play music …`，其余只和逐字相同的
         行配对 —— 认不出一律保守（原文一个字不动）。缩进**只来自原文**。
      ② **平整区间里允许增删整行**（所谓结构债：以前硬性要求"行数永不变化"，
         代价是"清掉一条 `show` 删不掉"和"给控制流卡填台词填不进去"）：
         生成器多出来的内容按**渲染顺序**插到"前一个对上的行"之后；
         生成器不再产出、且属于舞台/音频/`$` 的行跟着删掉。
         三条准入线（任一不过就退回①的老行为，**绝不猜**）：
           · 区间平整（旧段与新段都不含块结构、只有一种缩进）；
           · 配对顺序与渲染顺序一致（单调）—— 否则按渲染顺序合并会把原文重排；
           · 要增/删的那一行，其"身份"（`_head_of`）在对面**不存在** ——
             否则就是"同一件事没配上"（`call X from Y` 之类），
             增减都会写出**重复行 / 少一行**。

    为什么"行数不变"曾经是最重要的不变量（2026-09-30 实测）：只要允许增删，
    缩进、空行、结构就全会被牵动 —— 上一版在 `if` 体里把 8 空格压成 4 空格
    （**语法直接坏掉**），还因为"生成器把注释提到拍首"多插了 24 行。
    所以这一版的增删**只在平整区间**发生，插入的缩进**借原文区间**，
    并且事后仍有"重新解析 + 内容全在"的整体校验兜底（不过就退回全量）。
    """
    pool: dict = {}
    for idx, g in enumerate(gen_bodies):
        pool.setdefault(_line_key(g, relax_speaker), []).append(idx)
    used: set = set()
    #: `gen 下标 → 原文下标` 的配对（先贪心配对，再检查单调性）
    pair_of_gen: dict = {}
    pick_of_old: dict = {}
    for j, ln in enumerate(old_lines):
        cand = pool.get(_line_key(_norm(ln), relax_speaker)) or []
        for idx in cand:
            if idx in used:
                continue
            used.add(idx)
            pair_of_gen[idx] = j
            pick_of_old[j] = idx
            break

    out: list = []
    changed = 0
    grown = 0
    shrunk = 0
    landed_idx = set()

    # `_rewrite` 已提到模块级（`_rewrite_line`，M4 拆分）—— 它是纯判定表。

    # 单调性：只有"配对顺序和渲染顺序一致"时才敢按渲染顺序合并（否则会重排原文）
    order = [pair_of_gen[i] for i in sorted(pair_of_gen)]
    monotonic = all(order[t] < order[t + 1] for t in range(len(order) - 1))
    editable = (allow_edit and monotonic and _flat_editable_range(old_lines)
                and _flat_editable_range(gen_bodies))
    old_heads = {_head_of(_norm(ln)) for ln in old_lines if _norm(ln)}
    gen_heads = {_head_of(_norm(g)) for g in gen_bodies if _norm(g)}
    old_squash = [_squash(ln) for ln in old_lines if _squash(ln)]
    gen_squash = [_squash(g) for g in gen_bodies if _squash(g)]

    # `_is_new_content` 已提到模块级（`_is_new_content_row`，M4 拆分）—— 它是纯判定表。

    if editable:
        # `_may_drop` 已提到模块级（`_may_drop_line`，M4 拆分）—— 它是纯判定表。

        cursor = 0
        for idx, g in enumerate(gen_bodies):
            j = pair_of_gen.get(idx)
            if j is None:
                body = _norm(g)
                # 准入不过 → 不插（保持未落地，调用方据此退回全量，绝不写重复行）
                if not _is_new_content_row(body, old_heads, old_squash, file_squash):
                    continue
                pad = _indent_of(old_lines[0]) if old_lines else ''
                out.append(pad + body)
                landed_idx.add(idx)
                grown += 1
                changed += 1
                continue
            # 游标到 j 之间：原文有、生成器没有的行 → 能删就删，否则原样保留
            for t in range(cursor, j):
                if _may_drop_line(old_lines[t], gen_heads, gen_squash, gen_bodies):
                    shrunk += 1
                    changed += 1
                else:
                    out.append(old_lines[t])
            new_line, did = _rewrite_line(old_lines[j], g, relax_speaker)
            out.append(new_line)
            landed_idx.add(idx)
            changed += 1 if did else 0
            cursor = j + 1
        for t in range(cursor, len(old_lines)):
            if _may_drop_line(old_lines[t], gen_heads, gen_squash, gen_bodies):
                shrunk += 1
                changed += 1
            else:
                out.append(old_lines[t])
    else:
        for j, ln in enumerate(old_lines):
            idx = pick_of_old.get(j)
            if idx is None:
                out.append(ln)                # 生成器里没有对应的行 → 一个字不动
                continue
            landed_idx.add(idx)
            new_line, did = _rewrite_line(ln, gen_bodies[idx], relax_speaker)
            out.append(new_line)
            changed += 1 if did else 0

    # 生成器里"没能在本区间落地"的行（通常是注释/结构行，或不平整区间里的增行）
    unlanded = [g for idx, g in enumerate(gen_bodies) if idx not in landed_idx]
    return out, changed, unlanded, grown, shrunk


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


def _loose(line: str) -> str:
    """
    比对用的**最宽形式**：去空白 + 去所有引号。

    解决的是"同一件事、写法不同"这一类（都是真实项目里量很大的形态）：

        `'3-1章':`            ↔  `"3-1章":`          （作者用单引号，渲染器用双引号）
        `menu "标题":`         ↔  下一行的 `"标题"`     （菜单头被合并回一行）
        `voice v_ling_01`     ↔  `voice "v_ling_01"` （渲染器补引号）

    这些**不是"新内容"**：原文里已经有了，只是写法不同。用逐字比会把它们
    判成"新增内容没能落进产物" → 整份拒绝（实测 `hell_edit_rounds` 真实项目轴：
    `credits_screen.rpy` / `mini_game_dfinder.rpy` / `script.rpy` 都被误伤，
    而那些拍用户**根本没碰**）。
    """
    return re.sub(r'[\s"\']+', '', _norm(line))


def _literals(line: str) -> list:
    """
    这一行里的**引号字符串**（Ren'Py 的"内容"就藏在这些引号里）。

    用途：判"这一行说的内容，文件里是不是已经有了" —— 菜单标题被合并回一行时
    （生成 `menu "标题":` ← 原文下一行是 `lyj "标题"`），逐字比一定不同，
    但**内容**是同一句。见 `_unpairable_new_content`。
    """
    out = []
    for a, b in re.findall(r'"([^"\n]*)"|\'([^\'\n]*)\'', line or ''):
        t = (a or b).strip()
        if t:
            out.append(t)
    return out


def _unpairable_new_content(gen_bodies: list, old_lines: list,
                            file_squash=None, file_loose=None) -> bool:
    """
    这一拍的生成行里，有**"原文没有"、而且按配对规矩也落不了地**的吗？

    与 `_replace_matched` 内部的插入准入（`_is_new_content`）**同一套判据**
    —— 两处必须是同一条规矩，否则会自相矛盾：判据说"有新内容"，而配对那边
    明明**配对成功、一个字都没改**。

    实测（2026-10-05，`test_ui_qt` 加一张立绘那条）：原文 `voice v_ling_01`，
    生成器补引号写成 `voice "v_ling_01"` —— `_has_new_content`（只比整行的
    字面/外层引号）判它是"新内容" → 明明什么都没变，却报

        1 处新增内容没能落进产物

    → 整份拒绝（用户看到的就是"我只调了一个卡片，都不能替换"）。
    这里的判据会先看**身份**（`_head_of`）：两边都是 `('voice', 'v_ling_01')`
    → 早就在原文里 → 不算新内容。
    """
    old = list(old_lines or ())
    old_heads = {_head_of(_norm(ln)) for ln in old if _norm(ln)}
    old_squash = [_squash(ln) for ln in old if _squash(ln)]
    #: 旧段的**语义形式**（去外层引号 + 去转义 + 去空白）——
    #: 生成器会把 `'file "c:\\a.py"'` 写成 `"file \"c:\\\\a.py\""`：
    #: 逐字比是两行，语义比是同一行。不这么比就会把"一个字都没改"判成
    #: "新增内容没落地" → 整份拒绝（`hell_edit_rounds` 真实项目轴实测：
    #: `chapter1_2.rpy` / `credits_screen.rpy` / `script.rpy` 等 7 个文件）。
    old_bare = set()
    old_loose = set()
    for ln in old:
        if not _norm(ln):
            continue
        b = _unescaped(ln)
        old_bare.add(b)
        old_bare.add(re.sub(r'\s+', '', b))
        old_loose.add(_loose(ln))
    for body in _body_lines(list(gen_bodies or ())):
        s = _squash(body)
        if not s or _head_of(body) in old_heads:
            continue
        bare = _unescaped(body)
        if bare in old_bare or re.sub(r'\s+', '', bare) in old_bare:
            continue                     # 只差引号 / 转义 → 早就在原文里
        lo = _loose(body)
        if lo and lo in old_loose:
            continue                     # 最宽形式也相等 → 同一件事
        # **整份文件里已经有的内容不算"新"**：菜单头被合并回一行
        # （`menu "标题":` 对应原文下一行的 `"标题"`）这种形态，逐字比
        # 一定对不上，但它本来就在文件里、用户也没碰过 —— 报成"没落地"
        # 就是把整章判死（`credits_screen.rpy` 实测）。
        if file_loose and lo and any(lo in fl or fl in lo
                                     for fl in file_loose):
            continue
        # **内容（引号里的话）文件里已经有了** → 也不算新：菜单标题被并回一行
        # 就是这一种（生成 `menu "标题":`，原文把标题写在下一行，还可能带说话人
        # `lyj "标题"`）。逐字比必然不同，但用户**根本没碰**这一拍 ——
        # 报成"没落地"就是把整章判死（`script.rpy` / `credits_screen.rpy` 实测）。
        lits = _literals(body)
        if lits and file_loose and all(
                any(_loose(t) in fl for fl in file_loose) for t in lits):
            continue
        if _is_missing_note(body) and any(_mentions(body, o) for o in old):
            continue
        if any(s in so or so in s for so in old_squash):
            continue
        if file_squash and s in file_squash:
            continue
        return True
    return False


def _has_new_content(gen_bodies: list, orig_set: set) -> bool:
    """
    这一拍的生成内容里，有**原文里根本没有**的行吗？

    判据从"内容行（台词/舞台/音频）"扩到**整拍的所有非空行**（2026-10-05 修 O1）：

        menu:                       ← 拍的行号区间指着这一行
            "是时候和青一起学习了."   ← 标题正文在**下一行**

    用户改标题 → 单拍生成行成了 `menu "改过的标题":` —— 它既不是 say、也不是
    舞台/音频，旧判据（`_content_lines`）**看不见它** → `lost_new` 不触发 →
    补丁报成功而标题没改（实测 `script_pomo.rpy`：`ok=True`、`changed=0`）。
    这种"静默成功"比退回全量坏得多（用户以为改了），所以判据必须覆盖整拍。

    反向的误伤有限：**没改过**的行本来就逐字在原文里（注释 / `$ code` /
    `label` / `if` 头都对得上），只有"整份被手工改过导致这一拍对不上位"
    才会命中 —— 那种情况本来就该退回全量。
    """
    for ln in gen_bodies or ():
        n = _norm(ln)
        if not n:
            continue
        if n in orig_set or _unescaped(n) in orig_set:
            continue
        return True
    return False


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


def _condition_reasons(new_beats: list, beats: list) -> list:
    """
    同一句台词的条件变了没有？返回原因列表（空 = 都对得上）。

    判据按**内容身份**配对（label + 说话人 + 台词），不是全局多重集 ——
    后者会被"源里还留着的其它拍"误伤（实测两条既有用例：控制流文件里
    未参与编辑的 `jump` 拍、以及同文案出现在两个分支里）。
    """
    def _key(b):
        return (b.label or '', (b.speaker or '').strip(),
                (b.text or '').strip())

    have: dict = {}
    for b in new_beats:
        if (b.text or '').strip():
            have.setdefault(_key(b), []).append((b.condition or '').strip())
    bad = []
    for b in beats:
        if not (b.text or '').strip():
            continue
        want = (b.condition or '').strip()
        pool = have.get(_key(b)) or []
        if want in pool:
            pool.remove(want)                 # 一次匹配抵一次
            continue
        if pool:                              # 台词在、条件变了
            bad.append((_key(b)[2][:16], want, pool[0]))
    return bad


def _verify_conditions(text: str, beats: list) -> list:
    """补丁结果重新解析 → 条件对不对得上（`_condition_reasons` 的入口）。"""
    try:
        from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                     RenPyParser)
        from storygraph.story.beat_build import beats_from_parse_result
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.rpy', delete=False,
                                         encoding='utf-8') as fh:
            fh.write(text)
            tmp = Path(fh.name)
        try:
            new_beats = beats_from_parse_result(
                ParseResultBuilder().build(RenPyParser().parse(tmp)))
        finally:
            try:
                tmp.unlink()
            except OSError as e:
                swallow('patcher._verify_conditions', e)
    except Exception as exc:                 # noqa: BLE001
        return [f'条件校验：补丁结果解析失败：{exc}']
    bad = _condition_reasons(new_beats, beats)
    if bad:
        return [f'{len(bad)} 处条件与要写的拍对不上'
                f'（例：{bad[0]}）—— 条件改动走全量']
    return []


def _audio_ident(line: str):
    """
    音频行的身份 `(动词, 通道)`；不是音频行返回 None。

    与配对用的 `_line_key` **同源**（`('play','music')` / `('voice','')` …），
    这样"能不能配对上"和"算不算同一条音频"用的是同一套判据。
    """
    key = _line_key(_norm(line))
    if key and key[0] in ('play', 'stop', 'queue', 'voice'):
        return key
    return None


def _written_speaker(beat, symbols) -> str:
    """
    渲染器**真正会写出去**的说话人 —— 校验必须用同一个算出来的值。

    为什么（2026-10-05 用户实测报"我只调了一个卡片，都不能替换"）：
    用户填了一个**没登记**的角色名（实测 `lj`），渲染器按既定纪律
    （D7：缺资源就写 `# TODO: 缺少资源 角色 'lj'` 且不生成那条非法语句）
    把说话人丢掉、正文按旁白写。于是产物里那句台词的 key 是
    `(label, '', text)`，而校验拿 `(label, 'lj', text)` 去找 → 判"这句台词
    找不到" → **整份补丁作废**：

        1 句台词在补丁结果里找不到（例：'AI真的拥有自我意识吗？我不信，哈哈哈哈！'）

    而那句台词其实**就在产物里**（探针一摊开就看得见）。两边用同一套判据
    才不会互相打架 —— 这里照渲染器的规矩重算一遍（`beat_render` 里
    `audit.character(...) == TODO` 那一支）。

    字面量说话人（`"系统" "提示"`）不在角色表里查，照渲染器逐字写出去。
    """
    speaker = str(getattr(beat, 'speaker', '') or '').strip()
    if not speaker or symbols is None:
        return speaker
    try:
        from storygraph.story.beat_model import is_literal_speaker
        if is_literal_speaker(speaker):
            return speaker
    except Exception as e:                    # noqa: BLE001
        swallow('patcher._written_speaker.literal', e)
    try:
        # 与渲染器**同一套判据**：`Audit.character()` 回答的是
        # "这个角色表里有没有这个名字"（没建角色表 → PASS，一律放行）。
        from storygraph.story.dsl import TODO as _TODO, Audit
        if Audit(symbols).character(speaker) == _TODO:
            return ''
    except Exception as e:                    # noqa: BLE001 - 判不了就当登记过
        swallow('patcher._written_speaker', e)
    return speaker


def _verify_patch(text: str, original: str, beats: list, blocks: list,
                  symbols=None) -> list:
    """
    补丁后的文本能解析、且**该有的东西一样不少**吗？返回不通过的原因列表。

    这是最后一道闸：任何一条不过，`ok` 就是 False ——
    而 `ok=False` 的意思是**调用方拒绝写盘**（不是"改用全量"，
    见 `PatchResult.ok` 的说明）。宁可这次不保存，也绝不写出半对的脚本。

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
        # 说话人用**渲染器会写出去的那个**（未登记的角色会被丢掉 + TODO，
        # 见 `_written_speaker`）—— 否则"填了个没登记的角色名"会把整份补丁
        # 判死，而产物里那句台词其实好好地在那儿。
        key = (b.label or '', _written_speaker(b, symbols), t)
        if have.get(key, 0) <= 0:
            missing.append(t[:24])
        else:
            have[key] -= 1
    if missing:
        reasons.append(f'{len(missing)} 句台词在补丁结果里找不到（例：'
                       f'{missing[0]!r}）')

    # ②b **同一句台词的条件必须没变**（2026-10-05，增量 O1）。
    #
    # 为什么单独一条：条件**不是**"生成的一行" —— 它在产物里是 `if …:` 那一行，
    # 而块头不属于拍的行区间（见 `_body_lines`），所以"生成内容都在产物里"
    # 那条判据看不见它。用户改了「条件」列而增量补丁照写时，就会**报成功、
    # 条件没改**（静默 no-op）。
    cond_bad = _condition_reasons(new_beats, beats)
    if cond_bad:
        reasons.append(f'{len(cond_bad)} 处条件与要写的拍对不上'
                       f'（例：{cond_bad[0]}）—— 条件改动走全量')

    # ③ 生成器要的内容行不许丢（错位改错行会在这里现形）
    #
    # 比对要**容忍结尾冒号**：菜单选项在源文件里是 `"A":`，而生成器
    # 只产 `"A"`（冒号是 `menu:` 结构的语法）。不放这一档就会把
    # 正常脚本误判成"源码区间对不上"。
    #
    # 还要容忍**空白写法差异**（2026-10-05 真实 chapter3_1 实测）：
    # 原文 `yhj"结。"   ###注意：…`（说话人后没空格 + 行尾还有作者注释）而
    # 生成器写 `yhj "结。"` —— Ren'Py 读出来是同一句，按逐字节比却判"内容丢了"
    # → **整章退回全量重生成、手写注释被冲掉**。所以：
    #   · 键里加上"去掉全部空白"的形式；
    #   · 再允许"生成的那行是产物某一行（去空白后）的子串"，
    #     用来吸收"源文件那行后面还跟着注释"这种情况。
    def _keys(line):
        out = set()
        n = _norm(line)
        # ⚠ **顺序**：先 `rstrip(':')` 再 `_unescaped`（2026-10-05 修 O1）。
        # 从前是"去冒号"与"去引号"各自独立地加一遍，于是**两个一起出现**
        # 的形态对不上：原文 `'第一章':`（单引号 + 选项冒号）、生成器
        # `"第一章"` → 生成侧算得出 `第一章`，原文那侧只有 `'第一章':`
        # 与 `'第一章':`（去引号失败，因为它以 `:` 结尾）→ 判成"内容丢了"
        # → `script.rpy` 整章退回全量（7 条菜单选项）。
        for base in (n, n.rstrip(':')):
            for k in (base, _unescaped(base)):
                if k:
                    out.add(k)
                    out.add(re.sub(r'\s+', '', k))
        return {k for k in out if k}

    text_set = set()
    for ln in text.splitlines():
        text_set |= _keys(ln)
    out_blobs = [re.sub(r'\s+', '', _norm(ln)) for ln in text.splitlines()]
    out_blobs = [b for b in out_blobs if b]
    # ③b **音频行按"通道计数"容忍**（2026-10-05）。
    #
    # 音频行在配对里**永远不会被改写**（`_rewrite` 只放行 say 与舞台动词），
    # 所以产物里保留的是**作者的写法**；而生成器会把它翻译成真实路径
    # （`play music night` → `play music "music/night.ogg"` —— 那个翻译本身
    # 是对的，Ren'Py 要的就是路径）。于是下面那条"每条生成行都要在产物里"
    # 必然失败 → **整份补丁作废**：用户侧看到的是"我只改了一张卡，
    # 却弹「1 条生成内容在补丁结果里找不到」"。
    #
    # 容忍判据取**同一个 (动词, 通道) 的条数**：产物里的条数不许比生成器
    # 想要的少 —— "某条音乐凭空没了"仍然抓得住，"作者写 night、生成器写路径"
    # 不再误伤。
    audio_gen: Counter = Counter()
    audio_out: Counter = Counter()
    for _i, _f, _l, gen, _b in blocks:
        for line in _content_lines(gen):
            ident = _audio_ident(line)
            if ident:
                audio_gen[ident] += 1
    for line in text.splitlines():
        ident = _audio_ident(line)
        if ident:
            audio_out[ident] += 1
    audio_tolerated = {k for k, n in audio_gen.items()
                       if audio_out.get(k, 0) >= n}
    lost = []
    for _i, _first, _last, gen, _beat in blocks:
        for line in _content_lines(gen):
            ident = _audio_ident(line)
            if ident and ident in audio_tolerated:
                continue
            keys = _keys(line)
            if keys & text_set:
                continue
            stripped = re.sub(r'\s+', '', _norm(line))
            if stripped and any(stripped in blob for blob in out_blobs):
                continue
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

def _insert_points_for(beats, insert_ids, raw_gen: dict) -> tuple:
    """
    新增的拍 → `{插入锚点行号: [该锚点上的每一拍的渲染行]}`。

    锚点优先用**运行期锚点**（`beat.insert_anchor`，见 `patch_export` 里
    "锚点不写进 `src_first`"那段），其次才是表格那条路按对位算出来的
    `src_first`。

    ⚠ 锚点行号**不写回拍**：写进 `src_first` 之后这一拍就不再是
    "没有行号的拍"，**下一次导出**会被当成"替换一行"处理 ——
    实测（真实 chapter1_3）插一张卡、连导两次，第二次报告
    "增量补丁：改动 0 行"、产物里那句**没了**。

    `raw_gen` 是 `{拍序号: 已渲染的行}`（`patch_export` 算好的），
    返回的字典里同一锚点上有多张卡片时按拍序排好。

    返回 `(anchors, failed)`。
    """
    anchors: dict = {}
    failed = 0
    for i in sorted(insert_ids):
        point = int(getattr(beats[i], 'insert_anchor', 0) or 0) or \
            int(getattr(beats[i], 'src_first', 0) or 0)
        rows = list(raw_gen.get(i) or [])
        if not rows:
            # 渲染不出内容的新拍（结构补空拍）没有东西可插 ——
            # **不算"找不到锚点"**：那会让整份补丁作废（见 `patch_export`
            # 里"把结构补空拍从新卡片里摘出去"那段）。
            continue
        if not point:
            failed += 1                       # 有内容却没有锚点（文件头？）→ 不能安全插
            continue
        anchors.setdefault(point, []).append(rows)
    return anchors, failed


def _plan_deletions(dropped_src, blocks, orig_lines) -> tuple:
    """
    被删掉的源拍 → 可以安全删除的区间列表。

    用户 2026-10-05："为何删行无法进行增量操作？" → 这一版实现了。
    与"插入"同一套纪律：**只动该动的那几行，凡是拿不准的一律放弃**
    （退回全量），并由 `patch_export` 末尾的全局校验兜底。

    放弃的情形（**不猜**）：

      · 区间越界 / 空；
      · 区间里含块头（`menu:` / `if` / `elif` / `else:` / `python:`）——
        删掉会留下悬空的块结构（上一版就是在这里把 `if` 体的缩进压平、
        语法直接坏掉）；
      · 区间与**仍在的拍**交叠（那几行不只属于被删的拍）。

    返回 `(delete_ranges, delete_failed)`。
    """
    delete_ranges: list = []
    delete_failed = 0
    if not dropped_src:
        return delete_ranges, delete_failed
    taken = [(first, last) for (_i, first, last, _g, _b) in blocks if first]
    for b in dropped_src:
        first = int(getattr(b, 'src_first', 0) or 0)
        last = int(getattr(b, 'src_last', 0) or 0)
        if first < 1 or last < first or last > len(orig_lines):
            delete_failed += 1
            continue
        body = [_norm(x) for x in orig_lines[first - 1:last]]
        if any(_BLOCK_HEAD_RE.match(x) for x in body if x):
            delete_failed += 1                # 含块头 → 会删坏结构
            continue
        if any(first <= t_last and last >= t_first
               for t_first, t_last in taken if t_first and t_last >= t_first):
            delete_failed += 1                # 与仍在的拍交叠 → 不敢删
            continue
        delete_ranges.append((first, last))
    return delete_ranges, delete_failed


def _apply_sequence_edits(out_lines: list, delete_ranges, anchors,
                          indent: str) -> tuple:
    """
    插入 + 删除**一起按位置从后往前**做（先动后面的行，前面的行号才不受影响）。

    返回 `(inserted_lines, removed_lines)`。

    两处容易写错、都留着注释：
      · **锚点是块头**（`menu:` / `"选项":` / `if …:`）时不能紧贴着插，
        否则新台词会掉进那个块里（见 `_insert_anchor`）；
      · **缩进必须自己补** —— `render_beat_to_rpy` 返回的是"相对行"
        （不带基础缩进）。替换路径之所以看着没问题，是因为
        `_replace_matched` 保留原文那一行的缩进；插入没有"原文行"可依，
        只能借**锚点行**的缩进（新卡片在结构上就是锚点的兄弟节点）。
    """
    inserted_lines = 0
    removed_lines = 0
    ops: list = []
    for first, last in delete_ranges:
        ops.append((last, 0, first, last))    # (位置, 类型 0=删, 起, 止)
    for point, rows in anchors.items():
        # 同一锚点上的多张新卡片保持拍序（`rows` 是"每张卡片的行"的列表）
        flat = [ln for group in rows for ln in group]
        ops.append((point, 1, point, flat))   # (位置, 类型 1=插, 点, 行)
    for _pos, kind, a, b in sorted(ops, reverse=True,
                                   key=lambda t: (t[0], t[1])):
        if kind == 0:
            del out_lines[a - 1:b]
            removed_lines += b - a + 1
        else:
            at = max(0, min(a, len(out_lines)))
            # **锚点是块头**时不能紧贴着插（见 `_insert_anchor` 的 docstring）
            at, pad_hint = _insert_anchor(out_lines, at)
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
    return inserted_lines, removed_lines


def _insert_ids_of(beats: list) -> set:
    """
    哪些拍**没有源行号**（= 编辑器里新插进来的卡片）。

    行号是运行期元数据（不入表）；从表里读出来的拍没有 → 现找回来。
    **"新增的拍"也一样要处理**（用户 2026-10-05："你预料到增加新卡片或者
    删除原卡片的对照了吗？"）：新拍**没有行号**，而 `patch_export` 里的
    `usable` 过滤掉了没有行号的拍 → 新卡片的内容**静默丢失**
    （实测：插入一拍、导出后产物里根本没有那句台词，却报告"增量成功"）。
    所以这里把它们挑出来，交给**插入通道**处理。
    """
    return {i for i, b in enumerate(beats)
            if not int(getattr(b, 'src_first', 0) or 0)}


def _recover_ranges(beats: list, source_path):
    """
    取"被删掉的源拍"与"要插入的位置"。

    两条路**必须分开**，因为触发条件不同：

      · **全都没行号**（表格那条路）→ 现算。对位给"新增行"的插入点也要
        收下来，交给**插入通道**（见 `_set_insert_point` 的 docstring：
        当替换处理时，插入点若正好落在 `label` 行上，那一行会被静默丢掉）。
      · **拍上有行号**（编辑器那条路）→ **"表里删了拍"必须每次查**，
        不能因为"拍上已经有行号"就跳过：编辑器那条路（载入章节 →
        删一张卡片 → 导出）拍上都带行号，于是删掉的那一拍在源文件里被
        **静默保留**（2026-10-05 用真实项目验证抓到：214 拍的文件删 1 拍
        导出后仍是 214 拍、差异 0 行）。这里只**数**被删的拍 + 给新拍
        补插入点，不改已有行号。

    返回 `(insert_ids, dropped, dropped_src)`。
    """
    dropped_src: list = []
    if not any(getattr(b, 'src_first', 0) for b in beats):
        aligned_inserts: list = []
        _m, _t, dropped = recover_source_ranges(beats, source_path,
                                                dropped_out=dropped_src,
                                                inserted_out=aligned_inserts)
        return set(aligned_inserts), dropped, dropped_src
    try:
        # `apply_inserts=False`：插入点**不用对位算** —— 对位可能把新拍
        # 放到"源拍序列的开头"（它没有对应行），锚点会落到 `label` 之前。
        # 下面用"前后邻居"这条更实在的规则算（实测把它放在 label 后面、
        # 前一句台词之后）。
        _m, _t, dropped = recover_source_ranges(
            beats, source_path, apply_ranges=False, apply_inserts=False,
            dropped_out=dropped_src)
    except Exception as e:                    # noqa: BLE001 - 查不了就当没有
        swallow('story.patcher.patch_export', e)
        dropped = 0
    return set(_insert_ids_of(beats)), dropped, dropped_src


def _compute_insert_anchors(beats: list, insert_ids, dropped,
                            dropped_src: list):
    """
    给每张新卡片算**插入锚点**（用它在拍序列里的前后邻居）。

    对齐可能把新拍当成"替换"（说话人一样 → 认为是同一拍改了词），
    于是它还是没行号。这时用前后邻居推锚点。

    选点规则（实机踩过两个坑，都写在这里）：

      · 有"前一个带行号的拍" → 插在它的**末行之后**；
      · 只能靠后一个拍 → 插在它**起始行的前一行**；
      · 锚点行的缩进为空（锚点是 `label`/注释这类顶层行）时，
        缩进改用**下一行**的 —— 否则新台词会顶格写成 `jc "…"`，
        把文件结构读坏（实测：产物解析后少了 208 句台词）。

    ⚠ **锚点不写进 `src_first`**：写了之后这一拍就不再是"没有行号的拍"，
    **下一次导出**会被当成"替换一行"处理 —— 实测（真实 chapter1_3）：
    插一张卡、连导两次，第二次报告"增量补丁：改动 0 行"、产物里那句**没了**。
    存成运行期锚点（`insert_anchor`，不进 Excel、不入表），
    每次导出都重新按"插入"处理。

    返回清理后的 `(dropped, dropped_src)`。
    """
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
        b.insert_anchor = anchor
        # 两个邻居都没有 → 留 0，调用方按"找不到插入位置"退回全量
    if dropped and insert_ids and dropped <= len(insert_ids):
        # 对齐会把"新插了一拍"错认成"删了一拍 + 插了一拍"（同一件事的
        # 两面）：新拍本来就要单独插入，所以这里的 dropped 不算"删拍"，
        # 否则每次插入都会白白退回全量（注释全丢）。
        # `dropped_src` 是同一件事的另一半，要一起清掉 —— 否则那些
        # "看着像被删"的区间会和仍在的拍交叠，触发"删除不安全"。
        dropped = 0
        dropped_src = []
    return dropped, dropped_src


def _render_blocks(beats: list, indent: str, audit):
    """
    每一拍 → `(序号, 起始行, 结束行, 归一化后的生成行, Beat)`。

    另外返回 `{序号: 带缩进的生成行}`（`raw_gen`）—— 插入新拍时要用它，
    因为插入没有"原文行"可依，只能整段写进去。

    渲染失败（拍本身有问题）就记一笔 `swallow` 并当作"这一拍没有生成行" ——
    不抛出去，因为增量补丁失败时调用方会退回全量重生成，
    而"其中一拍渲染不了"不该让整次导出变成异常。
    """
    from storygraph.story.beat_render import render_beat_to_rpy

    blocks = []
    raw_gen: dict = {}
    for i, b in enumerate(beats):
        first = int(getattr(b, 'src_first', 0) or 0)
        last = int(getattr(b, 'src_last', 0) or 0)
        try:
            lines = [ln for ln in render_beat_to_rpy(b, indent=indent,
                                                     audit=audit)
                     if str(ln).strip()]
        except Exception as e:                    # noqa: BLE001
            swallow('story.patcher.patch_export', e)
            lines = []
        raw_gen[i] = lines
        gen = [_norm(ln) for ln in lines]
        blocks.append((i, first, last, gen, b))
    return blocks, raw_gen


class _PatchWalkState:
    """
    `_patch_walk_blocks` 要读写的状态（M4 拆 `patch_export` 时打包）。

    十八个名字里有一半是**逐拍累积的计数**，另一半是只读的上下文
    （原文行、blocks、可信标志…）。打包成一个对象，循环里不会漏传一个。
    """

    __slots__ = ('blocks', 'changed', 'grown_total', 'insert_ids', 'lost_new', 'matched', 'orig_lines', 'orig_loose', 'orig_set', 'orig_squash', 'out_lines', 'seen_ranges', 'shrunk_total', 'stale', 'strict_of', 'trust_ranges', 'unlanded_total', 'usable')

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _patch_walk_blocks(st) -> None:
    """
    `patch_export` 的**主循环**：逐拍做对位守卫 → `_replace_matched` → 写回。

    原样搬出（M4 拆分），读写状态全在 `st` 上 —— 它是**逐拍累积**的计数
    （`matched` / `stale` / `changed` / `grown` / `shrunk` / `lost_new`…），
    所以必须显式传，不能各留一份。判据见 `_replace_matched` 的 docstring。
    """
    for _i, first, last, gen, beat in st.usable:
        if _i in st.insert_ids:
            continue                          # 新增的拍 → 走下面的插入通道
        if first < 1 or last > len(st.orig_lines):
            continue
        if any(first <= r_last and last >= r_first
               for r_first, r_last in st.seen_ranges):
            continue
        old = st.orig_lines[first - 1:last]
        # ① 对位守卫：原文这一段必须真属于这一拍（防"源被手工改过"）。
        #    判定允许**多看前后一行**：解析器有少量 off-by-one 的怪癖
        #    （例：菜单标题拍的行号指向 `menu:` 那一行，正文在下一行），
        #    太严会把正常表判成"源文件被改过"。**替换仍然只动精确区间。**
        window = st.orig_lines[max(0, first - 2):min(len(st.orig_lines), last + 1)]
        if not _range_looks_like(window, beat, gen, exact_lines=old):
            if _narration_slot_replaced(beat, first, last, old, gen, _i,
                                        st.blocks, st.strict_of):
                # 旁白被整句换掉：位置由"邻居自洽"佐证 → 认这一拍，
                # 但**只做一对一替换**（增删行的准入比这严得多）。
                st.seen_ranges.append((first, last))
                st.matched += 1
                new_seg, n_changed, unlanded, _gd, _sk = _replace_matched(
                    old, gen, allow_edit=False)
                st.unlanded_total += len(unlanded)
                if new_seg != old:
                    st.changed += n_changed
                    st.out_lines[first - 1:last] = new_seg
                continue
            if st.trust_ranges and gen:
                # 行号**已被证明新鲜**（会话核对过"磁盘上那一份 == 载入时那一份"）：
                # 内容证据全没了不代表错位 —— 用户可以把一句旁白同时改台词、
                # 改正文、再加一张立绘，Identity 判据当然一条都命中不了。
                # 此时按行号替换，并且**按句位配对**（`relax_speaker`）：
                # 旁白 → 台词（说话人从空变成 lj）也要配得上。
                st.seen_ranges.append((first, last))
                st.matched += 1
                new_seg, n_changed, unlanded, _gd, _sk = _replace_matched(
                    old, gen, allow_edit=True, file_squash=st.orig_squash,
                    relax_speaker=True)
                st.unlanded_total += len(unlanded)
                if new_seg != old:
                    st.changed += n_changed
                    st.out_lines[first - 1:last] = new_seg
                elif _unpairable_new_content(gen, old, st.orig_squash, st.orig_loose):
                    # **行号可信 ≠ 一定配得上**：`menu:` → `menu "标题":` 那种
                    # "换行"（标题本来写在下一行）不是换词，配对键不同 →
                    # `_replace_matched` 什么都不做。此时必须**报出来**，
                    # 否则就是"报成功、内容没写"的静默 no-op ——
                    # `hell_edit_rounds` 真实项目轴在 `script_pomo.rpy` 上抓到过。
                    st.lost_new += 1
                continue
            st.stale += 1
            if not gen:
                continue
            # 守卫不过但仍可能改 → 一律不动（不猜）。
            # **但"原文里根本没有这条内容"时不许沉默**：那说明这句是**新写的**
            # （插入的卡片 / 刚改的台词），一动不动 = 内容静默丢失。
            # 实测（真实 chapter1_3）：插一张卡、连导两次，第二次报告
            # "增量补丁：改动 0 行"、产物里那句没了 —— 因为第一次导出把锚点
            # 写进了 `src_first`，第二次它就被当成"替换一行"处理了。
            if _has_new_content(gen, st.orig_set):
                st.lost_new += 1
            st.seen_ranges.append((first, last))
            continue
        st.seen_ranges.append((first, last))
        st.matched += 1                          # 区间对上了（哪怕不需要改）
        if not gen:
            continue                          # 这一拍生成器没输出内容
        if [_norm(ln) for ln in old] == gen:
            continue                          # 内容一致 → 一个字节都不动
        # ② **增删行的准入**：只有"精确区间自己也认得出这一拍"时才允许
        #    增删整行。上面那道守卫允许**多看前后一行**（解析器有 off-by-one
        #    的怪癖），那种宽松判据可以用来"替换"，但**不足以**支撑增删 ——
        #    实测：整体错位 +1 的源文件（行号全过期）配上宽松判据之后，
        #    生成器多出来的行会被当成"新内容"插进去 → 文件里出现重复行。
        #    错位时只做一对一替换（= 老行为：一个字节都不动）。
        strict = _range_looks_like(old, beat, gen, exact_lines=old)
        # ⚠ **配对也要放松**（2026-10-05 用户实测第二刀）：
        # 守卫常常会**通过**（舞台指纹还在：`scene bg bg` 那一行属于这一拍），
        # 于是走的是这条正常路而不是上面那条可信分支 —— 而配对仍然按
        # "说话人必须一样" → 用户"旁白 → 台词 + 换正文"那一刀**配对失败**、
        # 新台词落不了地，产物报"台词找不到 / 生成内容找不到"。
        # 行号已证明新鲜时，句位配对（`relax_speaker`）才对。
        new_seg, n_changed, unlanded, grown, shrunk = _replace_matched(
            old, gen, allow_edit=strict, file_squash=st.orig_squash,
            relax_speaker=st.trust_ranges)
        st.unlanded_total += len(unlanded)
        st.grown_total += grown
        st.shrunk_total += shrunk
        if new_seg == old:
            if st.trust_ranges and _unpairable_new_content(gen, old, st.orig_squash, st.orig_loose):
                # 行号可信却什么都没落地 → 必须报（绝不写"报成功、内容没写"）
                st.lost_new += 1
            continue
        st.changed += n_changed
        st.out_lines[first - 1:last] = new_seg


def patch_export(beats: list, source_path, generated_text: str,
                 registry=None, symbols=None, indent: str = '    ',
                 trust_ranges: bool = False) -> PatchResult:
    """
    把 `generated_text` 增量补进 `source_path`，返回合并后的全文。

    **本函数绝不返回"半对"的产物**：任何一项校验不过，`ok` 就是 False、
    `reasons` 说明原因，调用方应当退回全量重生成。
    ⚠ `ok=False` 时 **`text` 不可用**（它只是"尝试过的中间结果"）。

    `registry` / `symbols` 必须与生成 `generated_text` 时**同一套**，
    否则单拍渲染的写法会和真实产物不一致（`ling happy` vs 中文 tag）
    —— 那就是"把能跑的脚本改坏"。没给 symbols 会从 registry 现推，
    与 `render_beats_to_rpy` 的做法一致。

    `trust_ranges`（2026-10-05，用户实测报"我只调了一个卡片，都不能替换"）：
    **调用方保证"拍上的行号就是从这份源文件解析出来的"**（会话核对过磁盘上
    那一份没被外部改过，见 `EditorSession.source_unchanged`）。此时对位守卫
    可以退成"行号可信"：把一句旁白改成台词、**同时**换掉正文、再加一张立绘时，
    内容证据本来就全没了（说话人不在原文里、正文也不在），
    旧判据只能判"错位" → 整份拒绝。

    ⚠ 默认 `False`：**只有"能保证行号新鲜"的调用方才许传 True**。
    表格那条路（`src_first` 是 `recover_source_ranges` 现算的）绝不许传 ——
    那里的行号是猜出来的，守卫是它唯一的网。这也正是当年那次事故
    （整体下移 1 行、259 行被改写）的入口。
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

    # 行号是运行期元数据（不入表）；从表里读出来的拍没有 → 现找回来。
    # 细节见 `_insert_ids_of` / `_recover_ranges` 的 docstring。
    insert_ids, dropped, dropped_src = _recover_ranges(beats, source_path)
    dropped, dropped_src = _compute_insert_anchors(
        beats, insert_ids, dropped, dropped_src)

    from storygraph.story.dsl import Audit
    audit = Audit(symbols) if symbols is not None else None

    orig_lines = original.splitlines()
    out_lines = list(orig_lines)
    blocks, raw_gen = _render_blocks(beats, indent, audit)

    # 一个"渲染得出内容"的拍都没有 —— 与上面 `not beats` 是同一件事。
    #
    # 为什么还要单独判（2026-10-05，拓展边界③）：顶层 `init python:` 现在会
    # 造一张**空拍**来携带"文件级保真块"（它不属于任何演出流，见
    # `beat_build.attach_file_blocks`）。那张拍让 `beats` 非空，却永远对不上
    # 任何源行 → 覆盖率 0%、`reasons` 却是空的 —— 调用方只拿到一个
    # "没走增量"而**不知道为什么**（`test_empty_chapter_reason_is_distinct`
    # 抓到的正是这个：那条"没有解析出任何拍"的原因必须继续说出来）。
    if not any(gen for _i, _first, _last, gen, _b in blocks):
        return PatchResult(generated_text, None, 0, 0, 0,
                           reasons=['这一章没有解析出任何拍，无法增量'])

    # —— 把"结构补空拍"从新卡片里摘出去（2026-10-05 用户实测报的）——
    #
    # `label ch01_end:` + `return` 这种 label 会落下**一张什么都没有的拍**
    # （解析器补的空拍，见 `beat_build`）。它没有源行号（`src_first == 0`），
    # 于是被 `_insert_ids_of` 当成"编辑器里新插进来的卡片"；而它渲染不出
    # 任何行 → `_insert_points_for` 认为"这张新卡片找不到插入位置" →
    # **整份补丁作废** → 原地导出被拒。
    #
    # 症状正是用户报的那条："明明只是加了一张立绘（或改一句话），
    # 却报『1 张新卡片找不到插入位置，无法安全地增量写入』"—— 而那个文件里
    # 根本没有新卡片。真实例子：`tests/_ui_project.py` 的 `ch01`（13 拍，
    # 尾部 `label ch01_end: return` 一张空拍）→ 加一条 show 就导不出去。
    #
    # 判据：**渲染不出任何行**的新拍既没有内容可插、也不该拦住别人。
    insert_ids = {i for i in insert_ids if raw_gen.get(i)}

    matched = 0
    changed = 0
    stale = 0
    unlanded_total = 0
    grown_total = 0                           # 拍内"长出来"的行（新台词/新 show）
    shrunk_total = 0                          # 拍内"跟着删掉"的行（清掉的 show…）
    lost_new = 0                              # "新增的内容没能落地"的拍数
    out_lines = list(orig_lines)
    # 从后往前改，避免前面的替换影响后面的行号（本版行数不变，
    # 但保持这个顺序更稳：以后要支持增删行时不用再想一遍）
    usable = [blk for blk in blocks if blk[1] and blk[2] >= blk[1]]
    usable.sort(key=lambda t: t[1], reverse=True)

    #: 对位率的**分母**：真的需要落地的拍（见 `PatchResult.coverage`）。
    #:
    #: 排除"既没有源行号、又渲染不出内容"的结构补空拍 —— 它们既不需要替换
    #: 也不需要插入，算进分母只会让"只改一句台词"莫名其妙地掉到 90% 门槛
    #: 以下（实测：13 拍的章节里一张 `label x: return` 的空拍把它压到 92%，
    #: 三张就掉出门槛 → 整章退回全量，而那与用户的改动毫无关系）。
    pending = sum(1 for _i, _first, _last, gen, _b in blocks
                  if gen or (_first and _last >= _first))

    #: 原文里出现过的行（判定"这条内容是不是**新**的"用）
    orig_set = {_norm(ln) for ln in orig_lines}
    orig_set |= {_unescaped(x) for x in orig_set}
    #: 原文里出现过、且**去掉全部空白**的形式（增行的准入判据之一）
    orig_squash = {_squash(ln) for ln in orig_lines if _squash(ln)}
    #: 原文里出现过的**最宽形式**（去空白 + 去引号）—— 用来判"这条内容
    #: 文件里其实已经有了、只是写法不同"（见 `_loose` / `_unpairable_new_content`）
    orig_loose = {_loose(ln) for ln in orig_lines if _loose(ln)}

    changed += 0                              # （占位：下面插完再统计）
    #: 每一拍"用**精确区间**也认得出自己"吗？（增删行 / 旁白整句换写的准入）
    strict_of: dict = {}
    for _bi, _bf, _bl, _bg, _bb in blocks:
        if not _bf or _bl < _bf or _bl > len(orig_lines):
            strict_of[_bi] = False
            continue
        _old_seg = orig_lines[_bf - 1:_bl]
        strict_of[_bi] = _range_looks_like(_old_seg, _bb, _bg,
                                           exact_lines=_old_seg)
    seen_ranges: list = []
    st = _PatchWalkState(
        blocks=blocks,
        changed=changed,
        grown_total=grown_total,
        insert_ids=insert_ids,
        lost_new=lost_new,
        matched=matched,
        orig_lines=orig_lines,
        orig_loose=orig_loose,
        orig_set=orig_set,
        orig_squash=orig_squash,
        out_lines=out_lines,
        seen_ranges=seen_ranges,
        shrunk_total=shrunk_total,
        stale=stale,
        strict_of=strict_of,
        trust_ranges=trust_ranges,
        unlanded_total=unlanded_total,
        usable=usable,
    )
    _patch_walk_blocks(st)

    # —— 被删除的拍：**精确删掉它们的那几行** ——
    #
    # 用户 2026-10-05："为何删行无法进行增量操作？" → 这一版实现了。
    # 与"插入"同一套纪律：只动该动的那几行，凡是拿不准的一律放弃
    # （退回全量），并由下面末尾的全局校验兜底。
    # 具体"哪些情形不敢删"见 `_plan_deletions` 的 docstring。
    delete_ranges, delete_failed = _plan_deletions(
        dropped_src, st.blocks, st.orig_lines)
    # 新增的拍 → 插入点（锚点见 `_insert_points_for` 的 docstring）
    anchors, insert_failed = _insert_points_for(beats, st.insert_ids, raw_gen)
    # 插入 + 删除一起按位置**从后往前**做（先动后面的行，前面行号才不漂）
    inserted_lines, removed_lines = _apply_sequence_edits(
        st.out_lines, delete_ranges, anchors, indent)
    st.changed += inserted_lines + removed_lines + st.grown_total + st.shrunk_total

    text = '\n'.join(st.out_lines)
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
        reasons.append(f'{insert_failed} 张新卡片找不到插入位置，'
                       f'无法安全地增量写入')
    if st.insert_ids and inserted_lines == 0 and insert_failed == 0:
        # **新增的卡片一行都没落进产物** = 静默丢失。用户 2026-10-05 实测：
        # 表格里插一行、锚点正好是 `label …:` 那一行时，替换路径的对位守卫
        # 判"这段不像这一拍"就跳过了，而"什么都没改"时又跳过了整体校验 →
        # 导出报告"增量成功"，产物里却没有那句台词。这种"看着成功、东西没了"
        # 比报错糟得多，所以这里**宁可退回全量**。
        reasons.append(f'{len(st.insert_ids)} 张新卡片一行都没落进产物，'
                       f'继续写会静默丢内容')
    if st.lost_new:
        # 有**原文里根本没有的内容**没能落地（新写的台词 / 插入的卡片）。
        # 一动不动就是把它弄丢了 → 退回全量重生成，绝不报告成功。
        reasons.append(f'{st.lost_new} 处新增内容没能落进产物')
    if delete_failed:
        # 被删的拍里，有几拍**不敢删**（区间含块头 / 与别的拍交叠 / 越界）。
        # 少删一拍就会留下"表里没有、文件里还有"的幽灵台词。
        reasons.append(f'{delete_failed} 处删除不安全（含结构行或与其它拍交叠）')
    if text != original:
        reasons.extend(_verify_patch(text, original, beats, st.blocks,
                                    symbols=symbols))
    else:
        # **一个字都没改，也要查条件**（2026-10-05，O1 实测）。
        #
        # "改了条件、产物一个字节没动"恰好落在两条缝里：`changed_lines=0`
        # → 走不进整体校验；而条件又不是"生成的一行" → 第三条判据也看不见它。
        # 结果就是 `ok=True`、条件没改（静默 no-op）。
        reasons.extend(_verify_conditions(text, beats))

    return PatchResult(text, source_path, st.matched, pending, st.changed,
                       stale_beats=st.stale,
                       unlanded_lines=st.unlanded_total,
                       dropped_beats=dropped,
                       inserted_lines=inserted_lines,
                       inserted_beats=len(st.insert_ids),
                       removed_lines=removed_lines,
                       grown_lines=st.grown_total,
                       shrunk_lines=st.shrunk_total,
                       reasons=reasons)


__all__ = ['PatchResult', 'patch_export', 'recover_source_ranges']
