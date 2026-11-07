import sys
from pathlib import Path

sys.path.insert(0, '.')
from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.transition_expr import beat_transitions

ROOT = Path(r'D:\千连万理\sr_general')
shown = 0
for path in sorted((ROOT / 'game' / 'chapter').glob('*.rpy')):
    pr = ParseResultBuilder().build(RenPyParser().parse(str(path)))
    for b in beats_from_parse_result(pr):
        hide = str(getattr(b, 'hide', '') or '')
        if '@' in hide and not beat_transitions(b):
            print(path.name, 'seq', b.seq, 'show=', repr(b.show),
                  'hide=', repr(hide), 'text=', repr(str(b.text or '')[:20]))
            shown += 1
            if shown >= 15:
                raise SystemExit
