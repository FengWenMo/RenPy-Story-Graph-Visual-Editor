import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '.')
from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser

BODY = '    def part(expr):\n        return expr\n'


def run(head):
    tmp = Path(tempfile.mkdtemp())
    src = tmp / 'd.rpy'
    src.write_text(head + '\n' + BODY + '\nlabel start:\n    "hi"\n    return\n',
                   encoding='utf-8')
    p = RenPyParser()
    nodes = p.parse(src)
    res = ParseResultBuilder().build(nodes)
    print(f'--- {head}')
    print('  warnings:', [getattr(w, "message", w) for w in (p.warnings or [])])
    print('  file_blocks:', [(b.header, b.source_line) for b in res.file_blocks])
    print('  statements:', [(s.kind, getattr(s, "head", "")) for s in res.statements])
    print('  nodes:', [type(n).__name__ for n in nodes])


run('init python:')
run('init -1 python:')
run('init 1.5 python:')
