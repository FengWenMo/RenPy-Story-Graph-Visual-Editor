"""诊断：`LJK` 在"表里只有 ljk"时的整条链（临时探针）。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.char_decisions import (                    # noqa: E402
    plan_new_character, proposals, suggestions)
from storygraph.authoring.txt_import import CharacterMap, convert_text  # noqa: E402

rows = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'}}
cm = CharacterMap(rows)
conv = convert_text('LJK"我是谁"', characters=cm)
print('lines     :', [(ln.kind, ln.speaker_token, ln.speaker_var,
                       bool(ln.ambiguity)) for ln in conv.lines])
print('unmapped  :', dict(conv.unmapped))
print('ambiguous :', dict(conv.ambiguous))
for s in suggestions(conv, cm, rows):
    print('  sug', s.token, [(c.var, c.evidence) for c in s.candidates], s.note)
print('proposals :')
for p in proposals(conv, cm, rows):
    print('   ', p.token, p.action, p.executable, '|', p.note[:70])
print('new(char) 默认  :', plan_new_character(rows, 'LJK').ok,
      plan_new_character(rows, 'LJK').reason[:60])
print('new(char) 改名  :', plan_new_character(rows, 'LJK', var='ljk2').ok,
      plan_new_character(rows, 'LJK', var='ljk2').reason[:60])
