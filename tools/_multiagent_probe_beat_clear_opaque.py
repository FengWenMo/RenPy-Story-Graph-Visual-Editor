r"""
tools/_multiagent_probe_beat_clear_opaque.py
============================================
分级删除（`storygraph/app/beat_clear.py`）与「台词+原样块」合并拍的对撞。

背景链（2026-10-26 审计）：
  ① `renpy_parser._RE_MENU_NAME = r'\.?[^\W\d]\w*'` 不认点号 menu 名
     （`menu main.route:`）→ 整块掉进未建模通道，**折叠进后面那句台词的
     `opaque` 字段**（探针 `…_menu_brief_evidence.py` E1）。
  ② `beat_clear.why_not` 只要拍上有 `opaque` 就拦"清空"，理由是
     "原样块——清掉它会把块体和头拆开"。但模块 docstring 的三档表格
     写着 `text` 档"**代码块、舞台、音频全留**"、`text_stage` 档
     "**代码块（含原样块）**全留" —— 清空根本不碰 opaque。
  ③ 于是这种合并拍上"只清空文字"被拒、只能整拍删，而整拍删会把
     绑在上面的整块菜单**一起带走**（删一张台词卡 = 删一整块菜单）。

判据写的是**目标行为**（docstring 表格的承诺）：
  · 台词+原样块的拍，`text` 档应能清空、且 opaque 逐字节留下；
  · 纯原样块拍（没有台词可清）本来就该拒绝（"没有可清的内容"）。

跑法：

    /d/Python/python tools/_multiagent_probe_beat_clear_opaque.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app.beat_clear import plan_clear  # noqa: E402
from storygraph.story.beat_model import Beat  # noqa: E402

PASS, FAIL = '[PASS]', '[FAIL]'
results: list = []


def check(name, expected, actual, why=''):
    ok = expected == actual
    results.append((ok, name))
    print(f'{PASS if ok else FAIL} {name}')
    if why:
        print(f'       依据: {why}')
    if not ok:
        print(f'       期望: {expected!r}')
        print(f'       实际: {actual!r}')


def make_beat(**kw):
    b = Beat(seq=1, label='main')
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def main():
    tmp = Path(tempfile.mkdtemp(prefix='probe_beat_clear_'))
    try:
        # 台词 + 原样块（点号 menu 触发的那种合并拍）
        merged = make_beat(
            kind='say', text='菜单之后',
            opaque='menu main.route:\n    "点号选":\n        jump ch02')

        plan = plan_clear(merged, 'text')
        check('C1 台词+原样块：text 档应能清空（文字清掉、原样块逐字留下）',
              True, bool(plan.ok),
              'beat_clear 模块 docstring 三档表格：text 档"代码块（含原样块）'
              '全留"；实现却在 why_not 里见 opaque 就拦')

        if plan.ok:
            from storygraph.app.beat_clear import apply_clear
            apply_clear(merged, 'text')
            check('C1b 清空后：文字没了、原样块逐字节还在',
                  ('', 'menu main.route:\n    "点号选":\n        jump ch02'),
                  (merged.text, merged.opaque),
                  '"没多删一个字"的自校验（keep_before == keep_after）')
        else:
            check('C1b 清空后：文字没了、原样块逐字节还在',
                  ('', 'menu main.route:\n    "点号选":\n        jump ch02'),
                  (merged.text, merged.opaque),
                  '（C1 已红 —— 根本没清成）')

        # 纯原样块拍（没有台词）：本来就该拒绝，防"清完什么都不剩"
        pure_opaque = make_beat(kind='opaque',
                                opaque='menu main.route:\n    "x":\n        pass')
        plan2 = plan_clear(pure_opaque, 'text')
        check('C2 纯原样块拍：照旧拒绝（没有可清的台词）',
              False, bool(plan2.ok),
              '目标行为不变 —— 修正 why_not 时别把这一条放开了')

        # 常规台词拍（无 opaque）：照常能清（台词清掉、舞台留下）
        plain = make_beat(kind='say', text='普通台词', scene='bg room')
        plan3 = plan_clear(plain, 'text')
        check('C3 带舞台的普通台词拍：text 档照常（台词清、舞台留）',
              True, bool(plan3.ok))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [n for ok, n in results if not ok]
    print(f'\n共 {len(results)} 条：{sum(1 for ok, _ in results if ok)} 绿 / '
          f'{len(bad)} 红')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
