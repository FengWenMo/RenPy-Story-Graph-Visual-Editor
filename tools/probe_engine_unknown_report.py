"""真机验证：体检报告【⑩ 段】—— 剧本里"引擎不认的立绘属性"汇总。

路线图 §8.9 的"报出来"待做项。跑**副本**，不碰真项目。

    python tools/probe_engine_unknown_report.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
if not REAL.is_dir():
    print('真机项目不在本机，跳过')
    raise SystemExit(0)

from storygraph.app.beats import (beat_unknown_attrs,                # noqa: E402
                                  unknown_attrs_by_tag)
from storygraph.app.asset_report import format_asset_health          # noqa: E402
from storygraph.core import engine_facts as EF                       # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result      # noqa: E402

work = Path(tempfile.mkdtemp(prefix='unknownrep_')) / 'sr_general'
shutil.copytree(REAL, work,
                ignore=shutil.ignore_patterns('.git', 'saves', 'build',
                                              '*.rpyc', 'log.txt', '*.backup',
                                              'cache'))
facts = EF.loads((work / 'storygraph.engine_facts.json').read_text(encoding='utf-8'))
print('事实可用：', EF.is_collectable(facts))

def beats_of(path):
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    return beats_from_parse_result(pr)

# 逐章汇总（真机那些 rpy 在 game/chapter/）
files = sorted((work / 'game' / 'chapter').glob('*.rpy'))
files = [f for f in files if not f.name.endswith('.generated.rpy')]
all_beats = []
for f in files:
    try:
        all_beats.extend(beats_of(f))
    except Exception as exc:                       # noqa: BLE001
        print(f'  （{f.name} 解析失败：{type(exc).__name__}）')
print(f'扫了 {len(files)} 章、{len(all_beats)} 拍')

per_beat = [beat_unknown_attrs(b, facts) for b in all_beats]
print('逐拍命中：', sum(1 for x in per_beat if x), '拍有提示')
by_tag = unknown_attrs_by_tag(all_beats, facts)
print()
print('=== 项目级汇总（这就是【⑩ 段】的内容）')
for tag, words in sorted(by_tag.items(), key=lambda kv: (-len(kv[1]), kv[0])):
    print(f'  {tag:8s} {len(words):2d} 个：{"、".join(words)}')

print()
print('=== 渲染出来的那一节')
report = {'ok': True, 'problems': 0,
          'counts': {'images': 1657, 'music': 0, 'sounds': 0, 'voices': 0},
          'missing': {}, 'unregistered': {}, 'outside_game': {},
          'duplicate_ids': [], 'duplicate_paths': [], 'no_path': [],
          'placeholders': [], 'total_rows': 1657,
          'engine_unknown': by_tag}
text = format_asset_health(report)
start = text.find('【⑩')
print(text[start:start + 900] if start >= 0 else '（没有【⑩ 段】—— 数据为空？）')

print()
print('=== 不带事实时（不该出现这一段）')
text2 = format_asset_health({**report, 'engine_unknown': {}})
print('  出现【⑩ =', '【⑩' in text2)

# ⚠ 跑完删掉副本（2026-10-11：漏了清理，TEMP 里堆了 46.9GB 的 sr_general）
shutil.rmtree(work.parent, ignore_errors=True)
