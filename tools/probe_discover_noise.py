"""实测"发现"里为什么会冒出 `with` / `window` / `pause` / `I`（用户截图里那三行）。

用户 2026-10-10 截图报：
> 而且 with，window,pause 一类的不都是默认的关键字吗？emm……你确定你这个工具的
> 通用性足够强？？？

    python tools/probe_discover_noise.py [真机项目路径]
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.core.vocabulary import (RENPY_KNOWN_HEADS,               # noqa: E402
                                        statement_head_token)
from storygraph.story import statement_registry as SR                    # noqa: E402
from storygraph.story.beat_model import LEAF_ARG_HEADS                   # noqa: E402

print('工具**认识**的语句头（RENPY_KNOWN_HEADS）里有：')
for w in ('with', 'window', 'pause', 'play', 'show', 'scene', 'hide'):
    print(f'   {w:8s} ∈ 认识 = {w in RENPY_KNOWN_HEADS}')
print('工具**自己声明过参数**的叶语句（LEAF_ARG_HEADS）：', LEAF_ARG_HEADS)

SRC = '''label ch01:
    scene bg room
    show lyj happy
    with dissolve
    lyj "台词一"
    pause 1.0
    window hide
    I oh "3"
    swim_suit lyj "泳装" mood=happy
    return
'''

td = Path(tempfile.mkdtemp(prefix='noise_'))
game = td / 'game'
game.mkdir(parents=True)
(game / 'ch.rpy').write_text(SRC, encoding='utf-8')
print('\n样例脚本：')
for ln in SRC.splitlines():
    print('   ' + ln)

SR.clear_cache()
print('\n=== 现在的"发现"（用户截图里那一列）')
for rec in SR.discover_undeclared(td):
    head = rec['head']
    known = head in RENPY_KNOWN_HEADS
    leaf = head in LEAF_ARG_HEADS
    print(f"  {head:10s} x{rec['count']:<3d} 工具认识={known!s:5s} "
          f"工具自己声明过={leaf!s:5s} ← 该不该出现在这里？"
          f"{'  ✗ 不该（噪声）' if (known or leaf) else '  ✓ 该'}")

print('\n=== 逐条看它们是怎么被算成"未建模"的')
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer     # noqa: E402
pr, _ = IncrementalRenPyAnalyzer().analyze(files=[game / 'ch.rpy'], graph=None)
for st in pr.statements:
    payload = getattr(st, 'payload', None) or {}
    text = str(payload.get('text') or '')
    head = statement_head_token(text)
    if head in ('with', 'window', 'pause', 'I', 'swim_suit'):
        print(f"  {head:10s} kind={getattr(st, 'kind', '')!r:22s} "
              f"raw_kind={payload.get('raw_kind')!r:22s} text={text[:40]!r}")

print('\n=== `I oh "3"` 那一行到底是什么')
for st in pr.statements:
    payload = getattr(st, 'payload', None) or {}
    text = str(payload.get('text') or '')
    if text.startswith('I ') or text.startswith('I\t'):
        print('  ', getattr(st, 'kind', ''), '|', repr(text))
        print('   payload 键：', sorted(payload))
        print('   说话人 / 台词：', payload.get('who'), '/',
              payload.get('what'), '| attributes:',
              payload.get('attributes') or payload.get('what_attributes'))
