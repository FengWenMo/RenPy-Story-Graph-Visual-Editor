import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '.')
from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result

text = ('label start:\n'
        '    show ye happy\n'
        '    if True:\n'
        '        $ foo()\n'
        '    y "hi"\n')
td = tempfile.mkdtemp()
p = Path(td) / 'c.rpy'
p.write_text(text, encoding='utf-8')
pr = ParseResultBuilder().build(RenPyParser().parse(str(p)))
print([(s.kind, s.source) for s in pr.statements])
print([(b.seq, b.show, b.src_first, b.src_last, b.code, b.raw)
       for b in beats_from_parse_result(pr)])
