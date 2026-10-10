"""`discover_undeclared` 实测：能不能从项目脚本里找出未声明的写法。

    python tools/probe_discover_undeclared.py
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story import statement_registry as SR      # noqa: E402

SRC = '''label ch01:
    swim_suit lyj "泳装" intensity=3
    lyj "第一句"
    mycue "闪电" intensity=2:
        lyj "块里的台词"
    if love > 3:
        swim_suit lyj "泳装2"
    show lyj happy
    return
'''

td = Path(tempfile.mkdtemp(prefix='disc_'))
game = td / 'game'
game.mkdir(parents=True)
(game / 'ch.rpy').write_text(SRC, encoding='utf-8')
# 让 `discover_scripts` 认得出项目根（有 game/ 就够）
print('项目根：', td)

SR.clear_cache()
found = SR.discover_undeclared(td)
print('\n发现的未声明写法：')
for rec in found:
    print(f"  {rec['head']:12s} x{rec['count']:<3d} 带块={rec['block']}")
    for where, text in rec['samples'][:2]:
        print(f'       {Path(str(where)).name}: {text}')

print('\n教它一条（swim_suit）之后：')
res = SR.declare_statement(td, 'swim_suit', label='泳装切换',
                           columns=[{'name': 'intensity', 'label': '强度',
                                     'choices': ['1', '2', '3']}])
print('  ', res['action'], res['ok'])
SR.clear_cache()
print('  ', [r['head'] for r in SR.discover_undeclared(td)])

print('\n教带块的那条（mycue，block=True）：')
SR.declare_statement(td, 'mycue', block=True, columns=[{'name': 'intensity'}])
SR.clear_cache()
print('  ', [r['head'] for r in SR.discover_undeclared(td)])

print('\n体检结论（format_registry_report）：')
reg = SR.registry_for_parse_result(
    __import__('storygraph.parsing.renpy_parser', fromlist=['x'])
    .IncrementalRenPyAnalyzer().analyze(files=[game / 'ch.rpy'], graph=None)[0])
print(SR.format_registry_report(reg))
