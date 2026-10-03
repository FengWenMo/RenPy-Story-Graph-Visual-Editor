#!/usr/bin/env python
"""排查：解析器对各类台词样本是否能产出 statement。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa

SAMPLES = {
    '纯中文无标点': 'label a:\n    e "喜欢你"\n',
    '中文逗号': 'label a:\n    e "元神，启动"\n',
    '中文感叹号': 'label a:\n    e "元神启动！"\n',
    '逗号+感叹号': 'label a:\n    e "元神，启动！"\n',
    '带尾空格': 'label a:\n    e "元神，启动！ "\n',
    '英文': 'label a:\n    e "hello world"\n',
    '旁白': 'label a:\n    "这是一句旁白"\n',
}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='probe_'))
    for name, src in SAMPLES.items():
        f = tmp / 's.rpy'
        f.write_text(src, encoding='utf-8')
        analyzer = IncrementalRenPyAnalyzer()
        pr, _ = analyzer.analyze(files=[f], graph=None)
        st = list(getattr(pr, 'statements', []))
        kinds = [s.kind for s in st]
        texts = [(s.payload.get('text') or '') for s in st if s.kind == 'say']
        print(f'{name:<12} statements={len(st)} {kinds} text={texts}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
