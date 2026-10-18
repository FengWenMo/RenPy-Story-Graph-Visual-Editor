"""探针：存量声明的角色候选是显示名时，试切为什么认不出剧本里的 tag/变量名。

跑法：`python tools/_probe_role_spellings.py`
（只读，不改任何文件；读的是真机项目的 rules.json + characters.xlsx）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.authoring import learn_statement as L       # noqa: E402
from storygraph.authoring import pattern as P              # noqa: E402

ROOT = Path(r'D:\千连万理\RenPyTest\sr_general')


def _slots_from_columns(columns):
    """老形状声明（只有 columns）→ 界面会做的那件事（`_fill_params`）。"""
    out = []
    for col in columns:
        # ⚠ 与卡片 `ValueChips.text()` 同一口径（逗号），不是 `_fill_params` 的顿号串
        choices = ','.join(str(x) for x in (col.get('choices') or ()))
        out.append({'kind': 'choice' if col.get('choices') else 'variable',
                    'column': str(col.get('label') or col.get('name') or ''),
                    'choices': choices})
    return out


def main() -> int:
    data = json.loads((ROOT / 'storygraph.rules.json').read_text(encoding='utf-8'))
    decl = (data.get('statements') or [{}])[0]
    columns = decl.get('columns') or []
    head = str(decl.get('name') or '')
    print('声明：', head, '｜', [c.get('name') for c in columns])

    slots = P.with_optional_defaults(_slots_from_columns(columns), head)
    for s in slots:
        s['limit'] = True                      # 用户在界面上勾了"只能从下面这些里选"
    line = 'lan base_swimsuit happy'
    got = P.match_line(slots, line, word_offset=1)
    print('\n现在（候选=显示名）：ok =', got['ok'])
    print('  reason    :', got.get('reason'))
    print('  rejected  :', got.get('rejected'), got.get('rejected_word'))
    print('  leftover  :', got.get('leftover'))

    # ---- 把角色那格的候选换成"剧本里真要写的词" ----
    values = L.parse_choices(columns[0].get('choices'))
    fixed = L.role_candidates_for(ROOT, head, values)
    print('\n角色候选映射：')
    for old, new in list(fixed['changed'].items())[:6]:
        print(f'  {old} → {new}')
    print('  共换掉', len(fixed['changed']), '个；得到的值：', fixed['values'][:8], '…')

    columns[0] = dict(columns[0], choices=fixed['values'])
    slots2 = P.with_optional_defaults(_slots_from_columns(columns), head)
    for s in slots2:
        s['limit'] = True
    got2 = P.match_line(slots2, line, word_offset=1)
    print('\n换过之后：ok =', got2['ok'], '｜', got2.get('reason'))
    print('  cells:', [(c['text'], c['column'], c['ok']) for c in got2['cells']])
    print('  labels:', fixed['labels'])
    return 0 if got2['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
