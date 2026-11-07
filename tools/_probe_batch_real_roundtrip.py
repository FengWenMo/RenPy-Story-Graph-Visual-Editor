# -*- coding: utf-8 -*-
"""Real-file round trip: copy a chapter, apply the rule, re-parse, compare beats."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8')

from storygraph.app.batch_insert import apply_plan, plan_batch
from storygraph.app.batch_insert import load_rule_file
from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result

ROOT = Path(r'D:\千连万理\sr_general')
RULE = Path(r'D:\千连万理\Renpy_Story_Graph_Editor\examples\batch\master-dissolve.json')


def beats(path):
    pr = ParseResultBuilder().build(RenPyParser().parse(str(path)))
    return beats_from_parse_result(pr)


def signature(path):
    out = []
    for b in beats(path):
        out.append((b.seq, b.label, b.show, b.hide, b.text, b.speaker,
                    b.src_first, b.src_last, str(b.code or '')))
    return out


for name in ('chapter1_2.rpy', 'chapter2_1.rpy'):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / 'proj'
        dst = root / 'game' / 'chapter' / name
        dst.parent.mkdir(parents=True)
        shutil.copy2(ROOT / 'game' / 'chapter' / name, dst)
        before = signature(dst)
        rules = load_rule_file(RULE)
        plan = plan_batch(root, rules, chapters=['game/chapter/*.rpy'])
        res = apply_plan(plan)
        after = signature(dst)
        print(f'{name}: beats {len(before)} -> {len(after)}, '
              f'inserted {res["inserted"]}, failed {res["failed"]}')
        # seq/src 行号本来就会随插行漂移；只比"这一拍是什么"（label/show/hide/text/speaker）
        diffs = [(i, a, b) for i, (a, b) in enumerate(zip(before, after))
                 if a[1:6] != b[1:6]]
        print(f'  semantic beat diffs: {len(diffs)}')
        for i, a, b in diffs[:3]:
            print('   ', i, a[:6], '=>', b[:6])
        codes = [b[8] for b in after if 'renpy.transition' in b[8]]
        print(f'  beats carrying cue: {len(codes)}')
