"""实机演练（第二轮）：真机 5 章定稿走完整 S3，**所有写法都决定**，看能不能全落地。

    python tools/probe_s3_real_full.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.app.editor import EditorSession                # noqa: E402

TXT_DIR = Path(r'D:\千连万理\txt--renpy脚本转换工具')
REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
if not (TXT_DIR.is_dir() and REAL.is_dir()):
    print('真机素材不在本机，跳过')
    raise SystemExit(0)

tmp = Path(tempfile.mkdtemp(prefix='s3_full_'))
copy = tmp / 'sr_general'
# ⚠ 复制真机项目时**跳过 .git / saves / build**（一次把 C 盘塞满 53 GB）——
# 演练只要 game/ 里的脚本与资源表。
IGNORE = shutil.ignore_patterns('.git', 'saves', 'build', 'build_out', 'tmp',
                                '__pycache__', '*.rpyc')
shutil.copytree(REAL, copy, ignore=IGNORE)
files = sorted(TXT_DIR.glob('*-定稿.txt'))
s = EditorSession(project_root=copy)
s.load_assets()
before = sorted(p.name for p in (copy / 'game').rglob('*.rpy'))
print(f'导入前 game/ 下 .rpy：{len(before)} 个')

asked = []


def _ask(proposals):
    asked.extend(p.token for p in proposals)
    out = []
    for p in proposals:
        cands = p.suggestion.candidates
        if cands:
            out.append({'token': p.token, 'action': 'existing',
                        'target': cands[0].var})
        else:
            out.append({'token': p.token, 'action': 'new'})
    return out


res = s.import_plain_text(files, None, ask=_ask)
print(f'\n要决定的写法：{asked}')
d = {k: v for k, v in (res['decisions'] or {}).items() if k != 'messages'}
print('决定统计     ：', d)
print(f"\n落盘 {len(res['written'])} 个：")
for p in res['written']:
    print('   ', Path(p).name)
for p, why in (res['blocked'] or []):
    print('    🚫', Path(p).name, '|', why[:90])
if res['needs_decision']:
    print('  ⚠ 还有没定的：', [p.token for p in res['proposals']])
print('导入结果：', {k: v for k, v in (res.get('import_result') or {}).items()
                    if k in ('added_nodes', 'added_edges', 'nodes', 'edges')})
print(f"\n导入后 game/ 下 .rpy："
      f"{len(sorted(p.name for p in (copy / 'game').rglob('*.rpy')))} 个")

# —— 三个关键性质 ——
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer   # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result        # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy           # noqa: E402

bad = 0
for p in res['written']:
    text = Path(p).read_text(encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[Path(p)], graph=None)
    if render_beats_to_rpy(beats_from_parse_result(pr)) != text:
        bad += 1
        print('   ⚠ 往返不一致：', Path(p).name)
    if 'define' in text:
        print('   ⚠ 产物里有 define：', Path(p).name)
print(f'往返逐字节：{len(res["written"]) - bad}/{len(res["written"])} 一致')
