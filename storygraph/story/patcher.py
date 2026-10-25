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
     见 `docs/archive/P0_静默全量覆盖_2026-10-03.md`）。
  ④ 没有对位守卫 → 现在替换前先确认"原文这一段确实属于这一拍"
     （看得到它的台词/说话人/图像名），对不上就跳过、不猜。
  ⑤ 行号传不过来 → 新增 `recover_source_ranges()`：**解析源 .rpy，
     按内容把行号找回给表格拍**（只读源文件、不动表结构）。

另外：`改动行数` 现在只统计**内容真的变了**的行（只剩空格差异的算没改），
这样"什么都不改 → 产物逐字节等于原文"这条契约才成立。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow
from storygraph.core.vocabulary import RESERVED_STATEMENT_KEYWORDS
from storygraph.core.indent import indent_prefix, indent_width
import difflib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


def removal_note(res, limit: int = 5) -> str:
    """
    增量补丁"删掉了哪些行"→ 一句给用户看的话（没有删除返回空串）。

    为什么要有这么小一个函数（P0-D，2026-10-25《不安全因素》第五轮）：

      表过期时（策划先导出过表、程序员又在同一个 `.rpy` 里加了
      `$ flag = True` / `play music …`），增量补丁会把那些行当
      "表里已经没有对应内容"删掉。这是**设计内**的行为（与"舞台列清空 →
      那行该删"是同一个判据，分不开），但**一定要说**。

      而现实是三个出口三种待遇：CLI 的 `_report_incremental` 点名了，
      `format_compile_result` 紧跟其后又打印一句"只改了 N 行，原文其余部分
      逐字节保留"（**看起来像没删**），编辑器那条路（`session_export`）
      一个字都不提。用户读到的是最后那句。

      所以把"怎么说"收成一处：谁要报增量补丁，就拼这一句。
    """
    preview = tuple(getattr(res, 'removed_preview', ()) or ())
    count = int(getattr(res, 'removed_count', 0) or 0) or len(preview)
    if not count:
        return ''
    text = (f'；**删掉了源文件里 {count} 行**'
            f'（表里已经没有对应内容 —— 表是不是在源码改动之前生成的？）')
    if preview:
        text += '：' + ' / '.join(str(x)[:40] for x in preview[:limit])
    return text


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
    #: **原样透传**（2026-10-18）：这一章一张能渲染的拍都没有 ——
    #: 纯 `define` / `screen` / `init python` 文件，表里没有一处可编辑。
    #: 此时 `text` 就是原文，调用方应当**一个字节都不写**（或原样复制到新路径）。
    #:
    #: 为什么不是"报失败退回全量"：全量重生成会把顶层块重排
    #: （每条语句之间插空行、行尾空格被删、CRLF 变 LF）—— 真机
    #: `definitions.rpy` 1120 行变 1288 行，而用户**一处都没改**。
    passthrough: bool = False
    #: **被删掉的那几行原文**（最多 5 条，去掉缩进）—— 给回执点名用。
    #:
    #: 为什么要单独存（2026-10-21）：补丁从前只报"改动 N 行"。而"改动"里
    #: 混着三种完全不同的动作：换掉一句台词、插一行、**删掉源文件里的某行**
    #: （用户把表里某一拍的舞台列清空 → 源里那行 `show` 跟着走）。
    #: 第三种最容易被吓到："我只改了一句台词，怎么少了一行 show？"——
    #: 回执不说，用户只能自己 diff 文件。这里把删掉的行原样记下来。
    #: 真正被删掉的行数（`removed_preview` 只存前 5 条，**别拿它算数量**）。
    #:
    #: 2026-10-21 实测：批量删 30 拍时回执写"删掉了 5 行"—— 因为拿预览的
    #: 长度当总数。这是**假话**，比不报更糟。
    removed_count: int = 0
    removed_preview: tuple = ()
    #: **新插入、但一个字都没有**的拍数（2026-10-25，审计第四轮 P2）。
    #:
    #: 空卡片渲染不出任何行 —— 插进去之后"导出成功、文件没变"是**正常**的，
    #: 但回执只说"这一份与源文件逐字节相同"，用户会以为插卡失败了。
    #: 有了这个数，`session_export` 就能在回执里补一句"N 张空卡不产生行"。
    empty_beats: int = 0

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
        （2026-10-05 改正，见 `docs/archive/P0_静默全量覆盖_2026-10-03.md`）。

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
    cur = indent_width(lines[outer])
    j = at - 2
    while j >= 0:
        ln = str(lines[j])
        if not ln.strip():
            break
        ind = indent_width(ln)
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
    base = indent_width(lines[outer])
    i = outer + 1
    while i < len(lines):
        ln = str(lines[i])
        if not ln.strip():
            break                        # 空行 = 块结束（不吞掉分隔空行）
        if indent_width(ln) > base:
            i += 1
            continue
        break
    return i, indent_prefix(lines[outer])


def _ends_with_colon(line: str) -> bool:
    """这一行是块头吗（`menu:` / `"选项":` / `if …:`）？注释不算。"""
    body = str(line or '').strip()
    return bool(body) and body.endswith(':') and not body.startswith('#')


def _body_pad(lines: list, at: int, indent: str) -> str:
    """
    "插进块体"时新行该用哪一级缩进：`at` 是块头行的**下一个**下标。

    体里已经有语句 → 借它的缩进（它才是这个项目真用的缩进风格）；
    体是空的（下一个选项已经在同级）→ 块头缩进 + 一级。
    """
    head = lines[at - 1] if at >= 1 else ''
    nxt = lines[at] if 0 <= at < len(lines) else ''
    if str(nxt).strip() and indent_width(nxt) > indent_width(head):
        return indent_prefix(nxt)
    return indent_prefix(head) + indent


def _child_pad_at(lines: list, last: int, unit: str) -> str:
    """
    第 `last` 行是块头时，它的**块体**用哪一级缩进（不是块头就返回 `''`）。

    只给"菜单标题从无到有"那一支用（见 `_replace_matched` 的 `child_pad`）。
    """
    if not last or last < 1 or last > len(lines):
        return ''
    if not _ends_with_colon(lines[last - 1]):
        return ''
    return _body_pad(lines, last, unit)


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
    from storygraph.core.vocabulary import split_stage_items
    out = []
    for raw in split_stage_items(getattr(beat, 'show', '') or ''):
        raw = raw.split('@')[0].strip()
        if raw:
            out.append(raw)
    for raw in split_stage_items(getattr(beat, 'hide', '') or ''):
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


def _has_insert_and_delete(opcodes) -> bool:
    """对齐里**同时**出现"多一拍"和"少一拍"吗？（= 某一拍的对齐身份变了）"""
    tags = {op[0] for op in opcodes}
    return 'insert' in tags and 'delete' in tags


def _recover_ranges_for_groups(new_groups, src_groups, apply_ranges, apply_inserts, inserted_out, dropped_out, dropped, positions=None):
    """按 label 分组做内容对齐（SequenceMatcher）→ 给每拍行号 / 标记插入点 / 记被删的拍。返回配上的拍数（M4 搬出）。

    `positions`：`{id(beat): 全局下标}`。`inserted_out` 收的必须是**全局**下标
    （调用方拿去 `beats` 里取），而这里遍历的是**组内**列表 —— 不映射就会
    在多 label 章节里指错拍（2026-10-19 修，见 `recover_source_ranges`）。
    """
    matched = 0
    positions = positions or {}
    for name, group in new_groups.items():
        pool = src_groups.get(name) or []
        if not pool:
            continue
        sm = difflib.SequenceMatcher(
            None, [_align_key(b) for b in pool],
            [_align_key(b) for b in group], autojunk=False)
        opcodes = sm.get_opcodes()
        # 拍数没变、却又"多一拍 + 少一拍" —— 那不是增删，是**某一拍换了个身份**
        # （说话人改了 → `_align_key` 变了；2026-10-21 实测 `pomodorro.rpy`）。
        #
        # `SequenceMatcher` 这时会拿后面同键的拍去配前面，于是整体错位一位：
        # 新句只拿到"插入点"、旧句被当成"用户删掉的拍"，补丁随后把新句插进去、
        # 旧句因为"台词不许删"留着 —— 同一句话两遍。
        #
        # 拍数相等就按**顺序**对位：位置 i 就是源文件里的第 i 拍。真错位
        # （例如把一拍搬了家）仍由 `_range_looks_like` 的内容守卫与整体校验挡住。
        if len(pool) == len(group) and _has_insert_and_delete(opcodes):
            for k in range(len(group)):
                if apply_ranges:
                    _apply_range(group[k], pool[k])
            matched += len(group)
            continue
        for tag, i1, i2, j1, j2 in opcodes:
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
                                          index=positions.get(id(group[k]),
                                                              j1 + k))
                # —— **`replace` 里"源多出来的拍"必须进 dropped** ——
                # （2026-10-21，P0）
                #
                # 这一支从前只处理"表比源多"（新插入的拍 → 插入点），
                # 源比表多的那一半**没人管**：那些拍既没拿到行号、也没进
                # `dropped_out`，于是 `_plan_deletions` 根本不知道要删它们。
                #
                # 实测（`ling` 四句，表里删掉第二句、同时把第一句的说话人改成 `e`）：
                # 对齐键从 [ling,ling,ling,ling] 变成 [e,ling,ling] →
                # `SequenceMatcher` 给 `replace(源0:2, 表0:1)` + `equal(源2:4, 表1:3)`，
                # 源第 2 拍（"第二句台词"）落在 replace 的尾巴上被静默跳过 →
                # 补丁报 `ok=True / changed=2`、`dropped=0`，
                # **产物里那句台词还在**，而表里已经删掉了。
                #
                # 语义与 `delete` 分支完全一致：源里有、表里没有 = 用户删了这一拍。
                for k in range(i1 + pair, i2):
                    dropped.append(name)
                    if dropped_out is not None:
                        dropped_out.append(pool[k])
            elif tag == 'insert':
                prev = pool[i1 - 1] if i1 > 0 else None
                nxt = pool[i1] if i1 < len(pool) else None
                for k in range(j1, j2):
                    if apply_ranges or apply_inserts:
                        _set_insert_point(group[k], prev, nxt,
                                          inserted_out=inserted_out,
                                          index=positions.get(id(group[k]), k))
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
    # `inserted_out` 收的是**全局下标**（`patch_export` 拿它去 `beats` 里取），
    # 而分组对齐里拿到的是**组内下标** —— 多 label 的章节（真实 `pomodorro`
    # 有 10 个 label）里两者差着一个偏移，插入点会指到别的拍上：
    # 新卡片被当成"替换"，原文那一行反被顶掉（实测插入一拍 → 全量退回，
    # 且 verify 报"某句台词在补丁结果里找不到"）。2026-10-19 修。
    positions = {id(b): i for i, b in enumerate(beats)}
    matched = _recover_ranges_for_groups(new_groups, src_groups,
                                            apply_ranges, apply_inserts,
                                            inserted_out, dropped_out,
                                            dropped, positions=positions)
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
#:
#: ⚠⚠ **说话人不许只认 ASCII**（2026-10-24 修 P0-3）。从前是
#: `[A-Za-z_][\w.]*`，于是 `凌跃捷 "台词"` / `リン "台詞"` 匹配不上 →
#: `_line_key` 落到 `('other', 原文)` → **改了正文键就变了** → 补丁认为
#: "这是新内容"，插入新行；旧行的键也不匹配了，被当未知块留下 →
#: **改一句台词多一行**，而且每导出一次再长一行（实测 3→4→5）。
#: 解析器一直认中文说话人（Ren'Py 基于 Python 3，标识符可以是 Unicode），
#: 只有补丁这一侧不认 —— 典型的两套口径。
#:
#: `[^\W\d]` = 字母或下划线（`\w` 在 Python 3 默认 Unicode）；点号分段保留
#: （`ch.林`）。与 `beat_model._RE_SAY_WHO` **必须同源**，改一处就要改两处。
_SAY_RE = re.compile(
    r'^(?P<who>[^\W\d]\w*(?:\.[^\W\d]\w*)*)?\s*'
    r'(?P<q>"|\')(?P<body>.*)(?P=q)$')
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
#:
#: ⚠ 2026-10-15：集合本体收敛到 `core.vocabulary.RESERVED_STATEMENT_KEYWORDS`
#: （唯一真源里写明了**为什么必须排除 `narrator`**：官方文档说它是"没有角色名的
#: say 语句用的那个 Character"、特殊角色可重定义 → **它能当说话人**；
#: 并进来会让两条不同旁白撞键 → **静默丢行**）。
#: 本轮的收敛**只统一口径、不改口径**：生效集合与从前逐字相同。
_KEYWORD_SPEAKERS = RESERVED_STATEMENT_KEYWORDS


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

#: 台词**带尾巴**（`e "台词" (id="l1")` / `… with vpunch` / `… # 备注`）。
#:
#: 为什么单开一条（2026-10-24，修审计 P0-8「SayTail 改了不落盘」）：
#: `_SAY_RE` 要求整行以引号**结尾**，而高级区「台词后缀」一填，生成行就成了
#: `e "台词" (id="l1")` —— 两条身份键一个 `('say', 'e')`、一个
#: `('other', 原文)`，配不上对，于是补丁"成功"、文件一个字节没动。
#:
#: ⚠ 正文用"不含未转义引号"的字符类（`(?:\\.|[^"\\])*`），不是 `.*`：
#: 贪婪的 `.*` 会一路吃到**尾巴里最后一个引号**，`e "台词" (id="l1")` 的
#: 正文会算成 `台词" (id=`，反而更糟。
_SAY_TAIL_RE = re.compile(
    r'^(?P<who>[^\W\d]\w*(?:\.[^\W\d]\w*)*)?\s*'
    r'(?P<q>"|\')(?P<body>(?:\\.|[^"\\])*)(?P=q)'
    r'\s*(?P<tail>\(.*\)|with\s+[^#]+?|#.*)$')

#: 菜单头（`menu:` / `menu 名:` / `menu 名(args):` / `menu "标题":`）。
#:
#: 身份只到"这是一个菜单头"这一层：菜单名/参数/标题都正是用户会改的那几位
#: （2026-10-24，修审计 P0-8「MenuName 改了不落盘」）。从前它的键是
#: `('other', 原文)`，`menu:` → `menu my_menu:` 配不上对 → 零 diff。
_MENU_HEAD_RE = re.compile(r'^menu\b')

#: 控制流行（`call X [args] [from Y]` / `jump X`）。
#:
#: 身份 = **动词**（`('flow', 'call')`）：目标/参数/`from` 正是用户在控制流卡上
#: 改的那几位（2026-10-24，修审计 P0-5「改目标 → 多一条 call」）。从前键是
#: `('other', 原文)`，换了目标就配不上对 → 新行被插进去、旧行又受
#: `_PROTECTED_RE` 保护不许删 → **剧情跑两遍**。
_FLOW_START_RE = re.compile(r'^(?:call|jump)\b')

#: `label 名(参数)…:` 这一行（`label_header` 的参数表要一起保住）。
#:
#: ⚠ 名字里**允许点号**（2026-10-25 第六轮）：Ren'Py 的 `label main.route:`
#: 完全合法，而且**本工具自己**在"新建 label"的对话框里就示范了这种写法
#: （`ui_qt/window_nodes.py`：'新 label 名（可以用点号，如 main.route）'）。
#: 从前这里是 `[A-Za-z_]\w*` —— `\w` 不含点，于是 `label main.route:` 不被
#: 认成 label 行，补丁按"正文"去对位，整章一句都对不上 → 导出被拒
#: （探针 `tools/_p0_audit_round6_bisect.py`：点号 label 100% 失败）。
#: `pipeline/statement_probe.py` 早就是 `[A-Za-z_][\w.]*` 的正确写法。
_LABEL_LINE_RE = re.compile(r'^(\s*)label\s+([A-Za-z_][\w.]*)(.*)$')



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


def _is_comment_line(line: str) -> bool:
    """整行只是一条注释？（行尾注释属于它前面那条语句，不算）"""
    s = (line or '').strip()
    return bool(s.startswith('#')) and not _content_of(s).strip()


def _is_caption_line(line: str) -> bool:
    """
    这一行是**菜单块里的标题行**吗？（裸字符串：没有说话人、不以冒号结尾）

        "是时候和青一起学习了."        ← 是（`menu:` 的标题）
        "开始":                       ← 不是（选项行，带冒号）
        e "台词"                      ← 不是（有说话人）
    """
    body = _content_of(_norm(line))
    m = _SAY_RE.match(body)
    return m is not None and not (m.group('who') or '').strip()


def _caption_is_substantive(line: str) -> bool:
    """
    这一行标题**有实质内容**吗（不是 `" "` 这种全是空白的）？

    为什么单独问（2026-10-25）：渲染器把标题写成 `_quote(menu_caption_text(...))`，
    而 `menu_caption_text` 会 **strip** —— 所以作者写的 `" "`（一个空格）
    渲染出来是"没有标题"，两边行数天然差一。把它当"用户删掉了标题"
    就会静默删掉作者那一行（真机 `script_pomo.rpy` 第 205 行）。
    """
    body = _content_of(_norm(line))
    m = _SAY_RE.match(body)
    if m is None:
        return False
    return bool((m.group('body') or '').strip())


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
        if verb == 'voice':
            # `voice` 的"目标"是资产路径，正是用户会改的那一位（和 BGM 一样）。
            # 旧键把路径算进身份：`voice "a.ogg"` 改 `b.ogg` 后配不上对，
            # 生成行被当"新内容"**插进去**、旧行还留着 —— 同一拍两条 `voice`
            # （`test_writeback_fidelity` 的 Voice 用例复现；当时只断言"新值在"
            # 所以没红）。一圈只有一条 `voice`，身份 = 关键字本身。
            return (verb, '')
        # 目标**去掉引号**再当身份：`voice v_ling_01` 与 `voice "v_ling_01"`
        # 是同一件事（生成器会补引号）—— 不归一的话它们配不上对，
        # 生成器那行会被当"新内容"插进去 → 文件里多一条 `voice`。
        return (verb, rest.strip().strip('"\'').split()[0]
                if rest.strip().strip('"\'').split() else '')
    # **控制流行**（2026-10-24，修审计 P0-5）：身份 = 动词。
    #
    # 只认 `parse_flow_line` 认得的那几种（`call X` / `jump X` / `call X(a)` /
    # `call X from Y`）：`jump expression …` / `call screen …` 继续走
    # `('other', 原文)`，它们的目标不是 label，改了不该被"同类型"放行。
    if _FLOW_START_RE.match(bare):
        from storygraph.story.flow import parse_flow_line
        if parse_flow_line(bare) is not None:
            return ('flow', bare.split()[0])
    # **菜单头**（2026-10-24，修审计 P0-8）：身份 = "这是一个菜单头"。
    # 名字 / 参数 / 标题都正是用户会改的那几位，进身份就配不上对了。
    if _MENU_HEAD_RE.match(bare):
        return ('menu', '')
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
    if not m:
        # **带尾巴的台词**（2026-10-24，修审计 P0-8「SayTail」）：
        # `e "台词" (id="l1")` / `… with vpunch` / `… # 备注` 与 `e "台词"`
        # 是同一句话，只是多了后缀 —— 键要一样，否则配不上对、零 diff。
        mt = _SAY_TAIL_RE.match(bare)
        if mt is not None:
            m = mt
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


def _say_parts(body: str):
    """
    台词行 → `(说话人, 引号里的正文)`；不是台词行 → `None`。

    与 `_line_key` 同源，但**只认"能安全换说话人"的那一种台词行**，
    所以额外排掉两类：

      · **菜单选项行** `"选项":` —— 它的"正文"是选项文案，把说话人塞进去
        会写坏菜单结构（`yhj "选项":` 不再是选项）；
      · **关键字行**（`extend "…"` / `menu "标题":`）—— 它们的"说话人位"
        上是 Ren'Py 关键字，不是角色名。

    剩下的才是"`说话人 "台词"`"这一种：换掉说话人，语句还是同一句。
    """
    s = (body or '').strip()
    bare = _content_of(s)
    if _VERB_RE.match(bare):
        return None
    if _OPTION_ROW_RE.match(s):
        return None
    m = _SAY_RE.match(bare)
    if not m:
        return None
    who = (m.group('who') or '').strip()
    if who in _KEYWORD_SPEAKERS:
        return None
    return who, m.group('body')


def _speaker_swap_ok(old_body: str, new_body: str) -> bool:
    """
    **只有说话人变了、正文一字不差**吗？（2026-10-19，修 P0-1）

    为什么单开一条判据：`_line_key` 给台词算的身份是 `('say', 说话人)` ——
    "说话人一样才允许换正文"是防"把 A 的台词写到 B 头上"的纪律。
    可用户**就是想换说话人**时，新行 `yhj "…"` 与原文 `jc "…"` 键不同、
    配不上对 → 原文一个字不动、报告"改动 0 行"、退出码 0（真实项目
    4/4 复现）。纪律执行得太彻底，反而把一个正常操作吞掉了。

    这一条把"同一句话、只换说话人"单独放行：正文（引号里那一串）
    必须逐字相同，只是 `who` 不同。**认不出、正文有一字之差就不放行**
    —— 那时用户是在改台词或换一句话，仍然走 `relax_speaker` 那条老路。
    """
    a = _say_parts(old_body)
    b = _say_parts(new_body)
    if not a or not b:
        return False
    if a[0] == b[0]:
        return False
    if not (a[1].strip() and b[1].strip()):
        return False
    return _unescaped(a[1]) == _unescaped(b[1])


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
        # **同一句话换了说话人**（2026-10-19 修 P0-1）：正文逐字相同、
        # 只有 who 变了 —— 这正是"改说话人"那一刀，必须放行。
        if _speaker_swap_ok(old_body, new_body):
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
    # **音频行**（2026-10-24 修）：`play music "a"` → `"b"` / `voice a` → `b`
    # 就是"在卡片上换一首 BGM"最普通的一刀。身份键已经只含通道（`('play',
    # 'music')`），配上了对就该允许换目标 —— 从前这里对音频一律 return False，
    # 于是补丁报告"成功"、文件里还是旧音乐（`test_writeback_fidelity` 的
    # BGM/SFX 两条当场红）。`voice` 同理（键 = 关键字）。
    if kind in ('play', 'queue', 'stop', 'voice'):
        # 只在行号已证明新鲜时换目标：源文件被外部改过（整体错位）时，
        # 同一通道的两条 `play music` 可能分属相邻两拍，按通道配对会改错行。
        # 编辑器/表格两条正常路径都会带 `trust_ranges`。
        return relax_speaker and _line_key(old_body) == _line_key(new_body)
    # **控制流**（2026-10-24，修审计 P0-5）：同类型换目标 / 参数 / from。
    #
    #   · `call side from _call_side` → `call other_side from _call_side`
    #     —— 这就是用户在控制流卡上改目标，必须换掉那一行；
    #   · `call → jump` 连类型一起变，参数与 `from` 按 Ren'Py 语义作废，
    #     只在"行号已证明新鲜 / 1:1 槽位配对"时放行（`relax_speaker`）。
    if kind == 'flow':
        from storygraph.story.flow import parse_flow_line
        fo, fn = parse_flow_line(old_body), parse_flow_line(new_body)
        if fo is None or fn is None:
            return False
        if fo['type'] == fn['type']:
            return True
        return bool(relax_speaker)
    # **菜单头**（2026-10-24，修审计 P0-8）：名字 / 参数 / 标题都是用户改的那几位。
    if kind == 'menu':
        return True
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
        if m.group('verb') == 'voice':
            # 与 `_line_key` 同口径：`voice` 的身份是关键字，不是那串路径。
            return (m.group('verb'), '')
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
        indents.add(indent_prefix(ln))
    return len(indents) <= 1


def _statement_verb(line) -> str:
    """
    这一行是**一条普通原生语句**时返回它的首词，否则 `''`。

    普通原生语句 = 工具没建模、也没有专门通道的那种：
    `pause 0.1` / `window hide` / `mycue intensity=3` / `with dissolve`。
    **不包含**：台词、菜单头、选项行、`$` 代码、注释、块头（以冒号结尾）、
    结构关键字（`if` / `return` / `label` / `jump` …）、舞台与音频（它们
    各有各的身份键）。

    用途见 `_replace_matched` 的 ⑤：首词相同的两条普通语句就是"同一条语句
    改了正文"，配成一对 —— 否则改一个数字就会**插一条新的、旧的还留着**。
    """
    body = _norm(line)
    if not body or _is_comment_line(body):
        return ''
    if _ends_with_colon(body) or _is_protected(body):
        return ''
    if body.startswith('$'):
        return ''
    if _VERB_RE.match(body) or _FLOW_START_RE.match(body):
        return ''
    if _MENU_HEAD_RE.match(body) or _OPTION_ROW_RE.match(body):
        return ''
    if _SAY_RE.match(body) or _SAY_TAIL_RE.match(body):
        return ''
    parts = body.split()
    return parts[0] if parts else ''


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
        return indent_prefix(old_line) + _norm(pick), True
    # 纯注释行：内容就是注释文字（`_line_key` 对注释是逐字比，所以只有
    # 上面那条"行号已证明新鲜 + 两边各一条"的槽位配对才会走到这里）。
    if _is_comment_line(body) and _is_comment_line(pick_body):
        return indent_prefix(old_line) + _norm(pick), True
    # **同一条原生语句换正文**（2026-10-25）：`pause 0.1` → `pause 0.2`、
    # `window hide` → `window show`、`mycue a=1` → `mycue a=2` —— 首词相同
    # 就是"这条语句改了参数"，不是"另起一条新语句"。
    # （配对在 `_replace_matched` 的 ⑤ 里完成，两者的判据同一个函数。）
    if _statement_verb(old_line) and \
            _statement_verb(old_line) == _statement_verb(pick):
        return indent_prefix(old_line) + _norm(pick), True
    if not _same_thing_unless_rewritten(body, pick, relax_speaker):
        return old_line, False            # 不是"同一件事" → 不许换
    return indent_prefix(old_line) + _norm(pick), True   # 缩进只来自原文


def _is_new_content_row(body: str, old_heads, old_squash, file_squash,
                        old_lines) -> bool:
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


def _note_removed(removed_out: list, line) -> None:
    """
    把"这一行被删掉了"记进回执预览（去缩进，最多 5 条）。

    `removed_out is None` 是合法调用（老调用点不关心这件事）—— 不记就是了。
    """
    if removed_out is None:
        return
    body = _norm(line)
    if body and len(removed_out) < 5:
        removed_out.append(body[:100])


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


def _comment_run(lines: list):
    """
    这一段里**恰好一处**连续的注释块 → `(起, 止)`（含）；否则 `None`。

    为什么只认"恰好一处"：注释块在区间里是"贴在下面那条语句上"的，一拍的
    区间里通常只有一处；出现两处说明这段的结构比我们认得的多，**不猜**。
    """
    runs = []
    start = None
    for i, ln in enumerate(lines):
        is_c = bool(_norm(ln)) and _is_comment_line(ln)
        if is_c and start is None:
            start = i
        elif not is_c and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(lines) - 1))
    return runs[0] if len(runs) == 1 else None


def _replace_matched(old_lines: list, gen_bodies: list,
                     allow_edit: bool = True, file_squash=None,
                     relax_speaker: bool = False,
                     removed_out: list = None,
                     child_pad: str = '') -> tuple:
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

    `child_pad`：这一拍的**块体缩进**（调用方按原文算，见 `_body_pad`）。
    只在"菜单标题从无到有"那一支用得上 —— 那时源里没有标题行可借，
    新行得和块体第一行对齐。
    """
    # **注释块按"用户写的那几行"整段换掉**（2026-10-24，审计 P1）。
    #
    # 现场：一拍的行区间是 [`# 旧注释`, `e "台词"`]（注释贴在下面那条语句上），
    # 用户在「注释」格里写成两行 → 旧实现只能做 1:1 配对，多出来的行被当
    # "新增内容"插进去，而旧的注释行受"注释绝不许删"保护留着 →
    # 文件里变成 `# 第一行` `# 第二行` `# 旧注释`，**越改越多**。
    #
    # 判据很窄：这一段里**恰好有一处连续的注释块**（两边都是），且行号已
    # 证明新鲜（`relax_speaker`）。此时"这段注释"就是用户刚写的那几行 ——
    # 没有别的语义可猜，原样换掉、不参与后面的配对。
    pre_changed = 0
    # **菜单标题块按"用户写的那几行"整段换掉**（2026-10-24，审计第三轮 P0-9）。
    #
    # 菜单标题是 `menu:` 块里的**裸字符串行**，Ren'Py 允许写成一行，也允许
    # 写成多行（每一行都是标题的一部分）。拍的区间是 [`menu:`, 标题行…]，
    # 渲染结果也是 [`menu:`, 标题行…] —— 可普通的配对通道只做 1:1 对位：
    # 两行标题改成一行时，多出来的那行旧标题**没有对手**，于是留在原地
    # （产物里成了两行标题：一行新的、一行旧的）→ 校验说"台词找不到"→ 拒绝。
    #
    # 判据（只认这一种形状，别的一律走老路）：
    #   · 两边的第一行是**同一个不带标题的菜单头**（`menu:` / `menu 名:`）；
    #   · 其余每一行都是**裸字符串**（没有说话人的台词形状）；
    #   · 两边的**行数不同**（行数相同时普通配对已经能逐行换正文）。
    #
    # **标题"从无到有 / 从有到无"也走这条**（2026-10-25，审计第四轮 P1）：
    # 无标题的菜单在卡片上填一句标题，从前配不上对（旧区间只有 `menu:` 一行，
    # 生成结果是两行）→ 多出来的那行标题落不了地 → 整份拒绝
    # （`1 处新增内容没能落进产物`），而"加个标题"是再普通不过的一刀。
    # 反过来把标题清空时，旧标题行没人删 → 产物里标题还在（报成功、没改）。
    # 这两种都在这条通道里：`old_lines[1:]` / `gen_bodies[1:]` 允许为空。
    if allow_edit and old_lines and gen_bodies:
        _mh = _norm(old_lines[0])
        if (_MENU_HEAD_RE.match(_mh) and _mh.endswith(':')
                and _norm(gen_bodies[0]) == _mh
                and all(_is_caption_line(x) for x in old_lines[1:])
                and all(_is_caption_line(x) for x in gen_bodies[1:])
                and len(old_lines) != len(gen_bodies)
                # **只在"行数不同"确实来自标题本身时**才动手：
                # 全是空白的标题（`" "`）渲染出来是**没有标题**
                # （`menu_caption_text` 会 strip），于是一张标题为 `" "`
                # 的菜单两边行数天然差一 —— 那不是"用户删了标题"，
                # 照删就是**静默删掉作者写的一行**（真机 `script_pomo.rpy`
                # 第 205 行当场被抓到：删相邻的 elif 分支，连带少了一行 `" "`）。
                and (len(gen_bodies) > len(old_lines)
                     or all(_caption_is_substantive(x)
                            for x in old_lines[1:]))):
            # 缩进：源里有标题行就借它（作者写的那一级）；从无到有时借
            # **块体第一行的缩进**（`child_pad`，由调用方按原文算），
            # 拿不到就按渲染结果那一行的相对缩进垫一级。
            if len(old_lines) >= 2:
                _pad = indent_prefix(old_lines[1])
            else:
                _pad = (child_pad or (indent_prefix(old_lines[0])
                                      + (indent_prefix(gen_bodies[1])
                                         if len(gen_bodies) >= 2 else '    ')))
            _new_caps = [_pad + x for x in gen_bodies[1:]]
            pre_changed = max(len(old_lines) - 1, len(_new_caps))
            old_lines = [old_lines[0]] + _new_caps
            gen_bodies = [gen_bodies[0]]
    if relax_speaker:
        _o_run = _comment_run(old_lines)
        _g_run = _comment_run(gen_bodies)
        if _o_run and _g_run:
            _oa, _oz = _o_run
            _ga, _gz = _g_run
            _old_c = [_norm(x) for x in old_lines[_oa:_oz + 1]]
            _gen_c = [_norm(x) for x in gen_bodies[_ga:_gz + 1]]
            # **行尾注释被渲染器搬到拍首**的那一类不算新内容（2026-10-24）。
            #
            # 渲染器会把 `show ling close_happy  # 27无图片…` 的行尾注释
            # 提到块首，于是生成结果里出现**两条**注释：一条是「注释」列，
            # 一条是刚搬上来的行尾注释 —— 而原文里那行行尾注释**还在**
            # （舞台行不会被改写）。整段照抄就会写出两条重复注释
            # （真机 `chapter1_4.rpy` / `screens.rpy` 各一处，金标当场红）。
            # 所以：生成注释的正文如果已经是这一段里某行的行尾注释，丢掉它。
            _inline = set()
            for _ln in old_lines:
                _bare = _content_of(_norm(_ln))
                _full = _norm(_ln)
                # ⚠ `_bare` 必须非空：纯注释行的"内容"就是空串，
                # 否则它自己会被当成"某行的行尾注释"→ 把自己的注释滤掉
                # （第一版就这么把整段注释删没了，金标抓到的）。
                if _bare and _bare != _full \
                        and _full[len(_bare):].lstrip().startswith('#'):
                    _inline.add(_full[len(_bare):].lstrip()[1:].strip())
            _gen_c = [c for c in _gen_c
                      if c.lstrip('#').strip() not in _inline]
            if _old_c != _gen_c:
                _pad = indent_prefix(old_lines[_oa])
                old_lines = (list(old_lines[:_oa])
                             + [_pad + x for x in _gen_c]
                             + list(old_lines[_oz + 1:]))
                gen_bodies = (list(gen_bodies[:_ga])
                              + list(gen_bodies[_gz + 1:]))
                pre_changed = max(len(_old_c), len(_gen_c))
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

    # —— ② **同一句话、只换说话人**的第二轮配对（2026-10-19 修 P0-1）——
    #
    # 第一轮按 `('say', 说话人)` 配对：说话人一改，键就不同 → 配不上 →
    # 生成行成了"没落地"，原文一个字不动、报告"改动 0 行"。
    # 可"正文一字不差、只有说话人变了"正是用户点下拉要做的事 ——
    # 这时配对是**确定的**（同一区间里同一句话，不存在第二个候选），
    # 不需要任何语义猜测，也不该被"认不出就保守"挡掉。
    #
    # ⚠ 与 `relax_speaker` 分开：那条要"行号已证明新鲜"，这条只要求
    # **正文逐字相同** —— 比行号判据更强，表格那条路也能安全用。
    free_gen = [k for k in range(len(gen_bodies)) if k not in used]
    if free_gen:
        for j, ln in enumerate(old_lines):
            if j in pick_of_old:
                continue
            for pos, idx in enumerate(free_gen):
                if _speaker_swap_ok(_norm(ln), gen_bodies[idx]):
                    used.add(idx)
                    pair_of_gen[idx] = j
                    pick_of_old[j] = idx
                    del free_gen[pos]
                    break

    # —— ③ 同一拍里**只剩一行台词** → 槽位配对（2026-10-21 修 P0 重复行）——
    #
    # 真机（`pomodorro.rpy` 第 28 行，表格路径）：把说话人 `jc` 和正文同时改掉，
    # 导出回执「改动 2 行」、退出码 0，文件里却是：
    #
    #     yhj "【同改】…"          ← 新行被"插入"
    #     jc "好了，我休息好了…"     ← 旧行被"台词不许删"留着
    #
    # 同一句话出现两遍。根因是前两轮配对都靠**内容**（说话人 / 正文）：
    # 两者同时变，就与原文哪一行都配不上 → 走"新增内容"通道；而旧台词
    # 又不许删（`_may_drop_line`）—— 恰好是"插进去 + 留下"。
    #
    # 但一拍**最多渲染一行台词**（选项行 / `extend` / `voice` 都被
    # `_say_parts` 排除），所以"旧区间剩一行、生成块也剩一行"时，配对是
    # **确定性的**，不需要任何语义猜测：那一行就是这一拍自己的台词。
    # 只在这两个"都恰好一行"时生效 —— 多行一律不做猜测。
    #
    # `slot_pairs` 里的配对按"槽位替换"处理（允许说话人与正文同时变），
    # 仍由 `_verify_patch`（重新解析 + 内容全在）兜底。
    slot_pairs: set = set()
    old_slots = [j for j in range(len(old_lines))
                 if j not in pick_of_old and _say_parts(_norm(old_lines[j]))]
    gen_slots = [i for i in range(len(gen_bodies))
                 if i not in used and _say_parts(_norm(gen_bodies[i]))]
    if len(old_slots) == 1 and len(gen_slots) == 1:
        _j, _idx = old_slots[0], gen_slots[0]
        used.add(_idx)
        pair_of_gen[_idx] = _j
        pick_of_old[_j] = _idx
        slot_pairs.add(_idx)

    # —— ②' 注释行：**只在行号已证明新鲜时**做 1:1 槽位配对 ——
    #
    # 注释的身份就是它的文字（`('other', 原文)`），所以"改一句注释"配不上对
    # → 生成行被当新内容**插进去**、旧行又不许删 → 文件里多一条旧注释
    # （`test_writeback_fidelity` 的 Comment 用例：行数 +1）。
    # 但注释不能像台词那样无条件放宽：源文件整体错位一行时，`# 开场注释`
    # 会正好落到下一条注释 `# 该谁说话了` 的区间里 —— 按"同槽位"改写就是
    # 把别人的注释冲掉（`test_stale_ranges_never_change_the_file` 抓到的正是
    # 这个）。所以只认 `relax_speaker`（= 会话核对过"磁盘上那份 == 载入时
    # 那份"）这一条路，并且**两边都恰好一条注释**才敢配。
    if relax_speaker:
        old_c = [j for j, ln in enumerate(old_lines)
                 if j not in pick_of_old and _is_comment_line(ln)]
        gen_c = [i for i in range(len(gen_bodies))
                 if i not in used and _is_comment_line(gen_bodies[i])]
        if len(old_c) == 1 and len(gen_c) == 1:
            _j, _idx = old_c[0], gen_c[0]
            used.add(_idx)
            pair_of_gen[_idx] = _j
            pick_of_old[_j] = _idx
            slot_pairs.add(_idx)          # 允许 `_rewrite_line` 换内容

    # —— ②'' 控制流：`call` → `jump` 这类**连类型都变了**的 1:1 配对 ——
    #
    # 第一轮的身份是 `('flow', 动词)`，所以"改目标"能配上对（P0-5 的主刀），
    # 但 `call side` → `jump other` 配不上：新行被当"新增内容"**插进去**、
    # 旧行受 `_PROTECTED_RE` 保护不许删 → 两条控制流，剧情跑两遍。
    #
    # 只在**两边数量相等**时按顺序 1:1 配（同一段区间里的控制流本来就有序）：
    # 数量不等时无从判断哪条替哪条，宁可让它进 `unlanded`（→ ok=False 说清楚）。
    # 计数相等但 >1 时**要求行号已证明新鲜**（`relax_speaker`）——
    # 那种"同一个区间里两条 call 各改各的"在过期行号上猜不得。
    old_fl = [j for j in range(len(old_lines))
              if j not in pick_of_old and _line_key(_norm(old_lines[j]))[0] == 'flow']
    gen_fl = [i for i in range(len(gen_bodies))
              if i not in used and _line_key(gen_bodies[i])[0] == 'flow']
    if old_fl and len(old_fl) == len(gen_fl) \
            and (relax_speaker or len(old_fl) == 1):
        for _j, _idx in zip(old_fl, gen_fl):
            used.add(_idx)
            pair_of_gen[_idx] = _j
            pick_of_old[_j] = _idx
            slot_pairs.add(_idx)

    # —— ⑤ **同一条原生语句换正文**（2026-10-25，审计第四轮 P2 连带）——
    #
    # 现场（把自定义语句的参数写进「代码」格之后暴露的）：一拍里既有
    # `mycue intensity=3` 又有台词时，把它改成 `mycue intensity=5`：
    # 身份键是 `('other', 整行原文)` —— 改一个数字就配不上对 → 新行走
    # "新增内容"通道**插进去**、旧行又没人删 → 产物里两条语句并存
    # （剧情跑两遍：`pause 0.1` → `pause 0.2` 会停顿两次）。
    #
    # 判据（窄到只有这一种）：两边都是 `('other', …)`、**首词相同**、
    # 首词不是结构关键字（`if` / `return` / `label` …）、都不是注释、
    # 都不以冒号结尾（那是块头，改它就是改结构）。同一段区间里按顺序 1:1 配。
    _free_old = [j for j in range(len(old_lines))
                 if j not in pick_of_old
                 and _statement_verb(old_lines[j])]
    _free_new = [i for i in range(len(gen_bodies))
                 if i not in used
                 and _statement_verb(gen_bodies[i])]
    if _free_old and _free_new:
        _by_verb: dict = {}
        for _idx in _free_new:
            _by_verb.setdefault(_statement_verb(gen_bodies[_idx]),
                                []).append(_idx)
        for _j in _free_old:
            _cand = _by_verb.get(_statement_verb(old_lines[_j])) or []
            if not _cand:
                continue
            _idx = _cand.pop(0)
            used.add(_idx)
            pair_of_gen[_idx] = _j
            pick_of_old[_j] = _idx
            slot_pairs.add(_idx)          # 允许 `_rewrite_line` 换正文

    out: list = []
    changed = pre_changed
    grown = 0
    shrunk = 0
    landed_idx = set()

    # `_rewrite` 已提到模块级（`_rewrite_line`，M4 拆分）—— 它是纯判定表。

    # 单调性：只有"配对顺序和渲染顺序一致"时才敢按渲染顺序合并（否则会重排原文）
    order = [pair_of_gen[i] for i in sorted(pair_of_gen)]
    monotonic = all(order[t] < order[t + 1] for t in range(len(order) - 1))
    # **结构头不算这一拍的内容**（2026-10-19，修 pomodorro "改注释 0 段"）：
    # 条件拍的渲染结果是 `if 条件:` + 块体，而块头不在拍的行区间里。
    # 从前拿**整串**去判"平整区间"，一有 `if` 就判不平整 → 增删通道整个关掉
    # → 用户给一句 if 里的旁白加**注释**（注释是单独一行）时什么都不落地，
    # 报告"改动 0 行"。判平整时先摘掉开头的结构行，插入循环也跳过它们
    # （那几行不是这一拍的内容，插进去就是重复块头）。
    body_gen = _body_lines(gen_bodies)
    head_prefix = len(gen_bodies) - len(body_gen)
    editable = (allow_edit and monotonic and _flat_editable_range(old_lines)
                and _flat_editable_range(body_gen))
    old_heads = {_head_of(_norm(ln)) for ln in old_lines if _norm(ln)}
    gen_heads = {_head_of(_norm(g)) for g in gen_bodies if _norm(g)}
    old_squash = [_squash(ln) for ln in old_lines if _squash(ln)]
    gen_squash = [_squash(g) for g in gen_bodies if _squash(g)]

    # `_is_new_content` 已提到模块级（`_is_new_content_row`，M4 拆分）—— 它是纯判定表。

    if editable:
        # `_may_drop` 已提到模块级（`_may_drop_line`，M4 拆分）—— 它是纯判定表。

        cursor = 0
        for idx, g in enumerate(gen_bodies):
            if idx < head_prefix:
                continue                  # `if …:` 头不是这一拍的内容（见上）
            j = pair_of_gen.get(idx)
            if j is None:
                body = _norm(g)
                # 准入不过 → 不插（保持未落地，调用方据此退回全量，绝不写重复行）
                if not _is_new_content_row(body, old_heads, old_squash,
                                           file_squash, old_lines):
                    continue
                pad = indent_prefix(old_lines[0]) if old_lines else ''
                out.append(pad + body)
                landed_idx.add(idx)
                grown += 1
                changed += 1
                continue
            # 游标到 j 之间：原文有、生成器没有的行 → 能删就删，否则原样保留
            for t in range(cursor, j):
                if _may_drop_line(old_lines[t], gen_heads, gen_squash, gen_bodies):
                    _note_removed(removed_out, old_lines[t])
                    shrunk += 1
                    changed += 1
                else:
                    out.append(old_lines[t])
            new_line, did = _rewrite_line(old_lines[j], g,
                                          relax_speaker or idx in slot_pairs)
            out.append(new_line)
            landed_idx.add(idx)
            changed += 1 if did else 0
            cursor = j + 1
        for t in range(cursor, len(old_lines)):
            if _may_drop_line(old_lines[t], gen_heads, gen_squash, gen_bodies):
                _note_removed(removed_out, old_lines[t])
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
            new_line, did = _rewrite_line(ln, gen_bodies[idx],
                                          relax_speaker or idx in slot_pairs)
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


def _lost_content(unlanded: list, file_squash: set, file_loose: set) -> bool:
    """
    这一拍有**内容行既没落地、整份原文里也没有**吗？（2026-10-19 修 P0-1）

    与 `_unpairable_new_content` 的分工：
      · 那个问"是不是新内容"，用 `_head_of`（台词按正文）—— 换说话人时
        头相同，所以它答"不是新的"，然后整件事就落在缝里；
      · 这个只问一件事：**这一行内容在产物里到底有没有**。没有 → 这一拍
        被静默 no-op 了，必须报（`lost_new` → 退回全量并说明原因）。

    ⚠ 比对的是**整份文件**，不是这一拍的区间：菜单标题一类会因为解析器
    把它和 `menu:` 头并成一行而"落在别的拍里"，按区间比会把一次零改动
    往返判成丢失（实测 7 个既有用例当场红）。区间里找不到但文件里确实
    有这一句 → 不算丢。

    只认内容行（台词 / 舞台 / 音频），注释与结构行不算 —— 那些没落地是
    正常的（拍内注释本来就贴不回去）。
    """
    if not unlanded:
        return False
    for g in unlanded:
        n = _norm(g)
        if not n or _is_missing_note(n):
            continue
        if _line_key(n)[0] not in ('say', 'show', 'hide', 'scene',
                                   'play', 'stop', 'queue'):
            continue
        if _squash(n) in file_squash or _loose(n) in file_loose:
            continue                     # 文件里已经有这一行 → 没丢
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


def _condition_reasons(new_beats: list, beats: list,
                       renumber=()) -> list:
    """
    同一句台词的条件变了没有？返回原因列表（空 = 都对得上）。

    判据按**内容身份**配对（label + 说话人 + 台词），不是全局多重集 ——
    后者会被"源里还留着的其它拍"误伤（实测两条既有用例：控制流文件里
    未参与编辑的 `jump` 拍、以及同文案出现在两个分支里）。

    `renumber`（2026-10-24，审计第三轮 P0-10）：`(label, 旧条件, 新条件)`。
    删掉一条分支之后，**后面那几条分支的条件会被解析器重新累加**（否定项里
    少了被删的那一支），而文件里作者写的 `elif` 一个字都没动 —— 这不是
    "条件被改了"，是"这一支没了"。这里按解析器口径把新条件算好交进来
    （见 `_chain_renumber`），只容忍**恰好这一对、且要求完全相等**。
    """
    allowed: dict = {}
    for label, old, new in (renumber or ()):
        allowed[(str(label or ''), str(old or '').strip())] = \
            str(new or '').strip()

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
        exp = allowed.get(((b.label or ''), want))
        if exp and exp in pool:
            pool.remove(exp)                  # 删掉一支 → 解析器的重编号
            continue
        if pool:                              # 台词在、条件变了
            bad.append((b, want, pool[0]))
    return bad


def _condition_hint(bad: list, original: str) -> str:
    """
    "条件对不上"这条拒因的**具体**说法（2026-10-25，审计第四轮 P1）。

    从前一律说"条件改动走全量" —— 可条件改动里有一类**是能做增量的**
    （链头 / 独立 `if`，见 `_plan_condition_rewrites`）。剩下这几类做不了，
    原因各不相同，笼统的一句话让人不知道下一步该做什么：

      · `elif` / `else`：表里存的是**展平后的累积条件**（`(b) and not (a)`），
        改它没法用一行 `elif` 表达 —— 要改这一支得连链头一起重写；
      · 嵌在别的块里（`if` 套 `if`）：改的只是内层那一行，累积条件里还有外层，
        光改一行写不出用户填进去的那句话；
      · 清空条件 = 拆掉这一层 `if`（结构改动）。

    另外把 `_plan_condition_rewrites` 说出来的**拒绝理由**（"清空条件"那种）
    一并带上 —— 它比这里更早、更清楚。
    """
    b, want, got = bad[0]
    first = int(getattr(b, 'src_first', 0) or 0)
    lines = (original or '').splitlines()
    head_no, kind = (None, '')
    if first >= 2 and first <= len(lines):
        head_no, kind = _branch_head_for(lines, first)
    if kind in ('elif', 'else'):
        why = ('—— 这一支是 `elif` / `else`：表里存的是展平后的累积条件'
               '（`(b) and not (a)`），改它没法用一行表达。'
               '改**链头**那一支的条件可以增量；要改这一支请全量重写这一章')
    elif str(getattr(b, 'nest_path', '') or '').count('>') > 0:
        why = ('—— 这一拍嵌在别的块里（`if` 里还有 `if`）：累积条件里含着外层条件，'
               '光改这一行写不出你填的那句话，请全量重写这一章')
    else:
        why = '—— 条件改动走全量'
    return (f'{len(bad)} 处条件与要写的拍对不上'
            f'（例：{((want or "")[:40], (got or "")[:40])}）{why}')


def _verify_conditions(text: str, beats: list, renumber=()) -> list:
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
        return [_condition_hint(bad, text)]
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
                  symbols=None, renumber=()) -> list:
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

    `renumber`：删掉一条分支之后，后面几支条件的**可接受重编号**
    （见 `_chain_renumber` / `_condition_reasons`）。
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
    cond_bad = _condition_reasons(new_beats, beats, renumber=renumber)
    if cond_bad:
        reasons.append(_condition_hint(cond_bad, text))

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

    **`into_body`（2026-10-23）**：锚点是**块头**、而这一拍在模型里
    **就在某个选项体里**（`nest_path` 含 `>`，如 `menu:1>A`）时，插到这个
    选项的体里，不要退到块外。
    实测：选项头 `"去屋顶":` 后面插一张 `call roof`，模型放进选项体
    （`nest_path='menu:1>A'`）、全量渲染也在体里，补丁却把它退到 `menu:`
    之后、和选项同级 —— 语法合法、语义已经改了，还报 `ok=True`。
    `_insert_anchor` 那条"退到最外层包围块"的规矩是给**选项体的兄弟卡**
    用的（`tests/test_incremental_export.py::TestInsertIntoBlockStaysStructurallyValid`），
    两类不能共用一条。

    返回 `(anchors, failed, into_body)`。
    """
    anchors: dict = {}
    into_body: set = set()
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
        nest = str(getattr(beats[i], 'nest_path', '') or '').strip()
        # **只认"选项体里"这一种**（`menu:1>A`）：模型把这一拍放进了某个选项的
        # 体里。`menu:1`（菜单层、还不属于任何选项）不在此列 —— 那种状态的
        # 整份渲染本身就会写出 `# TODO` + 非法行（见 `beat_render`），补丁这边
        # 保持老规矩"退到块外"，至少产物是能读的。
        if '>' in nest:
            into_body.add(point)
    return anchors, failed, into_body


def _deletion_empties_block(orig_lines: list, first: int, last: int) -> bool:
    """
    删掉 `[first, last]` 之后，上一级块会**变成空块**吗？（2026-10-19 修 P1-3）

    真实事故（`pomodorro.rpy`，删一拍）：

        if breakline == 1:
            "我需要休息一下."      ← 表里删掉了这一拍
        elif breakline == 2:

    上一版只看"区间里有没有块头"，于是照删不误 —— 产物在 `elif` 那一行
    `expected statement`（引擎判据实测第 232 行）。门禁拦住了，没写坏文件，
    但结论是"退回全量"，而不是"这一刀不敢增量"。

    判据：被删区间的上一行（跳过空行）是块头、且比区间更深；
    再往下扫，**块体里没有一行活着**（先遇到 dedent = 块结束）→ 空块。
    """
    if first < 2 or first > len(orig_lines):
        return False

    def _transparent(line: str) -> bool:
        """空行 / 纯注释：Ren'Py 眼里不算块体语句。"""
        body = _norm(line).strip()
        return not body or body.startswith('#')

    idx = first - 2
    while idx >= 0 and _transparent(orig_lines[idx]):
        idx -= 1
    if idx < 0:
        return False
    head = orig_lines[idx]
    if not _content_of(_norm(head)).rstrip().endswith(':'):
        return False
    head_ind = indent_width(head)
    if head_ind >= indent_width(orig_lines[first - 1]):
        return False                       # 上一行不是这一段的"父块头"
    for j in range(first - 1, len(orig_lines)):
        ln = orig_lines[j]
        if _transparent(ln):
            continue                       # 空行 / 注释救不了空块
        if indent_width(ln) <= head_ind:
            return True                    # 块已经结束，里面一行都不剩
        if not (first - 1 <= j <= last - 1):
            return False                   # 还有别的块体行活着
    return True


def _branch_head_for(orig_lines: list, first: int) -> tuple:
    """
    `first` 这一行的**上一级块头**在哪？（0-based 下标 + 类型）

    类型：`'if'` / `'elif'` / `'else'`（条件分支），`'option'`（菜单选项
    `"文案":`），`'other'`（别的东西，比如 `python:`）。

    判据与 `_deletion_empties_block` 同源：往上跳过空行/注释，第一个
    **缩进比块体浅、且以冒号结尾**的行就是块头。
    """
    idx = first - 2
    while idx >= 0:
        body = _norm(orig_lines[idx]).strip()
        if body and not body.startswith('#'):
            break
        idx -= 1
    if idx < 0:
        return None, ''
    head = orig_lines[idx]
    body = _content_of(_norm(head)).strip()
    if not body.endswith(':'):
        return None, ''
    if indent_width(head) >= indent_width(orig_lines[first - 1]):
        return None, ''
    m = re.match(r'^(if|elif|else)\b', body)
    if m:
        return idx, m.group(1)
    if _OPTION_ROW_RE.match(_norm(head)):
        return idx, 'option'
    return idx, 'other'


def _next_sibling(orig_lines: list, head_no: int, last: int):
    """
    块头 `head_no` 这一段的**下一个兄弟行**（1-based 行号），没有就 `None`。

    "兄弟" = 缩进与块头相同的第一行；缩进更浅 = 块结束。空行与注释跳过
    （它们不属于任何一层）。
    """
    head_ind = indent_width(orig_lines[head_no - 1])
    for n in range(last + 1, len(orig_lines) + 1):
        line = orig_lines[n - 1]
        body = _norm(line).strip()
        if not body or body.startswith('#'):
            continue
        ind = indent_width(line)
        if ind == head_ind:
            return n
        if ind < head_ind:
            return None
    return None


#: `menu "标题":` —— 标题**写在头行上**的那种写法（引擎其实不收，但解析器认）。
#:
#: 解析器支持它（`_scan_menu_header` 的第一支），所以编排层**必须能原样还原**：
#: 零改动往返一个字节都不许动，改标题也只许动这一行。
_MENU_HEAD_CAPTION_RE = re.compile(
    r'^(?P<pre>\s*menu\s+)(?P<q>"|\')(?P<cap>(?:\\.|[^"\\])*)(?P=q)'
    r'(?P<post>\s*(?:\(.*\))?\s*:.*)$')


def _menu_head_caption_merge(lines: list, beat, first: int, last: int,
                             orig_lines: list) -> list:
    """
    `menu "标题":` 这种**标题在头行**的写法 → 把渲染出来的两行合并回头行。

    渲染器**故意**把标题写进块里（`menu:` + `    "标题"`）：Ren'Py 的 `menu`
    头只接受 `menu [名] [(参数)]:`，**不接受字符串**（写成头行形式引擎直接
    `expected ':' not found`，见 `beat_render` 那段注释）。所以整份渲染必须
    用块内形式 —— 这也正是解析器要把块内标题合并进头部的反面。

    可**源文件里就是这么写的**（别的工具/别的引擎导出来的，或者作者手写坏
    的）时，增量补丁必须把这一行**照原样还回去**：

      · 零改动往返：产物必须逐字节等于原文（否则"我什么都没改，它却报
        `1 处新增内容没能落进产物`" —— 审计第三轮 P0-9 的合成样本）；
      · 改标题：只许动这一行，选项与选项体一个字节不动。

    只认"恰好一行、恰好一个标题行"的情形；多行标题 / 头行还带菜单名或参数
    的一律不合并（渲染形状与源形状对不上时不猜，交给调用方拒绝）。
    """
    from storygraph.story.beat_model import KIND_MENU

    if not orig_lines or first != last or first < 1 or first > len(orig_lines):
        return lines
    if str(getattr(beat, 'kind', '') or '') != KIND_MENU:
        return lines
    src = orig_lines[first - 1]
    m = _MENU_HEAD_CAPTION_RE.match(src)
    if m is None:
        return lines                  # 源文件不是"标题在头行"的写法
    if len(lines) != 2:
        return lines                  # 多行标题：头行放不下换行，不猜
    head = _norm(lines[0])
    # 行尾注释不参与合并：渲染器会把这一拍的行尾注释贴到标题那一行上，
    # 而原文的注释本来就在头行末尾（`post` 里），照抄会写出两份
    # （实测：`menu "标题":  # 备注` → `menu "标题"  # 备注:  # 备注`）。
    cap = _content_of(_norm(lines[1]))
    if head != 'menu:' or not re.match(r'^(?:"|\')', cap):
        return lines                  # 头行还带名字/参数 → 形状对不上，不猜
    return [m.group('pre') + cap + m.group('post')]


def _split_negation(cond: str) -> tuple:
    """
    展开成条件的一条分支 → `(前半, [否定项…])`（"`P and not ((a) or (b))`"）。

    只认解析器写出来的那两种形状（`_w_if` 的口径，别的形态一律原样返回
    `(整条, [])`，调用方据此放弃）：

        (b) and not ((a))                 → ('(b)', ['(a)'])
        not ((a) or (b))                  → ('',    ['(a)', '(b)'])

    `or` 只在**顶层**切：否定项本身可能带括号里的 `or`（`not ((a or b) or (c))`）。
    """
    body = (cond or '').strip()
    idx = body.rfind(' and not (')
    if idx >= 0 and body.endswith(')'):
        head = body[:idx].strip()
        neg = body[idx + len(' and not ('):-1]
    elif body.startswith('not (') and body.endswith(')'):
        head = ''
        neg = body[len('not ('):-1]
    else:
        return body, []
    terms: list = []
    i, n = 0, len(neg)
    while i < n:
        if neg[i] in ' \t':
            i += 1
            continue
        if neg[i] != '(':
            return body, []                # 认不出的写法 → 不猜
        depth, j = 0, i
        while j < n:
            if neg[j] == '(':
                depth += 1
            elif neg[j] == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            return body, []
        terms.append(neg[i:j + 1])
        i = j + 1
        if neg[i:i + 4] == ' or ':
            i += 4
        elif i < n and neg[i:].strip():
            return body, []
    return head, terms


def _bare_cond(term: str) -> str:
    """`(a)` → `a`；本身没有外层括号就原样返回。"""
    s = (term or '').strip()
    if len(s) >= 2 and s[0] == '(' and s[-1] == ')':
        depth = 0
        for k, ch in enumerate(s):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    return s[1:-1].strip() if k == len(s) - 1 else s
    return s


def _own_condition(head_line: str) -> str:
    """`elif a == 1:` / `if a == 1:` → `a == 1`（`else:` → 空）。"""
    body = _norm(head_line).strip()
    m = re.match(r'^(?:if|elif)\s+(.*):$', body)
    return m.group(1).strip() if m else ''


def _chain_renumber(blocks, orig_lines: list, head_no: int, last: int):
    """
    删掉分支 `[head_no .. last]` 之后，**后面那些分支的条件**要跟着重算。

    为什么需要（2026-10-24，审计第三轮 P0-10）：拍上存的条件是**展平后的
    累积形式**（第 3 支是 `(b3) and not ((b1) or (b2))`）。删掉第 2 支的头行
    之后，解析器从头累加出来的条件里就**没有 `(b2)` 了** —— 与拍上存的对不上
    → `_verify_conditions` 判"条件改动走全量"。

    上一版的做法是**把后面的 `elif` 改写成展平的 `if`**（`if (b3) and not
    ((b1) or (b2)):`），语义等价、校验也过 —— 但那是**用户没碰过的结构行**，
    diff 一大片，评审时看不出是工具干的。审计的判据只有一条：
    **`elif` / `else` / `if` 行逐字节不动**。

    现在改成：文件里作者的结构行一个字节不动，**条件在模型侧对账** ——
    这里算出"删掉这一支之后，后面每支的条件该长什么样"，交给
    `_verify_patch` 当作**可接受的重编号**（只容忍这一处，且要求完全相等）。
    唯一还得动结构行的是"被删的正好是链头 `if`"：紧跟的 `elif` 必须提升成
    `if`（Ren'Py 的链必须以 `if` 开头），那也只是**换一个词**，条件正文不动。

    返回 `(rewrites, renumber)`：

      · `rewrites`：`[(行号, 新整行)]` —— 只有"链头提升"这一种；
      · `renumber`：`[(label, 旧条件, 新条件)]` —— 容忍对调的条件。

    认不出来（条件形状不是解析器写的那两种、或对不上拍的区间）→ `None`
    （调用方按"删这一拍不安全"处理，绝不猜）。
    """
    if head_no < 1 or head_no > len(orig_lines):
        return None
    own_deleted = _own_condition(orig_lines[head_no - 1])
    kind_deleted = _norm(orig_lines[head_no - 1]).strip().split(None, 1)[0]
    kind_deleted = kind_deleted.rstrip(':')
    if kind_deleted == 'else':
        return [], []                      # `else` 永远是链尾，后面没有分支
    if not own_deleted:
        return None
    head_ind = indent_width(orig_lines[head_no - 1])
    rewrites: list = []
    renumber: list = []
    first_later = True
    n = last + 1
    while n <= len(orig_lines):
        line = orig_lines[n - 1]
        body = _norm(line).strip()
        if not body or body.startswith('#'):
            n += 1
            continue
        ind = indent_width(line)
        if ind < head_ind:
            break                          # 链结束了
        if ind != head_ind:
            n += 1
            continue
        tag = body.split(None, 1)[0].rstrip(':')
        if tag not in ('elif', 'else'):
            break
        first_body = n + 1
        while first_body <= len(orig_lines):
            b2 = _norm(orig_lines[first_body - 1]).strip()
            if b2 and not b2.startswith('#'):
                break
            first_body += 1
        beat = next((blk[4] for blk in (blocks or ())
                     if blk[1] == first_body), None)
        if beat is None:
            return None                    # 找不到那一拍 → 不猜
        label = str(getattr(beat, 'label', '') or '')
        old_cond = str(getattr(beat, 'condition', '') or '').strip()
        if not old_cond:
            return None
        prefix, terms = _split_negation(old_cond)
        if not terms:
            return None                    # 形状认不出（不是解析器写的那一个）
        if first_later and kind_deleted == 'if':
            # 被删的是链头 → 这一支变成 `if`，条件就是它自己的原文。
            own = _own_condition(line)
            if not own:
                return None
            rewrites.append((n, indent_prefix(line) + f'if {own}:'))
            renumber.append((label, old_cond, own))
            first_later = False
            n = first_body
            continue
        kept = [t for t in terms if _bare_cond(t) != own_deleted]
        if len(kept) == len(terms):
            return None                    # 否定项里找不到被删的条件 → 不猜
        neg = ' or '.join(kept)
        new_cond = (f'{prefix} and not ({neg})' if prefix
                    else f'not ({neg})')
        renumber.append((label, old_cond, new_cond))
        first_later = False
        n = first_body
    if first_later:
        return [], []                      # 后面没有兄弟分支了
    return rewrites, renumber


def is_flattened_condition(cond: str) -> bool:
    """
    这条条件是解析器**展平后的累积形式**吗（`(b) and not ((a))`）？

    只有 `elif` / `else` 那几支是这样；`if` / 独立条件存的就是它自己。
    `app.session_table` 靠它判断"改的是不是链头"（跨包只许用公开名字，
    见 `tests/test_package_layering.py`）。
    """
    return bool(_split_negation(str(cond or ''))[1])


def rechain_condition(cond: str, old_own: str, new_own: str):
    """
    把一条**展平后的累积条件**里"某一支自己的条件"换掉（P1，2026-10-25）。

    为什么要换：`elif` / `else` 的守卫在表里是**累积形式**
    （第 3 支 = `(b3) and not ((b1) or (b2))`）。用户把链头那一支从
    `a == 1` 改成 `a == 9` 之后，后面每一支的否定项里都还是 `(a == 1)` ——
    文件（`elif a == 2:`）重新解析出来的必然是 `not (a == 9) and (a == 2)`，
    与表里的对不上；而**整份重写**又按表里的旧条件写，两边的语义就分家了。

    所以链头一改，后面那几支的条件**跟着重算**（与文件里的 `elif` 语义一致）。

    返回新条件；形状认不出 / 否定项里找不到 `old_own` → `None`（调用方不猜）。
    """
    prefix, terms = _split_negation(cond)
    if not terms:
        return None
    want = str(old_own or '').strip()
    repl = str(new_own or '').strip()
    if not want or not repl:
        return None
    out: list = []
    hit = False
    for term in terms:
        if not hit and _bare_cond(term) == want:
            out.append(f'({repl})')
            hit = True
        else:
            out.append(term)
    if not hit:
        return None
    neg = ' or '.join(out)
    return f'{prefix} and not ({neg})' if prefix else f'not ({neg})'


def _has_outer_head(orig_lines: list, head_idx: int) -> bool:
    """
    第 `head_idx` 行（0-based）那一层**外面还有没有块**？

    有就不再改那一行：表里存的是累积条件，而外层 `if` 里的内层 `if` 只写得下
    内层那半句 —— 把累积条件写进去，语义就重了（`if a:` 里套
    `if a and b:`）。`label` 不算外层（它是章边界，不是块）。
    """
    if head_idx is None or head_idx < 0 or head_idx >= len(orig_lines):
        return True
    ind = indent_width(orig_lines[head_idx])
    for k in range(head_idx - 1, -1, -1):
        ln = orig_lines[k]
        body = _norm(ln).strip()
        if not body or body.startswith('#'):
            continue
        if indent_width(ln) < ind:
            if _LABEL_LINE_RE.match(ln):
                return False
            return _ends_with_colon(ln)
    return False


def _beat_owns_head(orig_lines: list, head_idx: int, first: int) -> bool:
    """块头 `head_idx` 与拍的首行 `first` 之间只有空行/注释吗？"""
    if head_idx is None or head_idx < 0 or first < 1:
        return False
    if head_idx + 2 > first:               # 头必须在拍首的上面（至少差一行）
        return False
    for n in range(head_idx + 2, first):
        body = _norm(orig_lines[n - 1]).strip()
        if body and not body.startswith('#'):
            return False
    return True


def _plan_condition_rewrites(beats, orig_lines: list, skip_ids=()) -> tuple:
    """
    改「条件」那一格 → **把它写回那一行 `if …:`**（P1，2026-10-25）。

    从前条件永远是"改动走全量"：条件不在拍的行区间里（块头不属于任何一拍，
    见 `_body_lines`），逐拍对位看不见它 → 产物里条件一行没动 → 校验判
    "1 处条件与要写的拍对不上" → 整份拒绝。用户看着卡片上改好的条件，
    文件里却还是旧的，只能换条路（导出到 build/ 再手工搬回去）。

    现在：只认**最深不超过一层**的那一支（判据见 `_has_outer_head`），
    而且这一行必须就是**这一拍自己的块头**（`_beat_owns_head`）。
    满足就换那一行的条件正文，**别的字节一个不动**：

        if flag:            →   if not flag:
            e "在里面"              e "在里面"

    返回 `(rewrites, rejects)`：

      · `rewrites`：`[(行号, 新整行)]`；
      · `rejects`：`[原因]` —— 只在"确实改了、但改不了"时说话
        （清空条件 = 拆掉这一层 `if`）。

    `elif` / `else` 与嵌套的 `if` 一律不碰：它们"改了没有"判不出来
    （表里存的是累积条件，本来就和文件那一行不同），交给
    `_verify_conditions` 去拒绝并说清楚（见 `_condition_hint`）。
    """
    rewrites: list = []
    rejects: list = []
    for i, b in enumerate(beats):
        if i in skip_ids:
            continue
        # **只认"条件就是这一拍自己的守卫"的那几种拍**（台词 / 旁白）。
        #
        # 菜单与菜单选项的条件不走 `Beat.condition`（它们在
        # `Beat.menu_conditions` 里，渲染与校验另有通道），把它们的空条件
        # 当成"用户清空了条件"会误判 —— 实测 `test_patch_menu_condition`
        # 的 `if …: menu:` 当场被判"清空条件"而整份拒绝。
        if str(getattr(b, 'kind', '') or '') not in ('say', 'narration'):
            continue
        first = int(getattr(b, 'src_first', 0) or 0)
        if first < 2 or first > len(orig_lines):
            continue
        head_idx, kind = _branch_head_for(orig_lines, first)
        if head_idx is None or kind != 'if':
            continue
        if not _beat_owns_head(orig_lines, head_idx, first):
            continue
        if _has_outer_head(orig_lines, head_idx):
            continue
        own = _own_condition(orig_lines[head_idx])
        if not own:
            continue
        want = str(getattr(b, 'condition', '') or '').strip()
        if want == own or _squash(want) == _squash(own):
            continue                       # 没改（或只差空白）→ 一个字节不动
        if not want:
            rejects.append(
                f'第 {i + 1} 拍清空了条件 —— 那是拆掉这一层 `if`（结构改动），'
                f'增量补丁不做，请用全量重写这一章')
            continue
        if '\n' in want or '\r' in want:
            rejects.append(f'第 {i + 1} 拍的条件里有换行 —— 增量补丁不猜，'
                           f'请用全量重写这一章')
            continue
        rewrites.append((head_idx + 1,
                         indent_prefix(orig_lines[head_idx])
                         + f'if {want}:'))
    return rewrites, rejects


def _plan_branch_deletion(orig_lines: list, first: int, last: int,
                          taken, blocks=()) -> dict | None:
    """
    这一拍是**分支唯一的语句** → 连它所在的分支一起删/换。

    为什么（2026-10-21 真机扫了 `pomodorro.rpy` 45 拍）：删一拍时 33 拍
    走增量，12 拍退回全量 —— 12 拍**全是同一种形状**：

        if breakline == 1:          ← 不是拍，是结构
            "我需要休息一下."          ← 表里删掉的这一拍
        elif breakline == 2:        ← 删了上面两行后，这一行成了孤儿

    `_deletion_empties_block` 判"会留空块"是对的（空块语法错），但它的结论
    是"整章退回全量"—— 代价太大。这里按**分支**处理，只动必须动的几行：

      · `elif` / `else` 分支 → 删 `[分支头 .. 拍末行]`，链自然合法；
      · `if` 分支 + 后面紧跟 `elif` → 同样删，并把那条 `elif` 改写成 `if`
        （只换一个词）；后面是 `else:`（要把整块降一级缩进）→ **不猜**；
      · 菜单选项体（选项头自己是**一拍**，不能删）→ 把体换成 `pass`，
        与全量重生成的产物一致（实测 `pomodorro.rpy` 就是这么写的）。

    返回 `{'delete': (a, b)}` 或 `{'sub': (a, b, [行…])}` 或 `None`（不敢）。
    """
    head_idx, kind = _branch_head_for(orig_lines, first)
    if head_idx is None:
        return None
    head_no = head_idx + 1
    claimed = any(f and f <= head_no <= l for f, l in taken)
    if kind == 'option':
        if not claimed:
            return None                   # 选项头不是这一拍的：形状不认识
        # 选项头留着（它是一拍），体换成 `pass`（缩进借原体第一行）
        pad = indent_prefix(orig_lines[first - 1])
        return {'sub': (first, last, [pad + 'pass'])}
    if claimed:
        return None                       # 块头属于别的拍 → 不动它
    if kind in ('if', 'elif'):
        plan = _chain_renumber(blocks, orig_lines, head_no, last)
        if plan is None:
            return None                   # 链算不出来 → 不猜
        rewrites, renumber = plan
        if kind == 'if':
            nxt = _next_sibling(orig_lines, head_no, last)
            if nxt is not None:
                tag = _norm(orig_lines[nxt - 1]).strip().split(None, 1)[0]
                if tag.rstrip(':') == 'else':
                    return None           # 只剩 `else`：要整块降级缩进，不猜
        return {'delete': (head_no, last), 'rewrite': rewrites,
                'renumber': renumber}
    if kind == 'else':
        # `else:` 一定是链尾，后面没有兄弟分支
        return {'delete': (head_no, last), 'renumber': []}
    return None


def _plan_label_renames(beats, orig_lines) -> list:
    """
    表里改了「Label」→ 源文件里那个 `label 名:` 头也要改（2026-10-24，P0-8）。

    为什么必须单开一条：label 头**不属于任何一拍的行区间**（拍的行从
    `label` 下面第一条语句开始），于是补丁的对位循环根本看不见它 ——
    改了 `Label` 之后报告"改动 0 行"、文件里还是旧名（审计 P0-8 实测）。

    做法：对每个"新名字"分组，取这一组里 `src_first` 最小的一拍，往上找
    最近的一行 `label 旧名…:` —— 那就是它所属的块头。名字没变 → 不动。

    **只在能确定"这一拍确实属于这个块头"时才改**：往上第一个 `label` 行就是
    包住它的那个块头（块是嵌套的，Ren'Py 里 label 不嵌套，所以不会有歧义）。
    同一个块头被改成两个不同名字（用户把两拍的 Label 改得不一致）→ 不猜，
    整组跳过（`_verify_patch` 随后会报"台词在补丁结果里找不到"）。

    返回 `[(0 基行下标, 旧名, 新名)]`，每个块头最多一条。
    """
    groups: dict = {}
    for b in beats:
        name = str(getattr(b, 'label', '') or '').strip()
        first = int(getattr(b, 'src_first', 0) or 0)
        if not name or first < 1 or first > len(orig_lines):
            continue
        groups.setdefault(name, []).append(first)
    wanted: dict = {}                      # 行下标 → {旧名, 新名…}
    for name, firsts in groups.items():
        first = min(firsts)
        for k in range(first - 1, -1, -1):
            m = _LABEL_LINE_RE.match(orig_lines[k])
            if not m:
                continue
            old = m.group(2)
            if old != name:
                wanted.setdefault(k, (old, set()))[1].add(name)
            break
    plans = []
    for k, (old, names) in sorted(wanted.items()):
        if len(names) != 1:
            continue                       # 一个块头对两个新名字 → 不猜
        plans.append((k, old, next(iter(names))))
    return plans


def _plan_reorders(blocks, orig_lines, raw_gen, strict_of) -> tuple:
    """
    内存里的**顺序变了**（把一拍挪了位置）→ 整段按新顺序重写（P0-7）。

    为什么需要单开一条：补丁是**逐拍就地对位**的 —— 每一拍按自己的行号找到
    原文那几行，内容一样就"改动 0 行"。把一拍从上挪到下时，每一拍的**内容**
    都还在原处，于是逐拍看全都"没变"，产物顺序当然也不变（审计 P0-7 实测：
    内存 二/三/一、渲染也是 二/三/一，文件里还是 一/二/三）。

    判据（一条都不满足就不猜，交给调用方退回全量）：

      · 同一个 `label` 里，拍的行号序列**不是单调递增**（有逆序对）；
      · 逆序对涉及的拍的区间合起来是一段**连续区间**（中间只允许空行/注释）；
      · 每一拍的区间都**精确认得出自己**（`strict_of`，防"源文件被手改过"
        时按错位的行号重排）；
      · 这段区间**没有和别的拍交叠**。

    做法：把这段区间当成一排**槽位**（按源码顺序），槽位 k 里填"内存顺序
    第 k 拍"的渲染结果，缩进借该槽位原来第一行。空隙行（空行/注释）
    原地不动 —— 顺序变了，作者的分隔空行不该跟着跑。

    返回 `(plans, reasons)`；`plans` 是 `[(起, 止, 新行, {拍序号})]`。
    """
    by_label: dict = {}
    for blk in blocks:
        _i, first, last, _gen, beat = blk
        if first < 1 or last < first or last > len(orig_lines):
            continue
        by_label.setdefault(str(getattr(beat, 'label', '') or ''), []).append(blk)
    plans: list = []
    reasons: list = []
    for _name, seq in by_label.items():
        firsts = [blk[1] for blk in seq]
        involved = set()
        for j in range(len(firsts)):
            for k in range(j):
                if firsts[k] > firsts[j]:
                    involved.add(j)
                    involved.add(k)
        if not involved:
            continue
        span_first = min(firsts[j] for j in involved)
        span_last = max(seq[j][2] for j in involved)
        members = [blk for blk in seq
                   if span_first <= blk[1] and blk[2] <= span_last]
        member_ids = {blk[0] for blk in members}
        if not {seq[j][0] for j in involved} <= member_ids:
            reasons.append('有拍的区间跨出了重排段，无法安全地换顺序')
            continue
        if any(blk[1] <= span_last and blk[2] >= span_first
               for blk in seq if blk[0] not in member_ids):
            reasons.append('重排段与别的拍交叠，无法安全地换顺序')
            continue
        if any(not strict_of.get(blk[0]) for blk in members):
            reasons.append('重排段里有拍的原文对不上（源文件可能被手改过），'
                           '不敢按行号换顺序')
            continue
        slots = sorted(members, key=lambda t: t[1])
        ok = True
        crossed = False
        blocked: list = []                     # 空隙里那些"认不出"的行
        for a, b in zip(slots, slots[1:]):
            if b[1] <= a[2]:
                ok = False                 # 区间交叠 → 不猜
                break
            for n in range(a[2] + 1, b[1]):    # 中间的空隙只许是空行/注释
                body = _norm(orig_lines[n - 1]).strip()
                if _LABEL_LINE_RE.match(orig_lines[n - 1]):
                    crossed = True             # 跨过了 `label` 头
                if body and not body.startswith('#'):
                    ok = False
                    blocked.append(orig_lines[n - 1])
                    break
        if crossed:
            reasons.append('把一拍移到了另一个 label 里（跨 label 的重排）——'
                           '增量补丁不猜这种结构改动，请用全量重写这一章')
            continue
        if not ok:
            # **说清楚卡在哪一行、为什么**（2026-10-25，审计第四轮 P1）。
            #
            # 现场（真机 `script_pomo.rpy`）：把 `elif breakline == 2:` 那一支的
            # 台词挪到 `elif` 链外面 —— 空隙里那一行是**块头**
            # （`if breakline == 1:` / `elif …:`），它不属于任何一拍。
            # 旧提示只说"有认不出的行"，用户看不出这是"挪出分支 = 改剧情"：
            # 台词一旦离开 `elif`，条件就不再生效（或者换了判定顺序）。
            heads = [ln for ln in blocked if _ends_with_colon(ln)]
            if heads:
                reasons.append(
                    f'这一拍要挪过结构块头（`{_norm(heads[0]).strip()}`）：'
                    f'挪出 `if` / `elif` 分支之后，这一拍的条件就不再生效'
                    f'（或者换了生效顺序）—— 那是改剧情，不是排版。'
                    f'增量补丁不猜；要挪请先在卡片上清掉这一拍的条件，'
                    f'或者用全量重写这一章')
            else:
                shown = (_norm(blocked[0]).strip() if blocked else '')
                reasons.append(f'重排段里有认不出的行（例：`{shown[:40]}`）——'
                               f'它不属于任何一拍，挪动会把它留在原地或带走，'
                               f'增量补丁不猜；请用全量重写这一章')
            continue
        new_lines: list = []
        pos = span_first
        for slot, blk in zip(slots, members):
            for n in range(pos, slot[1]):          # 槽位前的空隙：原地不动
                new_lines.append(orig_lines[n - 1])
            pad = indent_prefix(orig_lines[slot[1] - 1])
            rows = raw_gen.get(blk[0]) or []
            for ln in rows:
                new_lines.append(pad + ln if str(ln).strip() else ln)
            pos = slot[2] + 1
        for n in range(pos, span_last + 1):
            new_lines.append(orig_lines[n - 1])
        if [ln.strip() for ln in new_lines] == \
                [ln.strip() for ln in orig_lines[span_first - 1:span_last]]:
            continue                       # 渲染结果与原文同序 → 不需要动
        plans.append((span_first, span_last, new_lines, member_ids))
    return plans, reasons


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

    **"这一拍是分支唯一语句"**（删完上一级块会空）交给
    `_plan_branch_deletion`：它把"整条分支"一起删/换（见那里的 docstring）。
    那也处理不了时才算失败。

    返回 `(delete_ranges, subs, rewrites, renumber, delete_failed,
    delete_empty)`；
    `subs` 是 `(first, last, [整行…])`（菜单选项体 → `pass`），`rewrites` 是
    `(行号, 新整行)`（`elif` → `if`，只在"删掉链头"时用），
    `renumber` 是 `(label, 旧条件, 新条件)`（删掉一支之后，后面几支条件
    要重算 —— 见 `_chain_renumber`）；`delete_empty` 是 `delete_failed` 里
    "删完上一层块会一行不剩"的那几条（回执要能说清是哪一种）。
    """
    delete_ranges: list = []
    subs: list = []
    rewrites: list = []
    renumber: list = []
    delete_failed = 0
    delete_empty = 0                          # 其中"删完上一层块会空"的条数
    if not dropped_src:
        return (delete_ranges, subs, rewrites, renumber, delete_failed,
                delete_empty)
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
        if _deletion_empties_block(orig_lines, first, last):
            # 删完上一级块会空（`if x:` 下面一行都没有）→ 引擎读不了。
            # 整条分支一起删/换（见 `_plan_branch_deletion`）。
            plan = _plan_branch_deletion(orig_lines, first, last, taken,
                                         blocks)
            if plan is None:
                delete_failed += 1
                delete_empty += 1
                continue
            if 'sub' in plan:
                a, z, rows = plan['sub']
                if any(a <= tf <= z or a <= tl <= z
                       for tf, tl in taken if tf):
                    delete_failed += 1        # 与仍在的拍交叠 → 不敢换
                    continue
                subs.append((a, z, rows))
                continue
            a, z = plan['delete']
            rewrites.extend(plan.get('rewrite') or [])
            renumber.extend(plan.get('renumber') or [])
            first, last = a, z
        if any(first <= t_last and last >= t_first
               for t_first, t_last in taken if t_first and t_last >= t_first):
            delete_failed += 1                # 与仍在的拍交叠 → 不敢删
            continue
        delete_ranges.append((first, last))
    return (delete_ranges, subs, rewrites, renumber, delete_failed,
            delete_empty)


def _apply_sequence_edits(out_lines: list, delete_ranges, anchors,
                          indent: str, subs=(), rewrites=(),
                          removed_out: list = None,
                          into_body=()) -> tuple:
    """
    插入 + 删除 + 整段替换**一起按位置从后往前**做（先动后面的行，前面的
    行号才不受影响）。返回 `(inserted_lines, removed_lines, replaced_lines)`。

    `removed_out`（2026-10-21）：把**被删掉的行原文**收集进去（去缩进，
    最多 5 条）—— 回执要能点名"删了哪几行"，不能只说"改动 N 行"。

    `subs` / `rewrites` 是 2026-10-21 为"删一条分支"加的：
      · `subs`：`(first, last, [整行…])` —— 菜单选项体换成 `pass`；
      · `rewrites`：`(行号, 新整行)` —— `elif` 提升成 `if`。
    两者与删除同一批处理：**按原行号、从后往前**，前面的行号不会漂。

    两处容易写错、都留着注释：
      · **锚点是块头**（`menu:` / `"选项":` / `if …:`）时不能紧贴着插，
        否则新台词会掉进那个块里（见 `_insert_anchor`）；
      · **缩进必须自己补** —— `render_beat_to_rpy` 返回的是"相对行"
        （不带基础缩进）。替换路径之所以看着没问题，是因为
        `_replace_matched` 保留原文那一行的缩进；插入没有"原文行"可依，
        只能借**锚点行**的缩进（新卡片在结构上就是锚点的兄弟节点）。

    `into_body`（2026-10-23）：这些锚点上的新拍**本来就该在块体里**
    （见 `_insert_points_for`）—— 紧贴块头插、缩进比块头深一级。
    这与上一条"退到块外"是两种拍，不能共用一条规矩。
    """
    inserted_lines = 0
    removed_lines = 0
    replaced_lines = 0
    # 改写只换一行的内容（不动行数）→ 先做，后面按原行号删/换都还对得上。
    for no, text in (rewrites or ()):
        if 1 <= no <= len(out_lines) and out_lines[no - 1] != text:
            out_lines[no - 1] = text
            replaced_lines += 1
    #: `(位置, 类型, 起, 止, 整行)` —— 0=删、1=插、2=整段换
    ops: list = []
    for first, last in delete_ranges:
        ops.append((last, 0, first, last, None))
    for first, last, rows in (subs or ()):
        ops.append((last, 2, first, last, rows))
    for point, rows in anchors.items():
        # 同一锚点上的多张新卡片保持拍序（`rows` 是"每张卡片的行"的列表）
        flat = [ln for group in rows for ln in group]
        ops.append((point, 1, point, point, flat))
    into_body = {int(x) for x in (into_body or ()) if x}
    for _pos, kind, a, b, rows in sorted(ops, reverse=True,
                                        key=lambda t: (t[0], t[1])):
        if kind == 0:
            for gone in out_lines[a - 1:b]:
                _note_removed(removed_out, gone)
            del out_lines[a - 1:b]
            removed_lines += b - a + 1
        elif kind == 2:
            # 整段换成给定的整行（缩进已经带在行里）——
            # 菜单选项体 → `pass`（见 `_plan_branch_deletion`）。
            n_old = b - a + 1
            old_rows = list(out_lines[a - 1:b])
            out_lines[a - 1:b] = list(rows)
            if len(rows) >= n_old:
                inserted_lines += len(rows) - n_old
            else:
                removed_lines += n_old - len(rows)
            # 行数没变的那部分（1 行→1 行）也要记进"改动行数"，
            # 否则回执会说"改动 0 行"，而文件里明明换了内容。
            replaced_lines += sum(1 for x, y in zip(old_rows, rows) if x != y)
        else:
            at = max(0, min(a, len(out_lines)))
            if a in into_body and at >= 1 and _ends_with_colon(out_lines[at - 1]):
                # 这一拍在模型里**就在块体里**（`nest_path` 非空）→ 紧贴着
                # 块头插、缩进比块头深一级；不许退到块外（见 `_insert_points_for`）。
                pad = _body_pad(out_lines, at, indent)
            else:
                # **锚点是块头**时不能紧贴着插（见 `_insert_anchor` 的 docstring）
                at, pad_hint = _insert_anchor(out_lines, at)
                if pad_hint is not None:
                    pad = pad_hint
                else:
                    pad = indent_prefix(out_lines[at - 1]) if at >= 1 else ''
                    if not pad:
                        # 锚点是 `label` / 注释这类顶层行 → 用**下一行**的缩进，
                        # 否则顶格写出来的台词会把文件结构读坏。
                        nxt_line = out_lines[at] if 0 <= at < len(out_lines) else ''
                        pad = indent_prefix(nxt_line) or indent
            out_lines[at:at] = [pad + ln for ln in rows]
            inserted_lines += len(rows)
    return inserted_lines, removed_lines, replaced_lines


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

    返回 `(insert_ids, dropped, dropped_src, fresh)`。

    `fresh=True` 表示**行号是这一刀刚解析出来的**（不是拍上带来的旧值）：
    那时"这一段像不像这一拍"的守卫是多余的 —— 行号本来就来自当前磁盘上的
    这一份文件，不存在"过期"。2026-10-21 实测（`pomodorro.rpy`，表格路径）：
    说话人与正文同改的拍，守卫一条内容证据都对不上 → 判 stale →
    新台词落不了地 → 整章退回全量。而它恰恰是最该增量的一刀。
    """
    dropped_src: list = []
    if not any(getattr(b, 'src_first', 0) for b in beats):
        aligned_inserts: list = []
        _m, _t, dropped = recover_source_ranges(beats, source_path,
                                                dropped_out=dropped_src,
                                                inserted_out=aligned_inserts)
        return set(aligned_inserts), dropped, dropped_src, True
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
    return set(_insert_ids_of(beats)), dropped, dropped_src, False


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


def _menu_condition_view(beats: list) -> dict:
    """
    拍序号 → 这条拍在**菜单结构**里的条件"应该长什么样"。

    为什么需要它（2026-10-23，条件菜单选项实测）：补丁是**逐行配对**的，
    而单拍渲染（`render_beat_to_rpy`）不知道菜单结构，于是同一件事长成两种形状：

        # 整份渲染（`_render_menu_segments`，即用户文件里的样子）
            "台词D" if persistent.flag:
                e "台词E。"

        # 单拍渲染（补丁用来配对的行）
            if persistent.flag:
                "台词D":
                if persistent.flag:       ← 选项体的拍又被包了一层
                    e "台词E。"

    这两种形状永远配不上 → 那几行被判成"新增内容没能落进产物" →
    **整章退回全量重生成**（真机 55 个文件里恰好没有条件选项，所以全绿也盖不住）。

    这里的口径**逐条镜像** `_render_menu_segments`：
      · 选项行内联的只是"选项自身"的条件（与菜单头重复的不写）；
      · 选项体拍带的条件是**累积**的（选项 + 菜单头）：正好等于期望值 → 剥掉不包；
        多出来的是真的嵌了一层 `if` → 减掉结构上那部分再包。
    """
    from storygraph.core.nest_path import option_index
    from storygraph.story.beat_render import (
        _expected_body_condition, _with_condition, strip_condition_context,
    )

    menus: dict = {}
    options: dict = {}
    for b in beats:
        path = str(getattr(b, 'nest_path', '') or '')
        kind = str(getattr(b, 'kind', '') or '')
        if kind == 'menu':
            menus[path] = str(getattr(b, 'condition', '') or '').strip()
        elif kind == 'menu_option':
            parent = path.rsplit('>', 1)[0] if '>' in path else path
            options.setdefault(parent, []).append(b)

    view: dict = {}
    for i, b in enumerate(beats):
        path = str(getattr(b, 'nest_path', '') or '')
        kind = str(getattr(b, 'kind', '') or '')
        cond = str(getattr(b, 'condition', '') or '').strip()
        if kind == 'menu_option':
            parent = path.rsplit('>', 1)[0] if '>' in path else path
            menu_cond = menus.get(parent, '')
            own = (strip_condition_context(cond, menu_cond)
                   if (cond and menu_cond) else cond)
            if own == menu_cond:
                own = ''
            view[i] = {'beat': b, 'inline': own, 'drop_wrap': True}
            continue
        if '>' not in path:
            continue                       # 不在菜单里 → 单拍渲染就是对的
        parent = max((p for p in options if path.startswith(p + '>')),
                     key=len, default='')
        if not parent:
            continue
        key = path[len(parent) + 1:].split('>')[0]
        idx = option_index(key)
        row = options[parent]
        opt_cond = (str(getattr(row[idx - 1], 'condition', '') or '').strip()
                    if 0 < idx <= len(row) else '')
        expected = _expected_body_condition(opt_cond, menus.get(parent, ''))
        if cond == expected:
            view[i] = {'beat': b, 'drop_wrap': True}
        elif cond:
            lean = strip_condition_context(cond, expected)
            if lean != cond:
                view[i] = {'beat': _with_condition(b, lean), 'drop_wrap': False}
    return view


def _menu_shaped_lines(lines: list, info: dict) -> list:
    """把单拍渲染的行调整成**源文件里的形状**（见 `_menu_condition_view`）。"""
    inline = str(info.get('inline') or '')
    if inline and len(lines) >= 2 and str(lines[0]).startswith('if '):
        head = str(lines[1]).strip()
        if head.endswith(':'):
            # `if C:` + `    "文本":` → `"文本" if C:`
            return [head[:-1] + f' if {inline}:'] + list(lines[2:])
    if info.get('drop_wrap') and lines and str(lines[0]).strip().startswith('if '):
        return list(lines[1:])             # 条件已经写在选项行上，不再包一层
    return lines


def _render_blocks(beats: list, audit, orig_lines: list = None):
    """
    每一拍 → `(序号, 起始行, 结束行, 归一化后的生成行, Beat)`。

    另外返回 `{序号: 带缩进的生成行}`（`raw_gen`）—— 插入新拍时要用它，
    因为插入没有"原文行"可依，只能整段写进去。

    `orig_lines`：源文件的行（用来把"标题写在头行"的菜单还原回头行形式，
    见 `_menu_head_caption_merge`）。不给就是纯渲染（老调用方的行为）。

    渲染失败（拍本身有问题）就记一笔 `swallow` 并当作"这一拍没有生成行" ——
    不抛出去，因为增量补丁失败时调用方会退回全量重生成，
    而"其中一拍渲染不了"不该让整次导出变成异常。
    """
    from storygraph.story.beat_render import render_beat_to_rpy

    view = _menu_condition_view(beats)
    blocks = []
    raw_gen: dict = {}
    for i, b in enumerate(beats):
        first = int(getattr(b, 'src_first', 0) or 0)
        last = int(getattr(b, 'src_last', 0) or 0)
        info = view.get(i, {})
        target = info.get('beat', b)
        try:
            lines = [ln for ln in render_beat_to_rpy(target, audit=audit)
                     if str(ln).strip()]
            lines = _menu_shaped_lines(lines, info)
            lines = _menu_head_caption_merge(lines, b, first, last, orig_lines)
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

    __slots__ = ('blocks', 'changed', 'grown_total', 'indent', 'insert_ids', 'lost_new', 'matched', 'orig_lines', 'orig_loose', 'orig_set', 'orig_squash', 'out_lines', 'removed_preview', 'reorder_ids', 'seen_ranges', 'shrunk_total', 'stale',
                  'strict_of', 'trust_ranges', 'unlanded_total', 'usable')

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
        if _i in st.reorder_ids:
            continue                          # 顺序变了的拍 → 整段重写（见下）
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
                elif _lost_content(unlanded, st.orig_squash, st.orig_loose):
                    # 一对一替换也没接住这句话 → 报出来，别静默保持原文
                    st.lost_new += 1
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
                    relax_speaker=True,
                    child_pad=_child_pad_at(st.orig_lines, last, st.indent))
                st.unlanded_total += len(unlanded)
                if new_seg != old:
                    st.changed += n_changed
                    st.out_lines[first - 1:last] = new_seg
                elif _unpairable_new_content(gen, old, st.orig_squash,
                                             st.orig_loose) \
                        or _lost_content(unlanded, st.orig_squash,
                                         st.orig_loose):
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
            relax_speaker=st.trust_ranges, removed_out=st.removed_preview,
            child_pad=_child_pad_at(st.orig_lines, last, st.indent))
        st.unlanded_total += len(unlanded)
        st.grown_total += grown
        st.shrunk_total += shrunk
        if new_seg == old:
            if _lost_content(unlanded, st.orig_squash, st.orig_loose) or (
                    st.trust_ranges
                    and _unpairable_new_content(gen, old, st.orig_squash,
                                                st.orig_loose)):
                # 什么都没落地 → 必须报（绝不写"报成功、内容没写"）
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
    表格那条路拍上没有行号，由 `recover_source_ranges` **当场**从这份源文件
    算出来 —— 那种行号同样新鲜，函数内部会把 `trust_ranges` 提为真
    （`_recover_ranges` 返回的 `fresh`）。而"拍上带着旧行号"的调用方
    （编辑器载入后文件被外部改过）仍然必须靠守卫：那正是当年那次事故
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
        # ⚠ 读源必须走 `source_style`（2026-10-18，审计 P0-1）：
        #   `Path.read_text(encoding='utf-8')` 会把开头的 BOM 留成正文第一个
        #   字符。合并产物在**首行之前**插了生成标记，于是 BOM 落到第二行行首：
        #
        #       # 由剧本表自动生成 …
        #       \ufeff# This file demonstrates…
        #
        #   那一行不再是注释，引擎直接 `expected statement.`（官方 tutorial
        #   23 个文件全带 BOM，全中）。BOM 由写盘那一层按风格还原。
        from storygraph.story.source_style import read_text as _read_source
        original, _style = _read_source(source_path)
    except (OSError, UnicodeDecodeError) as exc:
        return PatchResult(generated_text, None, 0, len(beats), 0,
                           reasons=[f'源文件读不了：{exc}'])

    # 行号是运行期元数据（不入表）；从表里读出来的拍没有 → 现找回来。
    # 细节见 `_insert_ids_of` / `_recover_ranges` 的 docstring。
    insert_ids, dropped, dropped_src, fresh = _recover_ranges(beats,
                                                             source_path)

    # —— **表过期守卫**（2026-10-21，P0）——
    #
    # `SequenceMatcher` 只按"身份键"对齐（正文刻意不进键 —— 正文正是要改的东西），
    # 于是**表过期**时（导表之后程序员又在源里加了一句台词），源比表多一拍：
    # 对齐会把"表里改过的那一拍"配到**程序员新加的那一行**上，再把
    # "本来属于它的那一拍"判成"用户删掉的拍"。
    #
    # 实测（`label ch1:` + 4 句 → 表里 3 拍，源里多 `pause` / 新台词 / `$`）：
    # `recover` 给第 3 拍行号 [4..5]（`pause` + 新台词），源第 4 拍（"第三句。"）
    # 进 dropped；补丁报 `ok=True`、`changed_lines=3`，而产物里
    # **两句源台词只剩一句** —— 没有任何一处报错。
    #
    # 判据：源里有**台词**被判"用户删掉了"，**同时**表里有拍的正文在源里
    # 根本不存在。两件事凑在一起时"改了那句"和"漏了一拍"无法区分 ——
    # 按契约（L2 不猜名字 / M4 认不出就报）**不猜**，整份退回全量，
    # 由调用方与门禁去处理（原地覆盖会被"增量没成功"那道闸拦下）。
    # 判据只认**台词拍**（`kind in (say, narration)`）：菜单拍的 `Beat.text`
    # 是占位符 `\x00menu`（`MENU_NO_CAPTION`），原文里写的是 `menu:` ——
    # 拿它去原文里找必然找不到，会把"删掉一个菜单选项体"这种正常操作
    # 误判成表过期（`test_a_menu_option_body_becomes_pass` 抓到的正是这个）。
    def _is_say_beat(b) -> bool:
        return (str(getattr(b, 'kind', '') or '') in ('say', 'narration')
                and str(getattr(b, 'text', '') or '').strip() != '')

    _dropped_say = [b for b in (dropped_src or []) if _is_say_beat(b)]
    # 比对走 `_unescaped`（语义形式）而不是逐字节：真机 `chapter1_2.rpy` 第 132 行
    # 是旁白 `'file "c:\\admin_script.py", …'`，解析出来的 `text` 是单个反斜杠 ——
    # 拿它去原文里找永远找不到，于是"删掉一句别的台词"会被误判成表过期
    # （实测：删一拍从"增量删 8 行"退回"全量 25 段"，L2b 从 19/20 掉到 17/20）。
    _src_blob = _unescaped(original)
    _unmatched = [b for b in beats if _is_say_beat(b)
                  and _unescaped(str(getattr(b, 'text'))) not in _src_blob]
    if _dropped_say and _unmatched:
        _d = str(getattr(_dropped_say[0], 'text', ''))[:20]
        _u = str(getattr(_unmatched[0], 'text', ''))[:20]
        return PatchResult(
            generated_text, None, 0, len(beats), 0,
            reasons=[f'表与源码不同步：源里有 {len(_dropped_say)} 拍台词不在表里'
                     f'（例如「{_d}」），表里有 {len(_unmatched)} 句在源里找不到'
                     f'（例如「{_u}」）—— 这有两种可能：'
                     f'① 导表之后源文件被改过（程序员加了内容）；'
                     f'② 你在表里删了一拍、同时又改了另一拍的说话人/舞台。'
                     f'两种目前分不开，按"不猜"拒绝增量'
                     f'（先重新载入这一章；确认无误再加 --force 全量）'])

    # **现算出来的行号 = 新鲜**：它们来自刚解析的这份文件，绕过了"行号会不会
    # 过期"这个问题（见 `_recover_ranges` 的 `fresh` 说明）。表格那条路因此
    # 也能享受"同一拍改了说话人和正文"这类改动的增量 —— 从前它被对位守卫
    # 判 stale，整章退回全量（2026-10-21 实测）。
    trust_ranges = bool(trust_ranges or fresh)
    dropped, dropped_src = _compute_insert_anchors(
        beats, insert_ids, dropped, dropped_src)

    from storygraph.story.dsl import Audit
    audit = Audit(symbols) if symbols is not None else None

    orig_lines = original.splitlines()
    out_lines = list(orig_lines)
    blocks, raw_gen = _render_blocks(beats, audit, orig_lines)

    # 一个"渲染得出内容"的拍都没有 —— 与上面 `not beats` 是同一件事。
    #
    # 为什么还要单独判（2026-10-05，拓展边界③）：顶层 `init python:` 现在会
    # 造一张**空拍**来携带"文件级保真块"（它不属于任何演出流，见
    # `beat_build.attach_file_blocks`）。那张拍让 `beats` 非空，却永远对不上
    # 任何源行 → 覆盖率 0%、`reasons` 却是空的 —— 调用方只拿到一个
    # "没走增量"而**不知道为什么**（`test_empty_chapter_reason_is_distinct`
    # 抓到的正是这个：那条"没有解析出任何拍"的原因必须继续说出来）。
    if not any(gen for _i, _first, _last, gen, _b in blocks):
        # **没有可渲染内容的文件**（纯定义 / 屏幕 / `init python` 文件）：
        # 表里没有一处可编辑的拍 ⇒ 产物只能是原文本身。
        #
        # 从前这里报"无法增量" → 调用方退回**全量重生成** → 顶层块被重排
        # （每两条语句之间插一个空行、行尾空格被删、连续空行被压成一个）：
        # 真机 `definitions.rpy` 原文 1120 行 → 产物 1288 行，
        # `sr_general` 55 个文件里 31 个有 diff —— 而用户**一处都没改**。
        # 现在直接**原样透传**：没得改的东西，一个字节都不动。
        return PatchResult(original, source_path, len(beats), len(beats), 0,
                           passthrough=True)

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
    # 它们同时是回执里那句"N 张空卡不产生行"的来源（审计第四轮 P2）。
    _empty_inserts = {i for i in insert_ids if not raw_gen.get(i)}
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
    #: 这一刀从源文件里删掉的行（原文，最多 5 条）—— 回执要能点名（2026-10-21）。
    #: 逐拍的"拍内删行"（清空某一列的立绘/音乐）与"整拍删除"都往这里写。
    removed_preview: list = []
    # —— **顺序变了**（P0-7）：整段按新顺序重写 ——
    # 放在逐拍对位之前算：这样那几拍不再走"就地对位"（它们的内容本来都还在
    # 原处，逐拍看全都"没变"——那正是顺序落不了盘的原因）。
    reorder_plans, reorder_reasons = _plan_reorders(
        blocks, orig_lines, raw_gen, strict_of)
    reorder_ids: set = set()
    for _pf, _pl, _pn, _pids in reorder_plans:
        reorder_ids |= _pids
    # —— **label 改名**（P0-8）：块头那一行不属任何一拍，得单独改 ——
    label_renames = _plan_label_renames(beats, orig_lines)
    for _k, _old_name, _new_name in label_renames:
        _m = _LABEL_LINE_RE.match(out_lines[_k])
        if _m is None:
            continue
        out_lines[_k] = f'{_m.group(1)}label {_new_name}{_m.group(3)}'
        changed += 1
    # —— **改条件**（P1，2026-10-25）：`if …:` 那一行也不属任何一拍 ——
    #
    # 与上面 label 改名同一类：块头单独改。`elif` / `else` / 嵌套的 `if`
    # 不碰（判据见 `_plan_condition_rewrites`），那几种由 `_verify_conditions`
    # 拒绝并给出具体理由。
    cond_rewrites, cond_rejects = _plan_condition_rewrites(
        beats, orig_lines, skip_ids=set(insert_ids) | set(reorder_ids))
    for _k, _line in cond_rewrites:
        if 1 <= _k <= len(out_lines) and out_lines[_k - 1] != _line:
            out_lines[_k - 1] = _line
            changed += 1
    st = _PatchWalkState(
        blocks=blocks,
        changed=changed,
        grown_total=grown_total,
        indent=indent,
        insert_ids=insert_ids,
        lost_new=lost_new,
        matched=matched,
        orig_lines=orig_lines,
        orig_loose=orig_loose,
        orig_set=orig_set,
        orig_squash=orig_squash,
        out_lines=out_lines,
        removed_preview=removed_preview,
        reorder_ids=reorder_ids,
        seen_ranges=seen_ranges,
        shrunk_total=shrunk_total,
        stale=stale,
        strict_of=strict_of,
        trust_ranges=trust_ranges,
        unlanded_total=unlanded_total,
        usable=usable,
    )
    _patch_walk_blocks(st)
    # 重排段从后往前换（先动后面，前面的行号才不漂）
    reorder_changed = 0
    for _pf, _pl, _pn, _pids in sorted(reorder_plans, key=lambda p: p[0],
                                       reverse=True):
        _old_seg = st.out_lines[_pf - 1:_pl]
        reorder_changed += sum(1 for a, b in zip(_old_seg, _pn) if a != b)
        reorder_changed += abs(len(_old_seg) - len(_pn))
        st.out_lines[_pf - 1:_pl] = _pn
        st.matched += len(_pids)
    st.changed += reorder_changed

    # —— 被删除的拍：**精确删掉它们的那几行** ——
    #
    # 用户 2026-10-05："为何删行无法进行增量操作？" → 这一版实现了。
    # 与"插入"同一套纪律：只动该动的那几行，凡是拿不准的一律放弃
    # （退回全量），并由下面末尾的全局校验兜底。
    # 具体"哪些情形不敢删"见 `_plan_deletions` 的 docstring。
    delete_ranges, delete_subs, delete_rewrites, delete_renumber, \
        delete_failed, delete_empty = \
        _plan_deletions(dropped_src, st.blocks, st.orig_lines)
    # 新增的拍 → 插入点（锚点见 `_insert_points_for` 的 docstring）
    anchors, insert_failed, insert_into_body = _insert_points_for(
        beats, st.insert_ids, raw_gen)
    # 插入 + 删除一起按位置**从后往前**做（先动后面的行，前面行号才不漂）
    inserted_lines, removed_lines, replaced_lines = _apply_sequence_edits(
        st.out_lines, delete_ranges, anchors, indent,
        subs=delete_subs, rewrites=delete_rewrites,
        removed_out=removed_preview, into_body=insert_into_body)
    st.changed += (inserted_lines + removed_lines + replaced_lines
                   + st.grown_total + st.shrunk_total)

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
    reasons = list(reorder_reasons)
    if reorder_plans and (st.insert_ids or dropped_src):
        # 重排段会改变那几行的**行数**，后面的删除 / 插入锚点全部要跟着漂。
        # 两者同时发生时不做编排（不猜），让调用方退回全量并说清楚。
        reasons.append('这一章里既移动了拍、又增删了拍 —— 增量补丁不猜这种组合，'
                       '请重新载入后再导出（或直接全量重写这一章）')
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
        _msg = f'{delete_failed} 处删除不安全（含结构行或与其它拍交叠）'
        if delete_empty:
            # 审计 P1：删 `label` 下唯一那一拍（常见是一条 `jump`）时，
            # 上一层块会变成空的 —— 用户看着内存里删掉了，文件却不动，
            # 而旧提示只会说"不安全"，看不出是这一类。这里点名。
            _msg += (f'；其中 {delete_empty} 处是"删完上一层块会一行不剩"'
                     f'（例如 label 下唯一那一拍是 jump）——'
                     f'内存里已经删了，文件没动；请手动删那一行，'
                     f'或者用全量重写这一章')
        reasons.append(_msg)
    if text != original:
        reasons.extend(_verify_patch(text, original, beats, st.blocks,
                                    symbols=symbols,
                                    renumber=delete_renumber))
    else:
        # **一个字都没改，也要查条件**（2026-10-05，O1 实测）。
        #
        # "改了条件、产物一个字节没动"恰好落在两条缝里：`changed_lines=0`
        # → 走不进整体校验；而条件又不是"生成的一行" → 第三条判据也看不见它。
        # 结果就是 `ok=True`、条件没改（静默 no-op）。
        reasons.extend(_verify_conditions(text, beats,
                                         renumber=delete_renumber))
    if cond_rejects:
        # 条件那一格改了、但这一支改不了（清空条件 / 换行）—— 这条理由比
        # "N 处条件对不上"具体得多，别让两条同时出现把人绕晕。
        reasons = [r for r in reasons if '处条件与要写的拍对不上' not in r]
        reasons.extend(cond_rejects)
    if reorder_plans and any('处条件与要写的拍对不上' in r for r in reasons):
        # **移动带条件的拍**：条件在文件里是"位置决定"的（`if` / `elif` 的
        # 先后就是语义），换了位置就换了哪一条先判 —— 剧情真的会变。
        # 这条理由点出来，别让人以为是补丁坏了（审计第四轮 P1）。
        reasons.append('这一章里移动了拍，而其中几拍带条件（`if` / `elif` 分支）：'
                       '条件在文件里由**位置**决定，换位置就等于换了判定顺序 —— '
                       '那是改剧情。请先在卡片上清掉这几拍的条件再移动，'
                       '或用全量重写这一章')

    # **覆盖率不足也必须说话**（2026-10-23）：从前 `ok` 会因为 coverage < 0.9
    # 变成 False，而 `reasons` 仍是空列表 —— 调用方只拿到"失败，没原因"。
    # 这里补一条人能看懂的理由；`PatchResult.ok` 仍然按同一条门槛判。
    base = max(0, pending - len(st.insert_ids))
    if base > 0:
        covered = st.matched / base
        if covered < 0.9 and not any('对位率' in r for r in reasons):
            reasons.append(
                f'对位率不足（{st.matched}/{base} = {covered:.0%}，'
                f'低于 90%）—— 源文件与表对不上，不能安全地增量写入')

    return PatchResult(text, source_path, st.matched, pending, st.changed,
                       stale_beats=st.stale,
                       unlanded_lines=st.unlanded_total,
                       dropped_beats=dropped,
                       inserted_lines=inserted_lines,
                       inserted_beats=len(st.insert_ids),
                       removed_lines=removed_lines,
                       # ⚠ **两个来源都要算**（2026-10-25 第六轮）：
                       # `removed_lines` = 整拍/整段被删的行（`_apply_sequence_edits`）；
                       # `st.shrunk_total` = **拍内跟着删掉**的行（清掉一格的
                       # show / 表里没有的 `$ x = 1`）。
                       # 从前这里写的是模块级的 `shrunk_total` —— 那个变量在
                       # `patch_export` 里只初始化、从不累加（真正的累加在
                       # `st.shrunk_total`），于是第二类永远算 0：
                       # 预览里明明有 2 行、`removed_count` 却是 0
                       # （回执靠 `removal_note` 的"预览兜底"才凑巧说对，
                       # 而任何只看 count 的判据——比如"表比源码旧"—都静默失效）。
                       removed_count=(removed_lines + st.shrunk_total),
                       removed_preview=tuple(removed_preview),
                       grown_lines=st.grown_total,
                       shrunk_lines=st.shrunk_total,
                       empty_beats=len(_empty_inserts),
                       reasons=reasons)


__all__ = ['PatchResult', 'patch_export', 'recover_source_ranges']
