"""实测：声明 `swim_suit` 之后，**卡片编辑器 / 表格**里到底出现什么？

用户 2026-10-10：
> 卡片编辑器内有没有自定义模块的显示？如果没有的话，那我还是无法编辑服装啊？
> 现在是最多只有不同表情……想个办法？

    python tools/probe_card_custom_columns.py
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer   # noqa: E402
from storygraph.story import statement_registry as SR                  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result        # noqa: E402
from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS      # noqa: E402

SRC = '''label ch01:
    swim_suit lyj "泳装" pose=happy intensity=3
    lyj "第一句"
    lyj "第二句"
    return
'''

td = Path(tempfile.mkdtemp(prefix='cardcols_'))
game = td / 'game'
game.mkdir(parents=True)
fp = game / 'ch01.rpy'
fp.write_text(SRC, encoding='utf-8')


def snapshot(tag):
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
    reg = SR.registry_for_parse_result(pr)
    beats = beats_from_parse_result(pr)
    SR.annotate_spec_statements(pr.statements, reg)
    beats = beats_from_parse_result(pr)
    dyn = SR.dynamic_spec_columns(beats, reg)
    print(f'\n=== {tag}')
    print('  动态列（会追加到表格/卡片）：', dyn)
    print('  to_row 返回类型：', type(beats[0].to_row()).__name__)
    for i, b in enumerate(beats):
        print(f'  拍{i}: spec_name={getattr(b, "spec_name", "")!r} '
              f'opaque={str(getattr(b, "opaque", ""))[:40]!r}')
        print(f'        Speaker={getattr(b, "speaker", "")!r} '
              f'Text={getattr(b, "text", "")!r}')
        for col in (dyn or []):
            print(f'        {col} = {getattr(b, "spec_args", {})!r}')
            break
    print('  卡片基础字段 PRIMARY ：', PRIMARY_FIELDS)
    print('  卡片高级字段 ADVANCED：', ADVANCED_FIELDS)
    return beats, reg


print('项目：', td)
print('文件：', SRC.strip().replace(chr(10), ' | '))
snapshot('① 未声明')

res = SR.declare_statement(
    td, 'swim_suit', label='泳装切换',
    columns=[{'name': 'pose', 'label': '姿势',
              'choices': ['happy', 'calm', 'sad']},
             {'name': 'intensity', 'label': '强度', 'choices': ['1', '2', '3']}],
    docs='把角色切到泳装（pose 选表情）')
print('\n声明：', res['action'], res['ok'], '→', res['path'])
SR.clear_cache()
beats, reg = snapshot('② 声明之后')

print('\n=== 卡片编辑器的"语句参数"那一组认不认它')
from storygraph.app import beats as _beats                              # noqa: E402
stmts = SR.StatementRegistry.for_project(str(td))
b = beats[0]
cols, values = _beats.spec_arg_fields_for(b, stmts)
print('  卡片会显示的参数控件：')
for c in cols:
    print('   ·', c)
print('  当前值：', values)
choices = _beats.spec_arg_choices_for(b, stmts)
print('  候选值：', choices)

print('\n=== 从卡片/表格改这个参数，产物会怎样')
headers = [c['header'] for c in SR.dynamic_spec_columns(beats, reg)]
print('  动态列表头（`语句名·参数名`）：', headers)
print('  改前 opaque ：', repr(getattr(b, 'opaque', '')))
print('  摊成列的值   ：', dict(zip(headers, b.spec_arg_cells(headers))))
from storygraph.story.beat_model import fold_spec_arg_columns            # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy             # noqa: E402

# ⚠ 动态列必须写**整列名**（`语句名·参数名`），这是我第一版搞错的地方
cleaned = fold_spec_arg_columns(b, {'swim_suit·intensity': '9'})
print('  fold {swim_suit·intensity: 9} →', cleaned)
print('  改后 opaque ：', repr(getattr(b, 'opaque', '')))
print('  渲染出来的行：',
      [ln.strip() for ln in render_beats_to_rpy([b]).splitlines()
       if 'swim_suit' in ln])
