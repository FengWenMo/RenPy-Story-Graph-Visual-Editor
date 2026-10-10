"""定向探针 3：括号出现在台词引号**内部**时会怎样（真机 5 章实测）+ 校验器能否兜住未映射说话人。"""

from __future__ import annotations

import glob
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.txt_import import (                      # noqa: E402
    CharacterMap, convert_text, read_text_file)
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.pipeline.build import load_asset_registry           # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result     # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy        # noqa: E402

TXT_DIR = Path(r'D:\千连万理\txt--renpy脚本转换工具')
REAL = Path(r'D:\千连万理\RenpyTest\sr_general')


def head(t: str) -> None:
    print('\n' + '=' * 78 + f'\n{t}\n' + '=' * 78)


head('A. 真机 5 章：括号出现在引号**内部**的行有多少')
if TXT_DIR.is_dir():
    chars = (CharacterMap((load_asset_registry(REAL) or {}).characters)
             if REAL.is_dir() else None)
    total = 0
    for f in sorted(glob.glob(str(TXT_DIR / '*-定稿.txt'))):
        text = read_text_file(f)
        r = convert_text(text, characters=chars, filename=Path(f).name)
        hits = []
        for tl in r.lines:
            if not tl.comments or not tl.text:
                continue
            # 注释是从**去掉括号后的正文**上抽的；如果注释内容原本在引号里，
            # 就说明这句台词被"挖了个洞"
            if tl.kind == 'say' and any(c and c in tl.raw for c in tl.comments):
                for c in tl.comments:
                    if f'（{c}）' in tl.raw or f'({c})' in tl.raw:
                        inner = tl.raw.split('"')
                        if len(inner) >= 2 and f'（{c}）' in inner[-1]:
                            hits.append((tl.no, c, tl.raw.strip()[:60]))
        if hits:
            print(f'\n  {Path(f).name}: {len(hits)} 行"引号内的括号被挖掉"')
            for no, c, raw in hits[:6]:
                print(f'    第{no}行 注释={c!r}  原文: {raw}')
        total += len(hits)
    print(f'\n  合计 {total} 行')
else:
    print('  定稿 txt 不在本机，跳过')

head('B. 校验器能不能兜住"未映射角色"（产物里有未 define 的说话人）')
try:
    reg = load_asset_registry(REAL) if REAL.is_dir() else None
except Exception as exc:                            # noqa: BLE001
    reg = None
    print(f'  读真机资源表失败：{exc}')

r = convert_text('OK"好的"\nMOM"吃饭了"\n正常旁白',
                 characters={'jc': {'ID': 'jc', 'Name': '「蒋忱」'}})
print('  --- 产物 ---')
for ln in r.rpy_text.splitlines():
    print(f'  | {ln}')
with tempfile.TemporaryDirectory(prefix='probe3_') as td:
    p = Path(td) / 'a.rpy'
    p.write_text(r.rpy_text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[p], graph=None)
    beats = beats_from_parse_result(pr)

from storygraph.pipeline.validate import validate_level1        # noqa: E402
for label, registry in (('不带资源表', None), ('带真机资源表', reg)):
    issues = validate_level1(beats, registry if registry else None)
    print(f'\n  [{label}] {len(issues)} 条：')
    for it in issues:
        code = getattr(it, 'code', '?')
        sev = getattr(it, 'severity', '?')
        msg = getattr(it, 'message', '')
        print(f'    {code} {sev} {msg[:100]}')
    if not issues:
        print('    （空）—— 未映射说话人**没有任何校验兜底**')
