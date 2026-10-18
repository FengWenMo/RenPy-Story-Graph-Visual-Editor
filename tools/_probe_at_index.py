# -*- coding: utf-8 -*-
"""
`@N` 到底是"第几格"还是"第几个会吃词的格子"？
声明里既有**固定字格**（`swim_suit` / `mood`）又有取值格时，两者不是一回事。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story import spec_args as SA            # noqa: E402
from storygraph.story import statement_registry as SR  # noqa: E402

RULES = {
    'statements': [{
        'name': 'swim_suit',
        'columns': [
            {'name': '人物', 'label': '人物', 'choices': ['lyj', 'ling']},
            {'name': '服装', 'label': '服装', 'choices': ['泳装', '校服']},
            {'name': '心情', 'label': '心情', 'choices': ['happy', 'sad']},
        ],
        'pattern': [
            {'kind': 'keyword', 'text': 'swim_suit'},
            {'kind': 'variable', 'column': '人物', 'options': ['lyj', 'ling']},
            {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服']},
            {'kind': 'keyword', 'text': 'mood'},
            {'kind': 'choice', 'column': '心情', 'options': ['happy', 'sad']},
        ],
    }]
}


def main():
    tmp = Path(tempfile.mkdtemp(prefix='r5_'))
    (tmp / 'storygraph.rules.json').write_text(
        json.dumps(RULES, ensure_ascii=False, indent=2), encoding='utf-8')
    reg = SR.StatementRegistry.for_project(str(tmp), use_cache=False)
    cols = SR.spec_columns(reg, 'swim_suit')
    print('列 / 位置号：')
    for c in cols:
        print(f"   {c['name']:<6} positional={c['positional']}  "
              f"header={SA.dynamic_column_name('swim_suit', c['name'], c['positional'])}")

    row = 'swim_suit lyj "泳装" mood=happy'
    values = {'人物': 'lyj', '服装': '校服', '心情': 'happy'}
    heads = {SA.dynamic_column_name('swim_suit', c['name'], c['positional']):
             values[c['name']] for c in cols}
    heads['__pattern__'] = RULES['statements'][0]['pattern']
    print('\n原样  ->', repr(row))
    print('折回  ->', repr(SA.fold_dynamic_columns(row, heads)))
    print('（预期：swim_suit lyj "校服" mood=happy）')


main()
