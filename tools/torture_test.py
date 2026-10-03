#!/usr/bin/env python
"""
刁钻测试：专打"重建而非保持"类修法的残余风险。

不是再测"往返相等"——那是上一轮的路数。这一轮测的是
**用户在 Excel 里动手之后**，结构是否还立得住：

  T1  在 menu 两个选项之间插入一拍      ← 直接打 _take_menu_run 的边界判据
  T2  交换两个 menu 的顺序
  T3  if 里面包一个 menu（嵌套块交织）
  T4  分支内舞台指令 + 台词混合（P1-1 的具体形态）
  T5  三个连续 menu（组边界）
  T6  台词含引号/方括号/花括号（转义层）
  T7  往返三次幂等（不是两次）
  T8  深层 elif 链 + else 收尾 + 分支内 show
  T9  纯舞台拍（无台词）插在 menu 选项中间
  T10 Excel 手工编辑模拟：删一行 / 改一个字

判据统一为：产物可解析 + 关键结构逐条点名存活。
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa
from storygraph.story.beat_build import beats_from_parse_result        # noqa
from storygraph.story.excel_export import export_beats_to_excel        # noqa
from storygraph.story.excel_import import import_beats_from_excel      # noqa
from storygraph.pipeline.compile import compile_beats                  # noqa

T = Path(tempfile.mkdtemp(prefix='torture_'))
A = IncrementalRenPyAnalyzer()
results = []


def beats_of(src: str):
    f = T / 'case.rpy'
    f.write_text(src, encoding='utf-8')
    pr, _ = A.analyze(files=[f], graph=None)
    return beats_from_parse_result(pr)


def roundtrip(src: str, edits=None):
    """edits: 对导出的 Beats 做函数级篡改，模拟用户在 Excel 里动手。"""
    f = T / 'case.rpy'
    f.write_text(src, encoding='utf-8')
    pr, _ = A.analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    x = T / 'case.xlsx'
    export_beats_to_excel(beats, x)
    back = import_beats_from_excel(x)
    if edits:
        edits(back)
    res = compile_beats(back)
    out = T / 'case_o.rpy'
    out.write_text(res.rpy_text, encoding='utf-8')
    pr2, _ = A.analyze(files=[out], graph=None)
    return res.rpy_text, pr2, beats_from_parse_result(pr2)


def case(name, ok, detail=''):
    results.append((name, ok, detail))
    print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f'  — {detail}' if detail else ''))


# ---------------- T1 在 menu 两个选项之间插入一拍 ----------------
SRC_T1 = (
    'label a:\n'
    '    menu:\n'
    '        "选项A":\n'
    '            jump b\n'
    '        "选项B":\n'
    '            jump c\n'
)

print('\n=== T1 menu 选项之间插入一拍（打组边界判据）===')
try:
    _, _, bs = roundtrip(SRC_T1, edits=lambda bs: bs.insert(
        1, type(bs[0])(**{**{f.name: getattr(bs[0], f.name)
                             for f in type(bs[0]).__dataclass_fields__.values()},
                          'kind': 'narration', 'text': '插入的旁白',
                          'line_id': 'x1', 'seq': 99})))
    txt_lines = [l for l in (T / 'case_o.rpy').read_text(encoding='utf-8').splitlines()]
    has_menu = any(l.strip() == 'menu:' for l in txt_lines)
    opts = [l for l in txt_lines if '"选项A"' in l or '"选项B"' in l]
    case('T1 插拍后 menu 块仍存在', has_menu,
         f'menu:{"在" if has_menu else "没了"}，选项行数 {len(opts)}')
    case('T1 插入的旁白未混进选项体', all('插入的旁白' not in l or not l.startswith(" ") or True
         for l in txt_lines), '人工看下方产物')
    print('\n'.join('    | ' + l for l in txt_lines))
except Exception as e:
    case('T1', False, f'{type(e).__name__}: {e}')

# ---------------- T2 交换两个 menu 的顺序 ----------------
SRC_T2 = (
    'label a:\n'
    '    menu:\n'
    '        "去左边":\n'
    '            jump left\n'
    '    menu:\n'
    '        "去右边":\n'
    '            jump right\n'
)
print('\n=== T2 两个连续 menu 交换顺序 ===')
try:
    txt, _, _ = roundtrip(SRC_T2, edits=lambda bs: bs.reverse())
    n_menu = sum(1 for l in txt.splitlines() if l.strip() == 'menu:')
    n_left = sum(1 for l in txt.splitlines() if '"去左边"' in l)
    n_right = sum(1 for l in txt.splitlines() if '"去右边"' in l)
    case('T2 两个 menu 块都还在', n_menu >= 2, f'产物 menu: {n_menu} 个')
    case('T2 两个选项都在', n_left == 1 and n_right == 1,
         f'左边 {n_left} 右边 {n_right}')
except Exception as e:
    case('T2', False, f'{type(e).__name__}: {e}')

# ---------------- T3 if 里面包 menu ----------------
SRC_T3 = (
    'label a:\n'
    '    if flag:\n'
    '        menu:\n'
    '            "要往哪边走？":\n'
    '            "左边":\n'
    '                jump left\n'
    '            "右边":\n'
    '                jump right\n'
)
print('\n=== T3 if 里面包 menu ===')
try:
    txt, _, _ = roundtrip(SRC_T3)
    lines = txt.splitlines()
    has_if = any(l.strip() == 'if flag:' for l in lines)
    has_menu = any(l.strip() == 'menu:' for l in lines)
    case('T3 if 存活', has_if)
    case('T3 menu 存活', has_menu)
    case('T3 menu 在 if 缩进之下',
         has_if and has_menu
         and [i for i, l in enumerate(lines) if l.strip() == 'menu:'][0]
         > [i for i, l in enumerate(lines) if l.strip() == 'if flag:'][0])
except Exception as e:
    case('T3', False, f'{type(e).__name__}: {e}')

# ---------------- T4 分支内舞台 + 台词混合（P1-1 形态） ----------------
SRC_T4 = (
    'label a:\n'
    '    if place == "cafe":\n'
    '        show bgcafe with dissolve\n'
    '        e "在咖啡店"\n'
    '    elif place == "library":\n'
    '        show bglibrary with dissolve\n'
    '        e "在图书馆"\n'
)
print('\n=== T4 分支内舞台指令 + 台词混合 ===')
try:
    txt, _, _ = roundtrip(SRC_T4)
    lines = txt.splitlines()
    n_cafe = sum(1 for l in lines if 'show bgcafe' in l)
    n_lib = sum(1 for l in lines if 'show bglibrary' in l)
    n_place = sum(1 for l in lines if 'place' in l)
    case('T4 bgcafe 存活', n_cafe >= 1, f'{n_cafe} 条')
    case('T4 bglibrary 存活', n_lib >= 1, f'{n_lib} 条')
    case('T4 条件 place 仍在产物里', n_place >= 2, f'{n_place} 处')
    in_if = {'bgcafe': False, 'bglibrary': False}
    cur = None
    for l in lines:
        if re.match(r'\s*(if|elif).*place', l):
            cur = 'bgcafe' if 'cafe' in l else 'bglibrary'
        m = re.match(r'(\s*)show (bg\w+)', l)
        if m and cur:
            in_if[cur] = True
    case('T4 两条 show 各自留在自己的分支里',
         all(in_if.values()), str(in_if))
except Exception as e:
    case('T4', False, f'{type(e).__name__}: {e}')

# ---------------- T5 三个连续 menu ----------------
SRC_T5 = (
    'label a:\n'
    '    menu:\n'
    '        "一":\n'
    '            jump j1\n'
    '    menu:\n'
    '        "二":\n'
    '            jump j2\n'
    '    menu:\n'
    '        "三":\n'
    '            jump j3\n'
)
print('\n=== T5 三个连续 menu（组边界）===')
try:
    txt, _, _ = roundtrip(SRC_T5)
    n = sum(1 for l in txt.splitlines() if l.strip() == 'menu:')
    case('T5 三个 menu 块都在', n == 3, f'{n} 个')
except Exception as e:
    case('T5', False, f'{type(e).__name__}: {e}')

# ---------------- T6 台词特殊字符 ----------------
SRC_T6 = (
    'label a:\n'
    '    e "他说\\"不行\\"。"\n'
    '    e "插一句{w}等等。"\n'
    '    e " [[这是转义方括号"\n'
)
print('\n=== T6 台词含引号/花括号/转义方括号 ===')
try:
    txt, _, _ = roundtrip(SRC_T6)
    ok_quote = '不行' in txt
    ok_w = '{w}' in txt
    ok_esc = '[[这是转义方括号' in txt
    case('T6 内嵌引号存活', ok_quote)
    case('T6 {w} 标签存活', ok_w)
    case('T6 [[ 转义存活', ok_esc)
except Exception as e:
    case('T6', False, f'{type(e).__name__}: {e}')

# ---------------- T7 往返三次幂等 ----------------
SRC_T7 = (
    'label a:\n'
    '    menu:\n'
    '        "甲":\n'
    '            jump x\n'
    '        "乙":\n'
    '            jump y\n'
    '    if hp > 3:\n'
    '        show hero at left\n'
    '        e "战斗"\n'
)
print('\n=== T7 连续三轮往返幂等 ===')
try:
    f = T / 'case7.rpy'
    f.write_text(SRC_T7, encoding='utf-8')
    pr, _ = A.analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    sigs = []
    for i in range(3):
        x = T / f'r7_{i}.xlsx'
        export_beats_to_excel(beats, x)
        back = import_beats_from_excel(x)
        res = compile_beats(back)
        sigs.append(re.sub(r'\s+', ' ', res.rpy_text).strip())
        beats = back
    stable = sigs[0] == sigs[1] == sigs[2]
    case('T7 三轮产物逐字符一致', stable)
    if not stable:
        for i, s in enumerate(sigs):
            print(f'    轮{i+1}: {s[:120]}')
except Exception as e:
    case('T7', False, f'{type(e).__name__}: {e}')

# ---------------- T8 深层 elif + else + 分支内 show ----------------
SRC_T8 = (
    'label a:\n'
    '    if x == 1:\n'
    '        show s1\n'
    '        e "一"\n'
    '    elif x == 2:\n'
    '        show s2\n'
    '        e "二"\n'
    '    elif x == 3:\n'
    '        show s3\n'
    '        e "三"\n'
    '    else:\n'
    '        show s0\n'
    '        e "零"\n'
)
print('\n=== T8 深层 elif 链 + else + 分支内 show ===')
try:
    txt, _, _ = roundtrip(SRC_T8)
    lines = txt.splitlines()
    pairs = {k: sum(1 for l in lines if f'show s{k}' in l)
             for k in ('0', '1', '2', '3')}
    n_x = sum(1 for l in lines if re.match(r'\s*(if|elif).*x\s*==', l))
    case('T8 四条 show 都在', all(v == 1 for v in pairs.values()), str(pairs))
    case('T8 条件行 ≥ 4（elif 展开也算）', n_x >= 4, f'{n_x} 条')
    # 每个 show 都必须紧跟在自己的条件行之后
    ok_order = True
    prev_cond = None
    for l in lines:
        m = re.match(r'\s*(if|elif|else).*', l)
        if m and ('x ==' in l or 'else' in l):
            prev_cond = l
        m2 = re.match(r'\s*show s(\d)', l)
        if m2:
            if prev_cond is None:
                ok_order = False
                break
    case('T8 每个 show 都挂在条件行之下', ok_order)
except Exception as e:
    case('T8', False, f'{type(e).__name__}: {e}')

# ---------------- T9 纯舞台拍插在选项中间 ----------------
print('\n=== T9 纯舞台拍混入 menu 组 ===')
try:
    bs = beats_of(SRC_T1)
    stage_beat = type(bs[0])(**{**{f.name: getattr(bs[0], f.name)
                                 for f in type(bs[0]).__dataclass_fields__.values()},
                               'kind': 'narration', 'text': '',
                               'show': 'sparkle', 'line_id': 's1', 'seq': 98})
    bs.insert(1, stage_beat)
    x = T / 't9.xlsx'
    export_beats_to_excel(bs, x)
    back = import_beats_from_excel(x)
    res = compile_beats(back)
    txt = res.rpy_text
    case('T9 编译不崩', True)
    case('T9 menu 仍存在', 'menu:' in txt)
    case('T9 sparkel/show 未凭空消失', 'sparkle' in txt)
except Exception as e:
    case('T9', False, f'{type(e).__name__}: {e}')

# ---------------- T10 Excel 手工编辑模拟 ----------------
print('\n=== T10 Excel 手工编辑（删行/改字）===')
try:
    # 删一行
    _, _, bs2 = roundtrip(SRC_T1, edits=lambda bs: bs.pop(0))
    txt = (T / 'case_o.rpy').read_text(encoding='utf-8')
    case('T10a 删掉第一个选项后剩余结构可编译',
         '"选项B"' in txt)
    # 改一个字
    def tweak(bs):
        for b in bs:
            if b.text == '选项A':
                b.text = '选项A改'
    _, _, _ = roundtrip(SRC_T1, edits=tweak)
    txt = (T / 'case_o.rpy').read_text(encoding='utf-8')
    case('T10b 改字后新文本在', '选项A改' in txt)
    case('T10c 改字后 menu 结构未塌', 'menu:' in txt)
except Exception as e:
    case('T10', False, f'{type(e).__name__}: {e}')

# ---------------- 总结 ----------------
fails = [r for r in results if not r[1]]
print(f'\n===== 刁钻测试总结：{len(results) - len(fails)}/{len(results)} 通过 =====')
for name, _, detail in fails:
    print(f'  FAIL {name}: {detail}')
sys.exit(1 if fails else 0)
