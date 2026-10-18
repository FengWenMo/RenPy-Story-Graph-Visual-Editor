import sys
from pathlib import Path

sys.path.insert(0, '.')
from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result

src = Path(r'D:\software\renpy-8.4.1-sdk\the_question\game\script.rpy')
result = ParseResultBuilder().build(RenPyParser().parse(src))
beats = beats_from_parse_result(result, label='script')
b = next(x for x in beats if x.line_id == 'rightaway.0030')
for k, v in sorted(vars(b).items()):
    if v not in ('', None, [], {}, 0):
        print(f'{k} = {v!r}'[:300])
print('---- src 97-100 ----')
lines = src.read_text(encoding='utf-8-sig').splitlines()
for i in range(95, 102):
    print(i + 1, repr(lines[i]))
