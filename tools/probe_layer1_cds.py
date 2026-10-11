"""第一层验证（合成用例）：引擎注册表**能看见 `register_statement` 注册的东西**。

为什么不能在真机项目上验：`sr_general` 的注册表**本来就是空的**（0 条，
与 `scan_project_cds` 的结果一致 —— 它没用 CDS）。所以这里造一个最小项目，
注册两类语句，看第一层能不能如实说出名字与形状。

    python tools/probe_layer1_cds.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.core import engine_facts as EF                        # noqa: E402
from storygraph.pipeline.engine_collect import collect                 # noqa: E402

td = Path(tempfile.mkdtemp(prefix='l1cds_'))
game = td / 'game'
(game / 'images').mkdir(parents=True)
# 最小工程：Ren'Py 需要 gui.rpy / options.rpy 之类吗？lint 在最小工程上能跑
(game / 'cds.rpy').write_text('''init python:

    # ⚠ 真机实验结论（2026-10-11）：**必须给 `parse`**！
    #   `register_statement("weather")`（不给 parse）→ 解析时 `expected statement.`
    #   （注册进了 registry 但解析器那条路是 None，直接崩）
    renpy.register_statement("weather", parse=lambda l: l.eol())

    renpy.register_statement("outfit", parse=lambda l: l.eol())


label start:
    weather
    outfit
    return
''', encoding='utf-8')

print('最小项目：', td)
res = collect(td, timeout=600, mode='copy')
print('采集 ok =', res['ok'], '|', res.get('reason') or '')
facts = res.get('facts') or {}
print('事实可用 =', EF.is_collectable(facts))
print('collection =', (facts.get('collection') or {}))

reg = EF.registered_statements(facts)
print(f'\n=== 第一层：引擎注册表 {len(reg)} 条')
for name in sorted(reg):
    print(f'    {name:12s} {reg[name]}')
names = EF.statement_names(facts)
print('\n=== 用它回答"认不认识"')
for head in ('weather', 'outfit', 'sg_definitely_unknown_xyz'):
    hit = head in names
    print(f'    {head:26s} 认识 = {hit}'
          + (f'   形状 = {EF.statement_shape(facts, head)}' if hit else ''))

print('\n=== 判据自检（第一层必须能区分这三件事）')
assert 'weather' in names, '注册过的 weather 必须在名单里'
assert 'outfit' in names, '注册过的 outfit 必须在名单里'
assert 'sg_definitely_unknown_xyz' not in names, '没注册的不许在名单里'
shape = EF.statement_shape(facts, 'outfit')
assert shape.get('has_parse') is True, shape
assert 'pause' in names, (
    '⚠ 引擎**内置**语句也该在名单里（真机实测 23 条里就有 pause / play music）'
    '—— 这一层比我们自己的词表更权威')
print('    ✅ 全部通过')

shutil.rmtree(td, ignore_errors=True)
