#!/usr/bin/env python
"""
地狱测试 · 第二轮：**结构位（`Nest`）× 行尾注释（`Inline`）的组合面**
================================================================================

第一轮（`tools/hell_test.py`）打的是"顶层块 × 结构 × 对抗文本 × 导出路径"，
但它**不认识 `Nest` / `Inline`** —— 那两个隐藏列是这一轮才落地的。
这一轮专打它们，而且刻意往**组合处**打：

    深度 1→5 的嵌套 × if/menu 交替 × 多选项 × 行尾注释 × 对抗文案
      × {直接渲染 / 全量 Excel 往返 / 两轮往返 / 原地增量合并} × 真实项目 41 个文件

判据（**每一条都是"要么全对、要么报出来"**，不测"崩没崩"）：

  N1 **结构守恒** —— 产物再解析回来，`Nest` 路径的**多重集**必须与原文一致。
     这是最硬的一条：它同时钉住"块边界有没有被猜错"和"隐藏列有没有掉"。
     ⚠ **口径校准（2026-10-05，见 §有意为之 5/6）**：只比**菜单口径**——
     `if:<n>` 那几段按"文档序、不参与渲染"排除，空块补的 `pass` 拍两边
     都不计。校准前它把 5 处**故意为之**的展平报成了"结构丢了"，
     真信号反而被淹。if 的保真由 N2/N3 逐字节盯着（`if` 从 `Condition`
     列来，不经过 `Nest`）。
  N2 **编码不动点** —— `render → parse → render` 必须**逐字节**相等。
     注释不许翻倍、缩进不许漂移、`elif` 不许越展越乱。
  N3 **两路一致** —— 直接渲染的产物 `R1` 与"全量 Excel 往返"后的产物 `R2`
     **逐字节**相等。表这条路上任何一处丢字段，它都会红。
  N4 **对拍器全绿** —— `nest_placement_conflicts()` 必须为空：结构列说的位置
     与老推断（`_menu_member` / 条件 / 行号）摆的位置，**两套独立机制必须一致**。
     这条比 N1 更狠：它抓的是"两边都自洽、但互相矛盾"。
  N5 **注释不翻倍、不错位** —— 每条注释在产物里**至多一次**，且 `R2` 与 `R1`
     的注释**逐行同址**（不是"都还在"就算过）。
  N6 **产物自洽** —— 产物能被自己的解析器吃回去且 0 错、无可跨物理行的字符串。

为什么 N3 用"逐字节相等"而不是"内容都在"：注释是**一行上的一个位置**，
`_note_lines` 那种集合比法会放过"注释贴到了隔壁那一行"。逐字节相等
代价是必须先把"有意为之的行为"摸清（见 §有意为之），但换来的是一条
**不需要我解释规则**的判据 —— 我解释得越多，假阳性越多（第一轮的教训）。

--------------------------------------------------------------------------------
§有意为之：这些**不算**缺陷（写在这里，免得下次又当成 bug 去修）
--------------------------------------------------------------------------------
1. `elif/else` 展平成 `if not((前一条件)):` —— A2 契约里的扁平展开；
2. **一拍渲染出多行时不贴自己的行尾注释** —— 贴到第一行会变成"整个块的注释"；
   于是"`if` 里的台词"那条注释本来就会掉（它渲染成 `if …:` + 台词两行）。
   这一条**不改**：靠 `cond_comment` 那条通道管 `if:` 行自己的注释。
3. 选项体里的拍**不再重复写 `if`**（选项条件已经写在选项那一行）——
   所以选项体里"无内层条件"的拍仍是一行，它自己的注释照样贴。
4. 空块补 `pass`（Ren'Py 语法要求）。
5. **`if:<n>` 的文档序不进 N1**（2026-10-05 校准）。那个 `n` 是"这一份文本里
   第几个 if 块"的序号，而 §有意为之 1 允许 `elif/else` 展平成多个 `if` ——
   展平之后**块数必然变**，编号跟着变：
   · 真实项目 `mini_game_dfinder.rpy`：原文 `if/else` 一个块体里 2 张拍
     （`if:2` ×2），产物把它写成两个 `if not (…)` 块 → `if:2` + `if:3`；
   · `pomodorro.rpy` / `mini_game1.rpy` / `mini_game_puzzle.rpy` 同型。
   `if:` 段**不参与渲染**（`if` 从 `Condition` 列来），所以拿它当"结构位守恒"
   的判据是把"故意展平"当缺陷 —— 而 `Condition` 的保真由 N2/N3 逐字节盯着。
   真正承载语义的是**菜单/选项体归属**，那一段一个字符都不许变。
6. **相邻同条件拍在产物里写成多个 `if` 块**（同上，文本形状差异，语义一致）：
   原文一个 `if` 块体里的两张拍（`show screen …` / `jump …`），渲染时各写一个
   `if <同一个条件>:`。产物能跑、语义一样，只是块数不同 —— 这也是上面
   `if:<n>` 编号漂移的来源。（真正要修的是"相邻同条件拍并块"，
   属于文本形状优化，不是数据丢失；记在这里免得下次当 bug 猜。）

用法：
    D:/Python/python.exe tools/hell_nest_inline.py              # 全跑
    D:/Python/python.exe tools/hell_nest_inline.py -v           # 打印每个用例的产物
    D:/Python/python.exe tools/hell_nest_inline.py --real       # 只跑真实项目
"""
from __future__ import annotations

import argparse
import itertools
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.core.nest_path import option_key, split_nest
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.pipeline.compile import merge_incrementally
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import (nest_placement_conflicts,
                                          render_beats_to_rpy)
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

REAL_PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')
NOTE = '# N:'                      # 注释的记号，便于统计与定位
_TAG = itertools.count()           # 注释记号全局唯一（同名会被"翻倍"判据误判）


# ---------------------------------------------------------------------------
#  原料生成
# ---------------------------------------------------------------------------

def _body(depth: int, kinds: list, pad: str, notes: bool = True,
          opt_texts: list = None, tag: str = '') -> list:
    """
    递归生成一个交替嵌套的块体。

    奇数层 `if`、偶数层 `menu`（由 `kinds` 决定），每一层都**带行尾注释** ——
    这是关键：注释必须与嵌套同时在场，才能测到"注释挂在算错的那一层"。

    `tag` 是**每份源码唯一的**前缀。同一份 body 会在多个 label 里复用，
    标签不唯一的话"注释翻倍"那条判据会自己骗自己（第一版就是这么假红的：
    `five_labels_deep` 把 5 个 label 的同名注释数成"翻倍"）。
    """
    ind = '    '
    if depth >= len(kinds):
        line = f'{pad}e "叶子{depth}"'
        return [line + (f'  {NOTE}{tag}{next(_TAG)}' if notes else '')]
    kind = kinds[depth]
    if kind == 'if':
        head = f'{pad}if flag{depth}:'
        if notes:
            head += f'  {NOTE}{tag}{next(_TAG)}'
        body = _body(depth + 1, kinds, pad + ind, notes, opt_texts, tag)
        # 再补一个 else 分支：验证分支注释与"分支体各归各位"
        alt = _body(depth + 1, kinds, pad + ind, notes, opt_texts, tag)
        out = [head] + body
        out.append(f'{pad}else:'
                   + (f'  {NOTE}{tag}{next(_TAG)}' if notes else ''))
        out.extend(alt)
        return out
    texts = (opt_texts or ['选项1', '选项2'])[:2] or ['选项1']
    body_pad = pad + ind + ind
    out = [f'{pad}menu:'
           + (f'  {NOTE}{tag}{next(_TAG)}' if notes else '')]
    for k, t in enumerate(texts):
        head = f'{pad}{ind}"{t}":'
        if notes:
            head += f'  {NOTE}{tag}{next(_TAG)}'
        out.append(head)
        out.extend(_body(depth + 1, kinds, body_pad, notes, opt_texts, tag))
    return out


def case(name: str, label: str, body: list) -> tuple:
    return name, f'label {label}:\n' + '\n'.join(body) + '\n'


def build_cases() -> list:
    cases = []
    ind = '    '

    # ---- 深度 1→5：if / menu 交替，每层都带注释 -------------------------
    for kinds in (['if'], ['menu'], ['if', 'menu'], ['if', 'menu', 'if'],
                  ['if', 'menu', 'if', 'menu'],
                  ['if', 'menu', 'if', 'menu', 'if'],
                  ['menu', 'if', 'menu'], ['menu', 'if', 'menu', 'if']):
        tag = ''.join(k[0] for k in kinds)
        cases.append(case(f'depth{len(kinds)}_{tag}', 't',
                          _body(0, kinds, ind, tag=f'{len(kinds)}{tag}_')))

    # ---- 30 个选项：位置键 A..Z 之后必须是 A27，不许撞键 ---------------
    lines = ['label t:', '    menu:  ' + NOTE + 'menu30']
    for i in range(1, 31):
        lines.append(f'        "选项{i}":  {NOTE}o{i}')
        lines.append(f'            e "体{i}"')
    cases.append(('menu_30_options', '\n'.join(lines) + '\n'))

    # ---- 三层菜单套娃（同一块类型连续嵌套）-----------------------------
    nest3 = [
        'label t:',
        '    menu:',
        '        "外1":  ' + NOTE + 'm1a',
        '            menu:',
        '                "中1":  ' + NOTE + 'm2a',
        '                    menu:',
        '                        "内1":  ' + NOTE + 'm3a',
        '                            e "最里面"',
        '                        "内2":',
        '                            e "内二"',
        '                "中2":',
        '                    e "中二"',
        '        "外2":',
        '            e "外二"',
    ]
    cases.append(('menu_in_menu_in_menu', '\n'.join(nest3) + '\n'))

    # ---- 选项体里的 if/elif/else 链（分支注释）-------------------------
    elif_case = [
        'label t:',
        '    menu:',
        '        "选":',
        '            if a:  ' + NOTE + 'if_a',
        '                e "甲"',
        '            elif b:  ' + NOTE + 'if_b',
        '                e "乙"',
        '            else:  ' + NOTE + 'if_c',
        '                e "丙"',
        '            e "选项体尾巴"  ' + NOTE + 'tail',
    ]
    cases.append(('elif_chain_in_option_body', '\n'.join(elif_case) + '\n'))

    # ---- 对抗性选项文案：引号 / `>` / `#` / 冒号 / emoji / 全角 ---------
    adv = [
        'label t:',
        '    menu:',
        '        "他说：\\"你好\\"":  ' + NOTE + 'q1',
        '            e "A"',
        '        "a > b 的选项":  ' + NOTE + 'q2',
        '            e "B"',
        '        "带 # 号":  ' + NOTE + 'q3',
        '            e "C"',
        '        "全　角　空　格":',
        '            e "D"',
        '        "😀 表情 + —— 「」":',
        '            e "E"',
        '        "路径 C:\\\\new\\\\test":',
        '            e "F"',
    ]
    cases.append(('adversarial_option_text', '\n'.join(adv) + '\n'))

    # ---- 深处两个选项**中间**插一拍（第一轮抓过的归属问题）-------------
    between = [
        'label t:',
        '    if outer:',
        '        menu:',
        '            "一":',
        '                e "1"',
        '            e "夹在选项中间"  ' + NOTE + 'between',
        '            "二":',
        '                e "2"',
    ]
    cases.append(('beat_between_options_deep', '\n'.join(between) + '\n'))

    # ---- 空块（只有结构、没有台词）------------------------------------
    empty = [
        'label t:',
        '    menu:',
        '        "空的":',
        '        "有内容":',
        '            e "x"',
    ]
    cases.append(('empty_option_body', '\n'.join(empty) + '\n'))

    # ---- 五个 label 连着，每个都带嵌套 + 注释（label 边界 × 结构）------
    many = []
    for i in range(5):
        many.append(f'label t{i}:')
        many.extend(_body(0, ['if', 'menu'], ind, True, tag=f'L{i}_'))
        many.append(f'    e "尾巴{i}"  {NOTE}tail{i}')
    cases.append(('five_labels_deep', '\n'.join(many) + '\n'))

    # ---- 【最小复现】菜单套在**选项体**里（两层，别的什么都不加）---------
    # 这一条是给"嵌套菜单被展平"那个真 bug 用的：它不含 if、不含注释、
    # 不含对抗文本 —— 只要这两层在，缺陷就现形。修好之后它就是回归钉。
    minimal = [
        'label t:',
        '    menu:',
        '        "外":',
        '            e "前"',
        '            menu:',
        '                "内":',
        '                    e "深深"',
        '            e "后"',
    ]
    cases.append(('minimal_nested_menu', '\n'.join(minimal) + '\n'))

    # ---- 【真实项目实测】`scene bg bg1_1` 这种 tag+属性 写法 --------------
    # 真实项目 `definitions.rpy:111` 定义的是 `image bg bg1_1`（tag `bg` +
    # 属性 `bg1_1`），全工程**没有** `bg_bg1_1` 这个名字。而表那条路
    # 把它改写成 `scene bg_bg1_1` → 运行时找不到这张图。
    names = [
        'label t:',
        '    scene bg bg1_1',
        '    show lan agreeable at right',
        '    show bg bg2_1 with dissolve',
    ]
    cases.append(('scene_show_tag_attr', '\n'.join(names) + '\n'))

    # ---- 【O2 复核】相邻两条 show，`with` 只属于第二条 -------------------
    # 早先记录里的"`with_` 跨相邻 show 泄漏"，一直没量过。判据很简单：
    # 产物必须与原文**逐字相同** —— `with dissolve` 只能贴在被写的那一行。
    stage_with = [
        'label t:',
        '    show ling happy at left',
        '    show ling angry at right with dissolve',
        '    hide ling with fade',
    ]
    cases.append(('stage_with_adjacent', '\n'.join(stage_with) + '\n'))

    return cases


# ---------------------------------------------------------------------------
#  单份源码 → 一组观测
# ---------------------------------------------------------------------------

def _beats(text: str, path: Path) -> tuple:
    path.write_text(text, encoding='utf-8')
    analyzer = IncrementalRenPyAnalyzer()
    pr, _ = analyzer.analyze(files=[path], graph=None)
    return beats_from_parse_result(pr), getattr(analyzer, 'errors', None) or []


#: 空块补的那个 `pass`（Ren'Py 语法要求，见 §有意为之 4）在**产物再解析**时
#: 会变成一张普通拍：`raw='pass'`、别的字段全空。它**不是**内容，
#: 只是"这个块体在原文里是空的"的语法填充 —— 两边都按"没有这一拍"算。
_PASS_FILLER_FIELDS = ('text', 'comment', 'scene', 'show', 'hide', 'stage',
                       'code', 'bgm', 'sfx', 'voice')


def _is_pass_filler(beat) -> bool:
    """这一拍是不是"空块补的 `pass`"（§有意为之 4）？"""
    if (getattr(beat, 'raw', '') or '').strip() != 'pass':
        return False
    return not any(str(getattr(beat, f, '') or '').strip()
                   for f in _PASS_FILLER_FIELDS)


def _menu_scope(path):
    """
    `Nest` 路径 → **菜单口径**（N1 的判据，见 §有意为之 5）。

    只滤掉 `if:<n>` 段：
      · 那个编号是**文档序**，`elif/else` 一展平（§有意为之 1）就变，
        拿它比会把"故意展平"报成"结构丢了"；
      · `if:` 段**不参与渲染**（`if` 从 `Condition` 列来），丢了也不改产物。

    `None`（不知道 —— 老表/手工构造的拍）**原样保留**：那是"隐藏列整个掉了"
    的信号，绝不能当成"在顶层"放过（与 N4 对拍器的口径一致）。
    """
    if path is None:
        return None
    return '>'.join(s for s in split_nest(path) if not s.startswith('if:'))


def _if_shape(path):
    """`if:<n>` 段只留"有一个 if"（给"提示"用的软口径，不进判据）。"""
    if path is None:
        return None
    return '>'.join('if' if s.startswith('if:') else s
                    for s in split_nest(path))


def _paths(beats: list) -> list:
    """每个 label 内 `Nest` 路径（**菜单口径**）的多重集（排序后可比）。"""
    return sorted(f'{getattr(b, "label", "")}|{_menu_scope(getattr(b, "nest_path", None))}'
                  for b in beats if not _is_pass_filler(b))


def _if_shapes(beats: list) -> list:
    """`if:<n>` 只留"有一个 if"的多重集 —— 只用来**提示**文档序差异。"""
    return sorted(
        f'{getattr(b, "label", "")}|{_if_shape(getattr(b, "nest_path", None))}'
        for b in beats if not _is_pass_filler(b))


def _notes(rpy: str) -> list:
    """产物里带注释的**整行**（strip 后），顺序敏感 —— 用来验"同址"。"""
    return [ln.strip() for ln in rpy.splitlines() if NOTE in ln]


_MARKER = '# 由剧本表自动生成'


def _syntax_broken(rpy: str) -> list:
    """
    产物里有**跨物理行的字符串字面量**吗？

    口径**照抄第一轮**（`tools/hell_test.py::_unterminated_strings`）——
    先去掉转义序列再数引号，奇数才算坏。我自己另写的那版用正则匹配
    "未闭合的尾部"，把 `"选项":  # 注释` 这种**已经闭合**的行也报了 ——
    13 个用例里 10 个假红，差点当成"嵌套全坏"（§判据的第三次复发）。
    """
    bad = []
    for ln in rpy.splitlines():
        if '"""' in ln or "'''" in ln:
            continue
        if re.sub(r'\\.', '', ln).count('"') % 2:
            bad.append(ln)
    return bad


def _norm(rpy: str) -> str:
    """
    逐字节比对前先去掉**模板噪声**：生成标记行、首尾空行、行尾空白。

    为什么不是"干脆别比整份"：`N2`/`N3` 的价值恰恰在于**逐字节** ——
    注释贴错一行、缩进差一格，只有逐字节才抓得到。所以只滤掉那几行
    **每次渲染都会重写的模板**，其余一个字都不放过。
    """
    lines = [ln.rstrip() for ln in rpy.splitlines()
             if not ln.startswith(_MARKER)]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return '\n'.join(lines)


def _dup_notes(rpy: str) -> dict:
    """产物里**出现多于一次**的注释记号（`{标记: 次数}`）。"""
    counts: dict = {}
    for ln in _notes(rpy):
        m = re.search(re.escape(NOTE) + r'(\S+)', ln)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return {k: v for k, v in counts.items() if v > 1}
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return '\n'.join(lines)


def _unterminated(rpy: str) -> list:
    return _syntax_broken(rpy)


def run_case(name: str, src: str, tmp: Path, verbose: bool) -> list:
    """跑一份源码的全部判据，返回问题列表 `[(判据, 说明), …]`。"""
    bad: list = []
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / 'ch.rpy'

    beats, perr = _beats(src, f)
    if perr:
        return [('解析', f'原文解析就报错：{perr[:2]}')]
    if not beats:
        return [('解析', '一张拍都没有')]

    # N4 对拍器全绿
    conflicts = nest_placement_conflicts(beats)
    if conflicts:
        bad.append(('N4对拍', f'{len(conflicts)} 处结构列与老推断不一致：'
                              f'{conflicts[:3]}'))

    r1 = render_beats_to_rpy(beats)
    if _unterminated(r1):
        bad.append(('N6自洽', f'直接渲染就出现未闭合字符串：{_unterminated(r1)[:2]}'))

    # N3 全量 Excel 往返必须逐字节一致
    xl = d / 'ch.xlsx'
    export_beats_to_excel(beats, xl)
    back = import_beats_from_excel(xl)
    r2 = render_beats_to_rpy(back)
    if _norm(r2) != _norm(r1):
        bad.append(('N3两路', _diff(_norm(r1), _norm(r2))))

    # 两轮往返（隐藏列会不会逐轮退化）
    xl2 = d / 'ch2.xlsx'
    export_beats_to_excel(back, xl2)
    r2b = render_beats_to_rpy(import_beats_from_excel(xl2))
    if _norm(r2b) != _norm(r2):
        bad.append(('N3两轮', _diff(_norm(r2), _norm(r2b))))

    # N1 结构守恒（对 `r2b`：两轮往返之后仍要一致）
    again, aerr = _beats(r2b, d / 'again.rpy')
    if aerr:
        bad.append(('N6自洽', f'产物不能再解析：{aerr[:2]}'))
    if _paths(again) != _paths(beats):
        bad.append(('N1结构', _path_diff(beats, again)))

    # N2 不动点
    r3 = render_beats_to_rpy(again)
    if _norm(r3) != _norm(r2b):
        bad.append(('N2不动点', _diff(_norm(r2b), _norm(r3))))

    # N5 注释：至多一次 + 与 R1 同址
    dup = _dup_notes(r2b)
    if dup:
        bad.append(('N5注释', f'注释翻倍：{dup}'))
    if _notes(r2b) != _notes(r1):
        bad.append(('N5注释', f'注释与直接渲染不同址：\n  R1={_notes(r1)[:4]}\n'
                              f'  R2={_notes(r2b)[:4]}'))

    # 原地增量合并：源文件在，产物应"每条注释恰好一次"
    src_line = d / 'inplace.rpy'
    src_line.write_text(src, encoding='utf-8')
    outcome = merge_incrementally(beats, r1, source_path=src_line)
    if getattr(outcome, 'used_patch', False):
        merged = outcome.text
        dup = _dup_notes(merged)
        if dup:
            bad.append(('N5合并', f'增量产物里注释翻倍：{dup}'))
        if _unterminated(merged):
            bad.append(('N6自洽', f'增量产物有未闭合字符串：{_unterminated(merged)[:2]}'))

    if verbose:
        print('-' * 70)
        print(f'[{name}] R1（直接渲染）')
        print(r1)
        print(f'[{name}] R2（表往返后）')
        print(r2b)
    return bad


def _diff(a: str, b: str) -> str:
    la, lb = a.splitlines(), b.splitlines()
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else '<缺行>'
        y = lb[i] if i < len(lb) else '<缺行>'
        if x != y:
            return (f'第 {i + 1} 行起不同：\n'
                    f'    A: {x!r}\n    B: {y!r}')
    return '内容相同但尾部有差异'


def _path_diff(beats: list, again: list) -> str:
    a, b = _paths(beats), _paths(again)
    lost = [x for x in a if x not in b][:4]
    extra = [x for x in b if x not in a][:4]
    return f'菜单口径 少 {len(a) - len(b) + len(extra)} 条 / 多 {len(extra)} 条\n' \
           f'    丢: {lost}\n    多: {extra}'


def _if_order_only_diff(beats: list, again: list) -> bool:
    """
    菜单口径**一致**、只是 `if:<n>` 的文档序不同（§有意为之 5/6）？

    这一档不是失败，但要**报出来** —— 免得下一次真出了 if 结构问题，
    被"这条判据本来就不看 if"当成默认行为放过。
    """
    return (_if_shapes(beats) != _if_shapes(again)
            and _paths(beats) == _paths(again))


# ---------------------------------------------------------------------------
#  真实项目：整库扫一遍
# ---------------------------------------------------------------------------

def run_real(verbose: bool) -> list:
    game = REAL_PROJECT / 'game'
    if not game.exists():
        print(f'⚠ 没有真实项目（{REAL_PROJECT}），跳过')
        return []
    files = sorted(p for p in game.rglob('*.rpy')
                   if not p.name.endswith('.generated.rpy'))
    print(f'真实项目：{len(files)} 个 .rpy')
    bad: list = []
    tmp = Path(tempfile.mkdtemp(prefix='hell_nest_real_'))
    stat = {'files': 0, 'notes': 0, 'kept': 0, 'deep': 0, 'nested': 0,
            'nested_menu': 0, 'nested_files': set(), 'if_order': []}

    for p in files:
        analyzer = IncrementalRenPyAnalyzer()
        pr, _ = analyzer.analyze(files=[p], graph=None)
        beats = beats_from_parse_result(pr)
        if not beats:
            continue
        stat['files'] += 1
        for b in beats:
            segs = split_nest(getattr(b, 'nest_path', '') or '')
            stat['deep'] = max(stat['deep'], len(segs))
            if any(s.startswith('menu:') for s in segs):
                stat['nested'] += 1
            # **真嵌套菜单**：路径里有两个 `menu:` 段 = 菜单套在选项体里的菜单
            if sum(1 for s in segs if s.startswith('menu:')) >= 2:
                stat['nested_menu'] += 1
                stat['nested_files'].add(p.name)

        conf = nest_placement_conflicts(beats)
        if conf:
            bad.append((p.name, 'N4对拍', f'{len(conf)} 处：{conf[:2]}'))

        notes = [b.inline_comment for b in beats
                 if getattr(b, 'inline_comment', '')]
        conds = [b.cond_comment for b in beats
                 if getattr(b, 'cond_comment', '')]
        r1 = render_beats_to_rpy(beats)
        # 全量 Excel 往返（**这是"表那条路"**）
        xl = tmp / f'{p.stem}.xlsx'
        try:
            export_beats_to_excel(beats, xl)
            r2 = render_beats_to_rpy(import_beats_from_excel(xl))
        except Exception as exc:                       # noqa: BLE001
            bad.append((p.name, 'N3两路', f'导出/回读抛异常：{exc!r}'))
            continue
        if r2 != r1:
            bad.append((p.name, 'N3两路', _diff(r1, r2)))
        if _notes(r2) != _notes(r1):
            bad.append((p.name, 'N5注释',
                        f'注释不同址：R1 {len(_notes(r1))} 条 / '
                        f'R2 {len(_notes(r2))} 条'))
        again, aerr = _beats(r2, tmp / f'{p.stem}.again.rpy')
        if aerr:
            bad.append((p.name, 'N6自洽', f'产物不能再解析：{aerr[:1]}'))
        elif _paths(again) != _paths(beats):
            bad.append((p.name, 'N1结构', _path_diff(beats, again)))
        elif _if_order_only_diff(beats, again):
            # 菜单归属一致、只有 `if:<n>` 文档序不同 —— **报出来但不计失败**
            stat['if_order'].append(p.name)
        stat['notes'] += len(notes) + len(conds)
        stat['kept'] += sum(1 for n in notes + conds if n in r2)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f'  文件 {stat["files"]} 个 / 最深嵌套 {stat["deep"]} 层 / '
          f'含菜单的拍 {stat["nested"]} 张')
    print(f'  **真嵌套菜单**（路径里两个 menu 段）的拍 {stat["nested_menu"]} 张，'
          f'分布在 {sorted(stat["nested_files"])}')
    print(f'  行尾+条件注释 {stat["notes"]} 条，往返后还在 {stat["kept"]} 条')
    print(f'  提示（不计失败）：`if:<n>` 文档序变了但菜单归属一致的文件 '
          f'{len(stat["if_order"])} 个 —— {sorted(stat["if_order"])}')
    return bad


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('-v', '--verbose', action='store_true')
    ap.add_argument('--real', action='store_true', help='只跑真实项目')
    ap.add_argument('--only', default='', help='只跑名字里含这个串的用例')
    args = ap.parse_args()

    total = 0
    for name, src, bad in _synth(args.verbose, args.only):
        total += 1
        if bad:
            print(f'\n[FAIL] {name}')
            for judge, msg in bad:
                print(f'   ✗ {judge}: {msg}')
        else:
            print(f'[ ok ] {name}')

    print(f'\n合成用例 {total} 个')
    if not args.only:
        real_bad = run_real(args.verbose)
        if real_bad:
            print(f'\n[FAIL] 真实项目 {len(real_bad)} 处')
            for nm, judge, msg in real_bad[:15]:
                print(f'   ✗ {nm} · {judge}: {msg}')
        else:
            print('[ ok ] 真实项目全绿')
        fail = sum(1 for _n, _s, b in _synth(False, '') if b) + len(real_bad)
    else:
        fail = sum(1 for _n, _s, b in _synth(False, args.only) if b)
    print(f'\n失败用例合计：{fail}')
    return 1 if fail else 0


def _synth(verbose: bool, only: str):
    tmp = Path(tempfile.mkdtemp(prefix='hell_nest_'))
    try:
        for name, src in build_cases():
            if only and only not in name:
                continue
            if verbose:
                print('=' * 70)
                print(f'源码 [{name}]')
                print(src.rstrip())
            yield name, src, run_case(name, src, tmp, verbose)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
