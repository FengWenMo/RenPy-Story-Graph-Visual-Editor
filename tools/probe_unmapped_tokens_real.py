"""看真机章节里那些未映射 token 到底长什么样（只读，用转换器自己的判定）。"""

import glob
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.txt_import import (CharacterMap, convert_file)  # noqa: E402
from storygraph.pipeline.build import load_asset_registry                 # noqa: E402

TXT = Path(r'D:\千连万理\txt--renpy脚本转换工具')
REAL = Path(r'D:\千连万理\RenpyTest\sr_general')

rows = (load_asset_registry(REAL) or {}).characters if REAL.is_dir() else {}
chars = CharacterMap(rows)
seen = {}
for f in sorted(glob.glob(str(TXT / '*-定稿.txt'))):
    r = convert_file(f, characters=chars)
    for tl in r.lines:
        if tl.kind == 'say' and not tl.speaker_var and tl.speaker_token:
            seen.setdefault(tl.speaker_token, []).append(
                (Path(f).name, tl.no, tl.raw.strip()[:64]))
for tok, items in sorted(seen.items()):
    print(f'\n{tok}  {len(items)} 处')
    for name, no, raw in items[:4]:
        print(f'   {name} 第{no}行: {raw}')
