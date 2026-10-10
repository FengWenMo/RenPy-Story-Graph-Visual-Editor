"""现状实测：自定义语句（`swim_suit` 这一类）现在能不能被读写、能不能被"教"。

用户 2026-10-10 的问题：
> 你说支持自适应的解析……但是我并没有找到一个入口可以教这个工具学会我自定义的
> 项目模块……至于什么 swim_suit 一类的貌似无法做到呢……
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer   # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result        # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy           # noqa: E402

SRC = '''label ch01:
    swim_suit lyj "泳装"
    lyj "第一句"
    if love > 3:
        swim_suit lyj "泳装2" intensity=3
    menu:
        "选项一":
            swim_suit lyj "泳装3"
        "选项二":
            pass
    return
'''

with tempfile.TemporaryDirectory(prefix='cds_') as td:
    fp = Path(td) / 'game' / 'ch.rpy'
    fp.parent.mkdir(parents=True)
    fp.write_text(SRC, encoding='utf-8')

    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
    print('=== 解析出来的语句（kind / 原文）')
    for st in pr.statements[:12]:
        raw = getattr(st, 'source', '')
        print(f'  {str(getattr(st, "kind", "?")):14s} {str(raw)[:80]!r}')

    beats = beats_from_parse_result(pr)
    out = render_beats_to_rpy(beats)
    print('\n=== 往返（解析 → 拍 → 渲染）')
    print(f'  逐字节一致：{out == SRC}')
    if out != SRC:
        for a, b in zip(SRC.splitlines(), out.splitlines()):
            if a != b:
                print(f'    原: {a!r}\n    产: {b!r}')

    print('\n=== 构建报告怎么说（有没有点名"没建模/未声明"）')
    from storygraph.pipeline.build import build_report
    try:
        rep = build_report(fp.parent.parent)
        print('  ', (rep.get('summary') or '')[:400])
    except Exception as exc:                    # noqa: BLE001
        print(f'  build_report 调不通：{type(exc).__name__}: {exc}')

print('\n=== 后端"教它"的 API 有哪些')
from storygraph.story import statement_registry as SR      # noqa: E402
print('  ', [n for n in dir(SR) if not n.startswith('_')][:24])
print('\n=== 项目里的声明文件长什么样')
rules = ROOT / 'storygraph' / 'story' / 'statement_registry.py'
src = rules.read_text(encoding='utf-8')
import re                                                  # noqa: E402
for m in re.finditer(r'(rules\.json|declared|register_statement)', src):
    line = src[:m.start()].count('\n') + 1
    print(f'   {rules.name}:{line}')
    break
