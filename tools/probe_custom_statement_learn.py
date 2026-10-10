"""实测：`swim_suit` 未声明 vs 声明后的差别（用户要的"教它"到底改变了什么）。

    python tools/probe_custom_statement_learn.py
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer   # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result        # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy           # noqa: E402
from storygraph.story import statement_registry as SR                  # noqa: E402

SRC = '''label ch01:
    swim_suit lyj "泳装" intensity=3
    lyj "第一句"
    return
'''

td = Path(tempfile.mkdtemp(prefix='learn_'))
game = td / 'game'
game.mkdir(parents=True)
fp = game / 'ch.rpy'
fp.write_text(SRC, encoding='utf-8')


def roundtrip(path):
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    beats = beats_from_parse_result(pr)
    return render_beats_to_rpy(beats), beats, pr


def show(tag):
    out, beats, pr = roundtrip(fp)
    # ⚠ 必须**带上 registry**（我第一次漏传 → capability_of 只能判 T3，
    # 于是"声明没生效"是假象）。registry 要从项目根装配。
    reg = SR.registry_for_parse_result(pr)
    body = [ln for ln in out.splitlines()
            if ln.strip() and not ln.startswith('#')]
    print(f'\n=== {tag}')
    print('  产物正文：', body)
    cov = SR.coverage_of_beats(beats, registry=reg)
    print('  覆盖字段：', {f: getattr(cov, f) for f in dir(cov)
                          if not f.startswith('_')
                          and not callable(getattr(cov, f))})
    print('  参数列：', [c.get('name') for c in
                        SR.arg_columns(reg, 'swim_suit')])
    print('  候选值(intensity)：', SR.spec_arg_choices(reg, 'swim_suit',
                                                     'intensity'))


show('① 未声明')
res = SR.declare_statement(td, 'swim_suit', label='泳装切换',
                           columns=[{'name': 'intensity', 'label': '强度',
                                     'choices': ['1', '2', '3']}],
                           docs='切到泳装立绘')
print('\n=== declare_statement 结果')
print(' ', {k: v for k, v in res.items() if k != 'entry'})
print('  rules.json：')
print(json.dumps(json.loads((td / 'storygraph.rules.json').read_text(
    encoding='utf-8')), ensure_ascii=False, indent=2))
show('② 声明之后')

# 再声明一次（同名 = 更新，不许变成两条）
res2 = SR.declare_statement(td, 'swim_suit', block=False)
print('\n=== 再声明一次（同名）')
print(' ', res2['action'], '| statements 条数：',
      len(json.loads((td / 'storygraph.rules.json').read_text(
          encoding='utf-8'))['statements']))
