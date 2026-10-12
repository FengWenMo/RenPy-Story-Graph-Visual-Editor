"""实机演练（第三轮）：删掉副本里已存在的章节 → 纯文字 5 章应该真的落地。

    python tools/probe_s3_real_landing.py
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

tmp = Path(tempfile.mkdtemp(prefix='s3_land_'))
copy = tmp / 'sr_general'
shutil.copytree(REAL, copy,
                ignore=shutil.ignore_patterns('.git', 'saves', 'build',
                                              'build_out', 'tmp',
                                              '__pycache__', '*.rpyc'))
game = copy / 'game'
# 把"已存在的章节"挪走（模拟"我还没把这些章放进项目"）
# ⚠ 真机的章节在 `game/chapter/` 子目录里（不是 game/ 根下），要递归找。
park = tmp / 'parked'
park.mkdir()
moved = []
for f in sorted(game.rglob('chapter*.rpy')):
    if f.name.startswith('chapter_') or f.name.endswith('.blocked.rpy'):
        continue
    moved.append(f.name)
    f.rename(park / f.name)
print(f'挪走已存在章节 {len(moved)} 个：{moved}')
print(f'剩下的 .rpy：{[p.name for p in sorted(game.glob("*.rpy"))][:8]} …')

files = sorted(TXT_DIR.glob('*-定稿.txt'))
s = EditorSession(project_root=copy)
s.load_assets()


def _ask(proposals):
    out = []
    for p in proposals:
        cands = p.suggestion.candidates
        out.append({'token': p.token,
                    'action': 'existing' if cands else 'new',
                    **({'target': cands[0].var} if cands else {})})
    return out


res = s.import_plain_text(files, None, ask=_ask)
print(f"\n落盘 {len(res['written'])} 个：")
for p in res['written']:
    print('   ', Path(p).name)
for p, why in (res['blocked'] or []):
    print('    🚫', Path(p).name, '|', why[:80])
for p, why in (res['skipped'] or []):
    print('    ⚠', Path(p).name, '|', why[:80])
print('导入结果：', {k: v for k, v in (res.get('import_result') or {}).items()
                    if k in ('added_nodes', 'added_edges', 'nodes', 'edges')})

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer   # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result        # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy           # noqa: E402

bad, defines = [], []
for p in res['written']:
    text = Path(p).read_text(encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[Path(p)], graph=None)
    if render_beats_to_rpy(beats_from_parse_result(pr)) != text:
        bad.append(Path(p).name)
    if 'define' in text:
        defines.append(Path(p).name)
print(f"往返逐字节：{len(res['written']) - len(bad)}/{len(res['written'])}"
      + (f'  不一致 {bad}' if bad else ''))
print(f"产物里有 define 的：{defines or '无'}")

# 未映射告警是否都消掉了
import re                                                              # noqa: E402
for p in res['written']:
    text = Path(p).read_text(encoding='utf-8')
    loose = re.findall(r'^\s{4}([A-Za-z_][A-Za-z0-9_]*)\s+"', text, re.M)
    from storygraph.authoring.txt_import import CharacterMap
    cm = CharacterMap(s.character_rows())
    unknown = [t for t in dict.fromkeys(loose) if cm.lookup(t)[0] in (None, '')]
    if unknown:
        print('   ⚠ 产物里还有认不出的说话人：', Path(p).name, unknown)
print('（没有上一条 = 所有说话人都能对上角色）')

# ⚠ 跑完删掉副本（2026-10-11：漏了清理，TEMP 里堆了 46.9GB 的 sr_general）
shutil.rmtree(tmp, ignore_errors=True)
