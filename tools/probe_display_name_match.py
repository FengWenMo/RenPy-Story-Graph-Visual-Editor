"""实测：txt 里直接写显示名（`妈妈"…"`）能不能命中真机角色表。

用户 2026-10-10：
> 通常来说都会定义显示出来的名称，如果显示出来的名称跟写在 txt 的字符串完美对应，
> 那也是同一个人物啊？比如 mm 对应为妈妈，我在剧本里就写"妈妈"……
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.char_decisions import suggestions      # noqa: E402
from storygraph.authoring.txt_import import (CharacterMap,       # noqa: E402
                                            convert_text, _norm_key)
from storygraph.pipeline.build import load_asset_registry        # noqa: E402

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
rows = (load_asset_registry(REAL) or {}).characters
print('=== 真机 characters.xlsx 里前 8 行（看 Name 列的真面目）')
for i, (k, v) in enumerate(rows.items()):
    if i >= 8:
        break
    print(f'  ID={v.get("ID")!r:14s} Name={v.get("Name")!r:16s} '
          f'归一(Name)={_norm_key(v.get("Name"))!r}')

chars = CharacterMap(rows)
print('\n=== 直接写显示名，能不能命中？')
TESTS = ['妈妈', '「蒋忱」', '蒋忱', '校领导', '保安', '教官', '校医',
         '汤X凤', '凌跃捷', '叶怀瑾', '蓝', '青', '舍友A', '班导', '姥爷']
for tok in TESTS:
    var, how = chars.lookup(tok)
    print(f'  lookup({tok!r:8s}) -> {(var, how)}')

print('\n=== 写进一段 txt 看整体判定（含「括号显示名」）')
text = ('妈妈"跟妈客气啥"\n'
        '「蒋忱」"我是写显示名的那位"\n'
        '蒋忱"不带括号也行吗"\n'
        '校领导"金秋九月"\n'
        '保安"你要去哪个地方"\n'
        '教官"预备——"\n'
        '校医"吃早餐了吗"\n')
conv = convert_text(text, characters=chars)
for line in conv.rpy_text.splitlines():
    if line.strip() and not line.startswith('label'):
        print('   ', line)
print('  未映射 :', dict(conv.unmapped))
print('  歧义   :', dict(conv.ambiguous))
