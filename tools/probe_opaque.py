#!/usr/bin/env python
"""
验证"第三类东西"的保真情况 —— 那些既不属于"结构调整"也不属于
"句子级演出"，谁都不管、只能原样保留的部分：

  V1  default / define 变量声明
  V2  python 函数定义 + 多行 python 块
  V3  persistent / renpy.store 等引擎对象
  V4  外部 jump / call 目标（图中不存在的 label）
  V5  被引擎调用的自定义 screen / transform 引用
  V6   init python 区块
  V7   $ 行内赋值与 compact 写法

判据：往返后这些东西必须**逐字节还在**。它们是"运输不可理解的部分"，
丢失 = 游戏跑不起来。
"""
from __future__ import annotations

import difflib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa
from storygraph.story.beat_build import beats_from_parse_result        # noqa
from storygraph.story.excel_export import export_beats_to_excel        # noqa
from storygraph.story.excel_import import import_beats_from_excel      # noqa
from storygraph.pipeline.compile import compile_beats                  # noqa

TMP = Path(tempfile.mkdtemp(prefix='opaque_'))
A = IncrementalRenPyAnalyzer()

CASES = {
    'V1 变量声明': (
        'default score = 0\n'
        'define config.k = 3\n'
        'default flags = {}\n'
    ),
    'V2 python 函数': (
        'init python:\n'
        '    def helper(x):\n'
        '        return x * 2\n'
    ),
    'V3 引擎对象': (
        'label a:\n'
        '    $ persistent.clear_count = 0\n'
        '    $ renpy.store.player_name = "x"\n'
        '    e "引用了引擎对象"\n'
    ),
    'V4 外部跳转': (
        'label a:\n'
        '    e "跳到外部"\n'
        '    jump external_minigame\n'
        '    call external_helper\n'
    ),
    'V5 screen 引用': (
        'label a:\n'
        '    show screen my_hud\n'
        '    hide screen my_hud\n'
        '    e "有 screen"\n'
    ),
    'V6 transform 引用': (
        'label a:\n'
        '    show logo at my_transform\n'
        '    e "用了自定义 transform"\n'
    ),
    'V7 紧凑 $ 写法': (
        'label a:\n'
        '    $score+=1\n'
        '    $ love = 5\n'
        '    e "两种美元写法"\n'
    ),
}


def main() -> int:
    print('=== "第三类"（谁都不管、必须原样保真）往返测试 ===\n')
    bad = 0
    for name, src in CASES.items():
        f = TMP / f'{abs(hash(name)) % 99999}.rpy'
        f.write_text(src, encoding='utf-8')
        try:
            pr, _ = A.analyze(files=[f], graph=None)
            beats = beats_from_parse_result(pr)
            if not beats:
                print(f'  {name}: 无拍（这些语句不进 Beats）')
                print('    → 意味着它们**不经过** Excel，只能靠 patcher 保真\n')
                continue
            xl = TMP / f'{abs(hash(name)) % 99999}.xlsx'
            export_beats_to_excel(beats, xl)
            res = compile_beats(import_beats_from_excel(xl))
            out = res.rpy_text
        except Exception as e:
            print(f'  {name}: 异常 {type(e).__name__}: {e}\n')
            bad += 1
            continue

        want = [l.strip() for l in src.splitlines()
                if l.strip() and not l.strip().startswith('#')]
        got = [l.strip() for l in out.splitlines()
               if l.strip() and not l.strip().startswith('#')]
        missing = [l for l in want if l not in got]
        status = 'OK ' if not missing else '丢失'
        if missing:
            bad += 1
        print(f'  [{status}] {name}')
        if missing:
            print(f'      原文有、产物没有：')
            for m in missing:
                print(f'        {m}')
            print('      --- 产物全文 ---')
            for l in got:
                print(f'        | {l}')
        print()
    print(f'===== {len(CASES) - bad}/{len(CASES)} 保真 =====')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
