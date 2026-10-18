import sys
from pathlib import Path

sys.path.insert(0, '.')
from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy

src = Path(r'D:\software\renpy-8.4.1-sdk\the_question\game\script.rpy')
nodes = RenPyParser().parse(src)
result = ParseResultBuilder().build(nodes)
beats = beats_from_parse_result(result, label='script')
print('beats', len(beats))
for b in beats:
    blob = ' '.join(str(x) for x in (
        b.text, b.speaker, getattr(b, 'extras', ''), getattr(b, 'opaque', ''),
        getattr(b, 'menu_name', ''), getattr(b, 'menu_args', '')))
    if 'visual' in blob or 'menu' in b.kind:
        print(b.line_id, b.kind, '| text=', repr(b.text)[:80],
              '| extras=', repr(getattr(b, 'extras', ''))[:120],
              '| src=', b.src_first, '-', b.src_last)
full = render_beats_to_rpy(beats, label_name=None)
print('--- in render:', 'visual novel' in full)
print([ln for ln in full.splitlines() if 'visual' in ln])
