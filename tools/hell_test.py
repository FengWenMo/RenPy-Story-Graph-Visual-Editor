#!/usr/bin/env python
"""
地狱测试（比 `brutal_test.py` 更狠的那一档）
=============================================

为什么还要一个：已有的测试都是**单维度**的 ——
`torture_test` 打结构、`brutal_test` 打随机变异、`audit_roundtrip`
打真实项目往返。而真正漏到交付后的缺陷几乎都出在**组合处**：

    顶层块 × 演出结构 × 对抗性文本 × 导出路径

这个脚本专打组合面，并且**不只看"崩没崩"**，而是断言三条不变量：

  · **I1 内容不灭** —— 原文里每个有意义的行，产物里还在（抹空白比对）；
  · **I2 结构不坏** —— 产物可解析、无 Python 语法错、label 数不减少、
    没有跨物理行的字符串字面量；
  · **I3 不许撒谎** —— 保不住的东西必须**报出来**（TODO / 拒写 / 警告）；
    静默写一份"能跑但少了东西"的脚本，是最坏的一类缺陷。

按导出路径分档判（因为三档的保真义务不同）：

  · **增量**（`--source` / `merge_incrementally`）→ 顶层块**必须全保住**；
  · **全量**（不给源）→ 演出必须保住；`init python:` 也必须保住
    （边界③已修）；其余第三类（`default` / `transform` / `screen` …）
    允许丢 —— 那是**已知边界**，CLI 层由 `OPAQUE_LOST` 门禁拦住，
    这里只计数、不算失败。

**判据要与"等价改写"区分开**（这一版修的，教训见文末 §判据）：
`elif` → `not((前一条件))`、裸 `e` → `$ e`、空台词 `e ""` → 被吞，
都是**有意为之**的行为，不是丢失。把它们一律算成"丢了"，就会把
真 bug（反斜杠写出物理换行、label 整段消失）淹没在几十条噪声里。

用法：
    D:/Python/python.exe tools/hell_test.py            # 全跑
    D:/Python/python.exe tools/hell_test.py --n 200    # 随机样本数
    D:/Python/python.exe tools/hell_test.py --only adversarial

只读；产物一律写临时目录。

------------------------------------------------------------------
§判据：这一版修过的三个"我自己的错"
------------------------------------------------------------------
1. 上一版所有用例都传 `strict=True`，但 `_roundtrip` **根本没走增量**
   （只调了 `compile_beats`）。于是"增量路径丢了顶层块 29 例"其实是
   全量路径的已知边界 —— **测试自称在测 A，实际在测 B**。
   现在真调 `merge_incrementally(source_path=…)`。
2. `elif X:` / `else:` 被当成丢失。它们是 A2 契约里的**扁平展开**。
   现在改成"条件表达式还在产物里"就算等价。
3. `e ""` 被当成丢失。真实项目 2039 张拍里 kind=say 且台词为空的有
   68 张、**说话人全是空**（纯舞台/孤儿指令拍）—— 给它们补 `e ""`
   是凭空造垃圾，所以空台词被吞是设计如此。
"""
from __future__ import annotations

import argparse
import random
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.pipeline.compile import compile_beats, merge_incrementally
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

# ---------------------------------------------------------------------------
#  原料：刻意挑"最容易炸"的那些
# ---------------------------------------------------------------------------

#: 对抗性台词（引号/反斜杠/百分号/花括号/emoji/以等号开头/行内 # …）
ADVERSARIAL = {
    'backslash': '    e "路径 C:\\new\\test"',
    'nested_quote': '    e "他说：\\"你好\\""',
    'newline_escape': '    e "第一行\\n第二行"',   # Ren'Py 里最常用的强制换行
    'tab_escape': '    e "列1\\t列2"',
    'brace': '    e "if {x} then"',
    'percent': '    e "100% 完成"',
    'equals_lead': '    e "=1+1"',
    'hash_inline': '    e "注释 # 后面还有字"',
    'emoji': '    e "你好😀🎮"',
    'fullwidth_space': '    e "全　角　空　格"',
    'trailing_space': '    e "尾部有空格 "',
    'empty_text': '    e ""',
    'only_speaker': '    e',
    'unicode_punct': '    e "——「」《》……"',
    'tab_inside': '    e "中间\t有制表符"',
    'very_long': '    e "' + '字' * 5000 + '"',
}

#: 对抗性结构
STRUCTURES = {
    'menu_in_menu': (
        'label a:\n'
        '    menu:\n'
        '        "外":\n'
        '            menu:\n'
        '                "内1":\n'
        '                    e "一"\n'
        '                "内2":\n'
        '                    e "二"\n'
        '        "退出":\n'
        '            return\n'
    ),
    'if_chain_10': (
        'label a:\n'
        + ''.join(f'    if v == {i}:\n        e "{i}"\n'
                  for i in range(10))
        + '    else:\n        e "other"\n'
    ),
    'branch_show_say': (          # 刁钻测试抓到过 T4
        'label a:\n'
        '    if p == "cafe":\n'
        '        show bgcafe\n'
        '        e "在咖啡店"\n'
        '    elif p == "library":\n'
        '        show bglibrary\n'
        '        e "在图书馆"\n'
    ),
    'adjacent_menus': (
        'label a:\n'
        '    menu:\n'
        '        "一":\n'
        '            e "1"\n'
        '    menu:\n'
        '        "二":\n'
        '            e "2"\n'
    ),
    'beat_between_options': (
        'label a:\n'
        '    menu:\n'
        '        "一":\n'
        '            e "1"\n'
        '    e "夹在中间"\n'
        '    menu:\n'
        '        "二":\n'
        '            e "2"\n'
    ),
    # **真 bug 回归**（2026-10-05）：没有台词的 label 段，其语句会被吸进
    # 下一个 label 的第一张拍 → 本 label 在产物里**整个消失**，不报错。
    # 真实项目 51 个 label 里丢 4 个，其中 `location` 带 `call screen map`。
    'orphan_label_then_next': (
        'label a:\n'
        '    $ focusmode = False\n'
        '    show screen home\n'
        'label b:\n'
        '    e "后一句"\n'
    ),
    'orphan_label_at_end': (
        'label a:\n'
        '    e "前一句"\n'
        'label b:\n'
        '    play music "m.ogg" fadein 1.0\n'
        '    show screen home\n'
        '    $ renpy.pause(hard=True)\n'
    ),
    'empty_menu': 'label a:\n    menu:\n',
    'dup_label': (
        'label a:\n    e "1"\n'
        'label a:\n    e "2"\n'
    ),
    'empty_label': 'label a:\n',
    'jump_missing': 'label a:\n    e "x"\n    jump nowhere\n',
    'return_only': 'label a:\n    return\n',
    'deep_indent': (
        'label a:\n'
        '    if a:\n'
        '        if b:\n'
        '            if c:\n'
        '                e "deep"\n'
    ),
}

#: 顶层块（边界③的新代码，最可能有雷）
TOPLEVEL = {
    'init_python_simple': 'init python:\n    LONE = 1\n',
    'init_python_blank': (
        'init python:\n'
        '    import random\n'
        '\n'
        '    def f():\n'
        '        return random.randint(1, 3)\n'
    ),
    'init_python_comment': (
        'init python:\n'
        '    # 整行注释\n'
        '    X = 1  # 行尾注释\n'
    ),
    'init_python_nested': (
        'init python:\n'
        '    def outer():\n'
        '        def inner():\n'
        '            return 42\n'
        '        return inner()\n'
    ),
    'init_python_multi': (
        'init python:\n    A = 1\n'
        '\n'
        'init python:\n    B = 2\n'
    ),
    'init_python_cjk': 'init python:\n    NAME = "中文字符串"\n',
    'init_python_bracket': (
        'init python:\n'
        '    L = [\n'
        '        (1, 2),\n'
        '        (3, 4),\n'
        '    ]\n'
    ),
    'default_cjk': 'default 分数 = 0\n',
    'define_char': 'define e = Character("艾琳")\n',
    'transform_atl': 'transform my_tf:\n    xalign 0.5\n    linear 1.0 xalign 1.0\n',
    'screen_def': 'screen my_hud():\n    text "HUD"\n',
}


def _combine(top: str, body: str, where: str) -> str:
    """顶层块 × 演出 的位置组合（顶层块在前/中/后）。"""
    if where == 'before':
        return f'{top}\n{body}'
    if where == 'after':
        return f'{body}\n{top}\n'
    return f'{top}\n{body}\n{top}\n'


# ---------------------------------------------------------------------------
#  三条不变量
# ---------------------------------------------------------------------------

def squash(s: str) -> str:
    return ''.join(s.split())


def _escaped(s: str) -> str:
    """按 `_quote` 的口径把真控制字符写成 Ren'Py 转义（与渲染同源）。

    原文里直接写了个**真 TAB**，产物里是 `\\t` 两个字符 —— 语义完全一样，
    逐字节不同。少了这一步就会把它报成"丢了"。
    """
    return (s.replace('\\', '\\\\')
             .replace('\t', '\\t')
             .replace('\n', '\\n')
             .replace('\r', '\\r'))


def _meaningful_lines(text: str) -> list:
    """原文里"有意义"的行（非空、非纯注释）。"""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith('#'):
            continue
        out.append(s)
    return out


def _parse(text: str, tmp: Path, name='h.rpy'):
    f = tmp / name
    f.write_text(text, encoding='utf-8')
    a = IncrementalRenPyAnalyzer()
    pr, _ = a.analyze(files=[f], graph=None)
    return pr, beats_from_parse_result(pr)


def _roundtrip(text: str, tmp: Path, name='h.rpy', path='full') -> tuple:
    """
    (产物文本, 是不是炸了, 增量有没有真的打上补丁)。

    `path='incr'` 时**真走增量**（`merge_incrementally` 带源文件）——
    上一版自称"增量"却只调了 `compile_beats`，那是**测了个假东西**。
    """
    f = tmp / name
    try:
        _pr, beats = _parse(text, tmp, name)
    except Exception as e:                             # noqa: BLE001
        return f'<<解析就炸了：{e}>>', True, False
    xl = tmp / f'{name}.xlsx'
    try:
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        gen = compile_beats(back).rpy_text
        if path == 'incr':
            out = merge_incrementally(back, gen, source_path=f)
            return out.text, False, bool(out.used_patch)
        return gen, False, False
    except Exception as e:                             # noqa: BLE001
        return f'<<往返炸了：{e}>>', True, False


def _labels(text: str) -> list:
    return re.findall(r'^label\s+([^\s:#]+)', text, re.M)


def _unterminated_strings(text: str) -> list:
    """
    产物里有**跨物理行的字符串字面量**吗？

    这就是 2026-10-05 那个真 bug 的通用形态：`e "路径 C:\\new\\test"`
    里的 `\\n` 在 Beat 里已经是**真的**换行符，渲染时不转义就写出物理换行，
    产物直接不可解析。Ren'Py 里 `\\n` 是常用写法（强制换行），不是边缘情况。
    """
    bad = []
    for ln in text.splitlines():
        if '"""' in ln:
            continue      # Python 三引号 docstring 本来就能跨行，判不了
        s = re.sub(r'\\.', '', ln)          # 去掉转义序列再数引号
        if s.count('"') % 2:
            bad.append(ln)
    return bad


def _py_ok(text: str) -> bool:
    """产物里的 python 块能不能编译（门禁同口径：整块 + 括号平衡）。"""
    import ast
    import textwrap
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith('python:') or s.startswith('init python:'):
            body, i = [], i + 1
            base = None
            while i < len(lines):
                ln = lines[i]
                if not ln.strip():
                    body.append('')
                    i += 1
                    continue
                ind = len(ln) - len(ln.lstrip())
                if base is None:
                    base = ind
                if ind < base:
                    break
                body.append(ln)
                i += 1
            src = textwrap.dedent('\n'.join(body))
            try:
                ast.parse(src)
            except SyntaxError:
                return False
            continue
        i += 1
    return True


#: 有意为之、**语义等价**的改写（不是丢失）
def _equivalent(line: str, out_sq: str, cond_sq: str = '') -> bool:
    s = line.strip()
    # 1) `elif X:` → `if X and not((前一条件)):`（A2 契约的扁平展开）
    if s.startswith('elif '):
        return squash(s[5:].rstrip(':')) in out_sq
    # 2) `else:` → `if not((前一条件)):`（同上）
    if s == 'else:':
        return True
    # 3) 嵌套 `if a: / if b: / if c:` → `if ((a) and (b)) and (c):`
    #    （合取扁平化，语义等价；条件片段还在产物里就行）
    if s.startswith('if ') and s.endswith(':'):
        return squash(s[3:-1]) in (cond_sq or out_sq)
    # 3) 裸标识符（`e` 单独一行）→ 归一化成 `$ e`
    if re.fullmatch(r'[A-Za-z_][\w.]*', s):
        return squash('$ ' + s) in out_sq
    # 4) 空台词（`e ""`）→ 被吞。真实项目 2039 拍里 68 张空台词拍
    #    **说话人全为空**（纯舞台/孤儿指令拍），给它们补 `e ""` 是造垃圾。
    if re.fullmatch(r'[\w.]+ ""', s):
        return True
    return False


#: 第三类里**只有** `init python:` 有随表通道（边界③）；其余靠增量保。
def _has_toplevel_channel(line: str) -> bool:
    return line.startswith(('init python:', 'python:'))


#: 资产引用会被**规范化**成无引号写法（`play music "m.ogg"` →
#: `play music m.ogg`）—— 符号表认出它是音频资产 ID 后输出标识符形式。
#: Ren'Py 里两种写法等价，不改语义。
_ASSET_HEADS = ('play ', 'stop ', 'show ', 'scene ', 'hide ', 'voice ',
                'window ', 'image ')


def _asset_equivalent(line: str, have_no_quote: set) -> bool:
    s = line.strip()
    if not s.startswith(_ASSET_HEADS):
        return False
    return squash(s.replace('"', '')) in have_no_quote


def check(name: str, src: str, tmp: Path, top_text: str = '',
          path: str = 'full') -> tuple:
    """
    跑一个用例，返回 `(真失败, 已知边界)`，元素形如 `(不变量, 证据)`。
    """
    bad, known = [], []
    out, exploded, patched = _roundtrip(src, tmp, name=f'{name}.rpy',
                                        path=path)
    if exploded:
        return [('崩了', out)], known

    # ---- I2：结构不坏 ----
    if not _py_ok(out):
        bad.append(('I2 产物有 python 语法错', out[:200]))
    broken = _unterminated_strings(out)
    if broken:
        bad.append(('I2 产物有跨行字符串（语法坏）', str(broken[:2])))
    src_labels, out_labels = _labels(src), _labels(out)
    if len(out_labels) < len(set(src_labels)):
        bad.append(('I2 label 少了', f'{src_labels} → {out_labels}'))
    dupes = {n for n in out_labels if out_labels.count(n) > 1}
    if dupes and len(set(src_labels)) == len(src_labels):
        bad.append(('I2 产物内部重复 label', str(dupes)))

    # ---- I1：内容不灭 ----
    want = _meaningful_lines(src)
    have = {squash(l) for l in out.splitlines()}
    have_nq = {squash(l.replace('"', '')) for l in out.splitlines()}
    out_sq = squash(out)
    #: 产物里所有 `if …:` 行 —— 判断"条件还在不在"要限定在这里，
    #: 否则 `if a:` 只因产物里某个地方有个 `a` 就被判等价。
    cond_sq = squash('\n'.join(ln for ln in out.splitlines()
                               if ln.strip().startswith(('if ', 'elif '))))
    top_lines = set(_meaningful_lines(top_text))
    lost = [l for l in want
            if squash(l) not in have
            and squash(_escaped(l)) not in have
            and not _asset_equivalent(l, have_nq)]
    lost = [l for l in lost if not _equivalent(l, out_sq, cond_sq)]

    top_lost = [l for l in lost if l in top_lines]
    body_lost = [l for l in lost if l not in top_lines]

    if body_lost:
        bad.append(('I1 演出内容丢了', str(body_lost[:4])))

    if top_lost:
        if path == 'incr' and patched:
            # 增量路径：顶层块必须全保住，一个都不能少
            bad.append(('I1 增量路径丢了顶层块', str(top_lost[:4])))
        elif path == 'incr' and not patched:
            known.append(('增量没打上（退回全量）', str(top_lost[:2])))
        else:
            # 全量路径：`init python:` 现在有随表通道，丢了就是 bug；
            # 其余第三类是已知边界（CLI 层 OPAQUE_LOST 拦）。
            real = [l for l in top_lost if _has_toplevel_channel(l)]
            if real:
                bad.append(('I1 全量路径丢了 init python', str(real[:3])))
            rest = [l for l in top_lost if not _has_toplevel_channel(l)]
            if rest:
                known.append(('全量丢第三类（已知边界）', str(rest[:3])))
    return bad, known


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=60, help='随机组合样本数')
    ap.add_argument('--only', default='', help='只跑某一组')
    ap.add_argument('--path', default='both',
                    choices=('full', 'incr', 'both'),
                    help='导出路径：全量 / 增量 / 两条都跑')
    args = ap.parse_args(argv)

    rng = random.Random(20261004)
    findings = []
    knowns = []
    paths = ('full', 'incr') if args.path == 'both' else (args.path,)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        def run(name, src, top=''):
            ok = True
            for p in paths:
                bad, known = check(f'{name}@{p}', src, tmp,
                                   top_text=top, path=p)
                for inv, ev in bad:
                    findings.append((f'{name}@{p}', inv, ev))
                for inv, ev in known:
                    knowns.append((f'{name}@{p}', inv, ev))
                ok = ok and not bad
            return ok

        print('=' * 64)
        print(f'地狱测试 · 组合面   路径={args.path}')
        print('=' * 64)

        # ---- 1. 对抗性台词 × 普通结构 ----
        if args.only in ('', 'adversarial'):
            print('\n-- 1. 对抗性台词 --')
            ok = 0
            for k, line in ADVERSARIAL.items():
                src = f'label a:\n{line}\n'
                if run(f'adv_{k}', src):
                    ok += 1
            print(f'  {ok}/{len(ADVERSARIAL)} 通过')

        # ---- 2. 对抗性结构 ----
        if args.only in ('', 'structure'):
            print('\n-- 2. 对抗性结构 --')
            ok = 0
            for k, src in STRUCTURES.items():
                if run(f'struct_{k}', src):
                    ok += 1
            print(f'  {ok}/{len(STRUCTURES)} 通过')

        # ---- 3. 顶层块 × 位置（边界③的新代码）----
        if args.only in ('', 'toplevel'):
            print('\n-- 3. 顶层块 × 位置 --')
            body = 'label a:\n    e "台词"\n'
            ok = 0
            total = 0
            for k, top in TOPLEVEL.items():
                for where in ('before', 'after', 'both'):
                    total += 1
                    if run(f'top_{k}_{where}',
                           _combine(top, body, where), top):
                        ok += 1
            print(f'  {ok}/{total} 通过')

        # ---- 4. 顶层块 × 对抗结构（组合雷区）----
        if args.only in ('', 'combo'):
            print('\n-- 4. 顶层块 × 对抗结构 --')
            ok = 0
            total = 0
            for tk in ('init_python_blank', 'init_python_nested',
                       'init_python_bracket', 'transform_atl'):
                for sk in ('menu_in_menu', 'branch_show_say',
                           'beat_between_options', 'deep_indent'):
                    total += 1
                    src = _combine(TOPLEVEL[tk], STRUCTURES[sk], 'before')
                    if run(f'combo_{tk}_{sk}', src, TOPLEVEL[tk]):
                        ok += 1
            print(f'  {ok}/{total} 通过')

        # ---- 5. 随机组合（种子固定，可复现）----
        if args.only in ('', 'random'):
            print(f'\n-- 5. 随机组合 × {args.n} --')
            tk = list(TOPLEVEL)
            sk = list(STRUCTURES)
            ak = list(ADVERSARIAL)
            ok = 0
            for i in range(args.n):
                top = TOPLEVEL[rng.choice(tk)]
                st = STRUCTURES[rng.choice(sk)]
                adv = ADVERSARIAL[rng.choice(ak)]
                where = rng.choice(('before', 'after', 'both'))
                body = st.rstrip('\n') + '\n' + adv + '\n'
                src = _combine(top, body, where)
                if run(f'rand_{i:03d}', src, top):
                    ok += 1
            print(f'  {ok}/{args.n} 通过')

    print('\n' + '=' * 64)
    by_inv = {}
    for _n, inv, _ev in knowns:
        by_inv.setdefault(inv, []).append(_n)
    if by_inv:
        print('已知边界（不算失败）：')
        for inv, names in sorted(by_inv.items(), key=lambda x: -len(x[1])):
            print(f'  · {inv}（{len(names)} 例）')

    if not findings:
        print('\n三条不变量全部守住')
        return 0
    print(f'\n抓到 {len(findings)} 处：')
    by_inv = {}
    for _n, inv, _ev in findings:
        by_inv.setdefault(inv, []).append(_n)
    for inv, names in sorted(by_inv.items(), key=lambda x: -len(x[1])):
        print(f'  · {inv}（{len(names)} 例）：{names[:5]}')
    print('\n前 8 处证据：')
    for n, inv, ev in findings[:8]:
        print(f'  [{n}] {inv}\n      {ev[:200]}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
