"""show 各形态往返冒烟（必须写 .py：多行源码走 shell 管道会被吃掉）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy

CASES = {
    '普通show': 'label s:\n    show e happy\n    e "x"\n',
    '带at': 'label s:\n    show e happy at i11\n    e "x"\n',
    '带with': 'label s:\n    show e happy with dissolve\n    e "x"\n',
    '复杂behind': 'label s:\n    show e happy at i11 behind qing\n    e "x"\n',
    'expression': 'label s:\n    show expression "x.png" as q at i11\n    e "x"\n',
    '多show一拍': 'label s:\n    show a happy\n    show b sad\n    e "x"\n',
    'scene': 'label s:\n    scene bg room with fade\n    e "x"\n',
    'hide': 'label s:\n    hide e\n    e "x"\n',
    '带块show': 'label s:\n    show e happy:\n        zoom 0.1\n    e "x"\n',
    '带块+with': 'label s:\n    show e happy at right:\n        zoom 0.1\n    with dissolve\n    e "x"\n',
}


def run(src: str):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / 'a.rpy'
        f.write_text(src, encoding='utf-8')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        report = []
        beats = beats_from_parse_result(pr, report=report)
        return render_beats_to_rpy(beats), report


def main() -> int:
    bad = 0
    for name, src in CASES.items():
        out, report = run(src)
        want = [ln.strip() for ln in src.split('\n')[1:] if ln.strip()]
        got = [ln.strip() for ln in out.split('\n')
               if ln.strip() and not ln.startswith('#') and not ln.startswith('label')
               and ln.strip() != 'return']
        ok = got == want and not report
        if not ok:
            bad += 1
        print(('  OK   ' if ok else '  DIFF ') + name)
        if not ok:
            print('     want:', want)
            print('     got :', got)
            if report:
                print('     未落点:', report)
    print(f'\n{len(CASES) - bad}/{len(CASES)} 形态逐行一致')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
