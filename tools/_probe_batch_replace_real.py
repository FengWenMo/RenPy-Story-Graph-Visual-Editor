# -*- coding: utf-8 -*-
"""Real-file replace round trip: copy chapter1_2, retime 0.1 -> 0.2, compare."""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8')

from storygraph.app.batch_edit import (apply_plan, load_rule_file,
                                       plan_batch)
from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result

ROOT = Path(r'D:\千连万理\sr_general')
RULE = Path(r'D:\千连万理\Renpy_Story_Graph_Editor\examples\batch\retime-transition.json')


def beats(path):
    pr = ParseResultBuilder().build(RenPyParser().parse(str(path)))
    return beats_from_parse_result(pr)


with tempfile.TemporaryDirectory() as td:
    root = Path(td) / 'proj'
    dst = root / 'game' / 'chapter' / 'chapter1_2.rpy'
    dst.parent.mkdir(parents=True)
    shutil.copy2(ROOT / 'game' / 'chapter' / 'chapter1_2.rpy', dst)
    before = [(b.seq, b.show, b.hide, b.text, b.speaker) for b in beats(dst)]
    rules = load_rule_file(RULE)
    plan = plan_batch(root, rules, chapters=['game/chapter/*.rpy'])
    print('pending', plan.pending, 'edits',
          len(plan.files[0].edits), 'skipped', len(plan.files[0].skipped))
    res = apply_plan(plan)
    print('apply', res)
    after = [(b.seq, b.show, b.hide, b.text, b.speaker) for b in beats(dst)]
    print('beats', len(before), '->', len(after),
          'semantic diffs', sum(1 for a, b in zip(before, after) if a != b))
    text = dst.read_text(encoding='utf-8')
    print('0.1 count', text.count('Dissolve(0.1)'),
          '0.2 count', text.count('Dissolve(0.2)'))
