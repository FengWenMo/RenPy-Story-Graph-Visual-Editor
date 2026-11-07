import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '.')
from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_model import compose_code
from storygraph.story.transition_expr import beat_transitions

text = ('label start:\n'
        '    show ye happy\n'
        '    hide ye with dissolve\n'
        '    y "hi"\n')
td = tempfile.mkdtemp()
p = Path(td) / 'c.rpy'
p.write_text(text, encoding='utf-8')
pr = ParseResultBuilder().build(RenPyParser().parse(str(p)))
for b in beats_from_parse_result(pr):
    print('show=', b.show, 'hide=', b.hide, 'with_=', b.with_,
          'raw=', repr(b.raw), 'code=', repr(b.code),
          'compose=', repr(compose_code(b)),
          'transitions=', len(beat_transitions(b)))
