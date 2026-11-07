# -*- coding: utf-8 -*-
"""Smoke probe for the generic batch inserter (ASCII fixture)."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8')

from storygraph.app.batch_insert import (apply_plan, load_rule_file,
                                         plan_batch)

CHAPTER = '''\
label start:
    scene bg room
    show cg cg01
    "cg line"

    show ye happy
    y "hello"

    show lan sad
    $ renpy.transition(Dissolve(0.1), layer="master")
    l "already"

    hide ye
    show ye angry
    y "angry"

    show ye shy:
        xalign 0.5
        ease 0.2 alpha 1.0
    y "atl"
'''

RULE = {
    'schema': 'storygraph.batch/1',
    'rules': [{
        'id': 'cue',
        'when': {'show': 'sprite', 'transition': 'none'},
        'insert': {
            'placement': 'after_last_show',
            'text': '$ renpy.transition(Dissolve(0.1), layer="master")',
        },
    }],
}

with tempfile.TemporaryDirectory() as td:
    root = Path(td) / 'proj'
    (root / 'game' / 'chapter').mkdir(parents=True)
    src = root / 'game' / 'chapter' / 'ch01.rpy'
    src.write_bytes(CHAPTER.encode('utf-8'))
    rule = Path(td) / 'rule.json'
    rule.write_text(json.dumps(RULE), encoding='utf-8')

    rules = load_rule_file(rule)
    plan = plan_batch(root, rules, chapters=['game/chapter/*.rpy'])
    print('pending', plan.pending)
    for fp in plan.files:
        for ins in fp.insertions:
            print('insert', ins.line, 'after', repr(CHAPTER.split('\n')[ins.line - 1]))
        for sk in fp.skipped:
            print('skip', sk)
    res = apply_plan(plan)
    print('apply', res)
    print('--- new ---')
    print(src.read_text(encoding='utf-8'))
    plan2 = plan_batch(root, rules, chapters=['game/chapter/*.rpy'])
    print('pending after', plan2.pending, plan2.files[0].skipped)
