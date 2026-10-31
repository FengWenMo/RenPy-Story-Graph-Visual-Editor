"""验证 P0-1：映射精确覆盖 / 项目声明 / glob 规则 三者的实际顺序。只读。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.assets.asset_identity import resolve_image
from storygraph.core.authority import AUTHORITY, IDENTITY_ORDER, layer_of

RULE = {'glob': 'game/images/**', 'kind': 'bg', 'char': 'bg', 'attr': '<stem>'}


def make_row(declared: bool, tag: str = 'bg'):
    # ⚠ `Tag` 必须给：`_from_declared` 是靠 `Tag` 列拆出"名 + 属性"的，
    # 没有它一律按"认不出"处理（那是它**该**有的行为，不是 bug）。
    row = {'ID': 'bg_room', 'Path': 'game/images/bg/room.png', 'Tag': tag}
    if declared:
        # 这三条任一为真即"项目 .rpy 里字面量声明过"（见 `_is_declared_image`）
        row['_layered'] = True
    return row


def show(title, row, mapping):
    ident = resolve_image(row, mapping=mapping, known_tags={'bg'},
                          known_chars={}, rows=[row])
    print(f'{title}')
    print(f'    source={ident.source!r}  kind={ident.kind!r}  '
          f'tag={ident.tag!r}  attrs={ident.attrs}')
    return ident


def main() -> int:
    print('AUTHORITY =', AUTHORITY)
    print('IDENTITY_ORDER =', IDENTITY_ORDER)
    print('layer_of(declared) =', layer_of('declared'),
          '| layer_of(mapping-rule) =', layer_of('mapping-rule'))
    print()

    bad = 0

    # ① 项目声明 + 能命中它的 glob 规则 → 声明必须赢
    a = show('[1] 声明 vs glob 规则  → 期望 declared',
             make_row(True), {'rules': [RULE], 'images': {}})
    if a.source != 'declared':
        bad += 1
        print('    ✗ 规则盖掉了项目声明')

    # ② 项目声明 + images 精确覆盖 → 精确覆盖必须赢
    b = show('[2] 声明 vs 精确覆盖  → 期望 mapping',
             make_row(True),
             {'rules': [RULE],
              'images': {'bg_room': {'kind': 'cg', 'char': 'cg',
                                     'attr': 'cg01'}}})
    if b.source != 'mapping':
        bad += 1
        print('    ✗ 精确覆盖没盖住项目声明')

    # ③ 没有声明 + glob 规则 → 规则可以补空缺
    c = show('[3] 无声明 vs glob 规则 → 期望 mapping-rule',
             make_row(False), {'rules': [RULE], 'images': {}})
    if c.source != 'mapping-rule':
        bad += 1
        print('    ✗ 规则没给空缺补上')

    # ④ 什么都没有 → 推断（不能是 unknown，因为有路径）
    d = show('[4] 裸行（只有路径）    → 期望 inferred 系',
             make_row(False, tag=''), None)
    if d.source not in ('stem', 'folder'):
        bad += 1
        print('    ✗ 裸行的推断结论异常')

    print()
    print('不合格项:', bad)
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
