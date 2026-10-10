"""校验 `char_decisions` 的两个前提：拼音首字母表 + 建议/计划的行为（临时探针）。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.char_decisions import (                    # noqa: E402
    plan_alias, plan_new_character, suggestions)
from storygraph.authoring.txt_import import CharacterMap, convert_text  # noqa: E402

print('=== 1. （拼音首字母已按设计撤掉 —— 见 char_decisions 文件上方说明）')

print('\n=== 2. 真机 8 个未映射 token 的候选（用真机角色表）')
from storygraph.pipeline.build import load_asset_registry             # noqa: E402
REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
rows = (load_asset_registry(REAL) or {}).characters if REAL.is_dir() else {}
chars = CharacterMap(rows)
text = ('MOM"跟妈客气啥"\nA"卷王——"\nB"大学生楷模——"\nLINGDAO"金秋九月"\n'
        'JIAOGUAN"预备——"\nXIAOYI"吃早餐了吗"\nBAOAN"你要去哪个地方"\n'
        'TJF"你怎么睡得着哒"\nLJK"我是谁"\nL_J_K"下划线"\n')
r = convert_text(text, characters=chars)
print(f'   unmapped={dict(r.unmapped)}  ambiguous={dict(r.ambiguous)}')
for sug in suggestions(r, chars, rows):
    print(f'\n   [{sug.token}] x{sug.count}  {sug.note or ""}')
    for no, raw in sug.samples[:2]:
        print(f'        第{no}行 {raw}')
    for c in sug.candidates:
        print(f'        → {c.var:8s} {c.evidence:6s} {c.score:.2f}  {c.reason}')

print('\n=== 3. 计划层：能加 / 不能加')
plan = plan_alias(rows, 'MOM', 'mm')
print(f'   plan_alias(MOM→mm)      ok={plan.ok}  before={plan.before!r} after={plan.after!r}')
print(f'      reason={plan.reason}')
for tok, var in (('LINGDAO', 'ld'), ('JIAOGUAN', 'jg'), ('LJK', 'ljk')):
    p = plan_alias(rows, tok, var)
    print(f'   plan_alias({tok}→{var}) ok={p.ok}  {p.reason or p.after}')
for tok, var in (('LJK', 'ljk'), ('MOM', 'mm')):
    p = plan_new_character(rows, tok, var=var)
    print(f'   plan_new_character({tok}, var={var}) ok={p.ok}  {p.reason or p.row}')
