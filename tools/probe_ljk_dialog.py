"""诊断：`LJK` + 两个只差大小写的变量 时，弹窗拿到几行（临时探针）。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.char_decisions import (                    # noqa: E402
    propose_one, proposals, suggestions)
from storygraph.authoring.txt_import import CharacterMap, convert_text  # noqa: E402

ROWS = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
        'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'}}

cm = CharacterMap(ROWS)
conv = convert_text('LJK"我是谁"', characters=cm)
print('lines    :', [(ln.kind, ln.speaker_token, ln.speaker_var,
                      bool(ln.ambiguity)) for ln in conv.lines])
print('unmapped :', dict(conv.unmapped))
print('ambiguous:', dict(conv.ambiguous))
print('suggestions:')
for s in suggestions(conv, cm, ROWS):
    print('   ', s.token, [(c.var, c.evidence) for c in s.candidates], '|', s.note)
print('proposals:')
for p in proposals(conv, cm, ROWS):
    print('   ', p.token, p.action, p.executable, '|', (p.note or '')[:80])
print('propose_one 直接:',
      propose_one(ROWS, suggestions(conv, cm, ROWS)[0]).action
      if suggestions(conv, cm, ROWS) else '无建议')
