"""Hydrate 金标：表 → 拍（`_hydrate_stage` 那一层）的**同输入 → 同结果**
（M4 拆 `beat_build._hydrate_stage` 的护栏）。

hydrate 把「舞台（高级）」列的 DSL **并回结构化字段**（`show/hide/scene/at/with_`），
并把"原生真迹"记在动态属性上（`_stage_native_show/hide/scene`）。它出错的形态是
**静默**的：某一类 op 没并回去 → 渲染少一行，或者并重了 → 产物多一行。

所以金标 = **整批拍的可序列化快照**：`to_row()`（所有列）+ 那几个动态属性。

    python tools/probe_hydrate_fingerprint.py
    python tools/probe_hydrate_fingerprint.py --save <file>
    python tools/probe_hydrate_fingerprint.py --check <file>
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.discover import discover_scripts          # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.pipeline.build import load_asset_registry          # noqa: E402
from storygraph.story.beat_build import (beats_from_parse_result,  # noqa: E402
                                         hydrate_from_dsl)
from storygraph.story.beat_render import make_symbols             # noqa: E402
from storygraph.story.excel_export import export_beats_to_excel   # noqa: E402
from storygraph.story.excel_import import import_beats_from_excel  # noqa: E402

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')

#: hydrate 会写到拍上的**动态属性**（不进 Excel，但必须一致）
ATTRS = ('_stage_native_show', '_stage_native_hide', '_stage_native_scene',
         '_hydrated_stage')


def _snapshot(beat) -> dict:
    rec = {'row': [str(x) for x in beat.to_row()]}
    # ⚠ **ContentHash 不算 hydrate 的输出**：它是内容指纹，算法在
    # 2026-10-17 收敛字段契约时**有意**改了输入集（P0-4：补上 spec_head /
    # opaque / code_blocks / file_blocks 等）。把它留在快照里，这张
    # "hydrate 结果"的金标会因为一次哈希升级整批变红，掩盖真正要抓的
    # "某类 op 并少了/并重了"。指纹本身另有 `test_content_hash_*` 守。
    try:
        from storygraph.story.beat_model import BEAT_FIELDS
        rec['row'][BEAT_FIELDS.index('ContentHash')] = ''
    except (ImportError, ValueError):
        pass
    for a in ATTRS:
        v = getattr(beat, a, None)
        if isinstance(v, (set, frozenset)):
            rec[a] = sorted(str(x) for x in v)
        else:
            rec[a] = None if v is None else str(v)
    for f in ('show', 'hide', 'scene', 'at', 'with_', 'stage', 'stage_order',
              'op_order'):
        rec[f] = str(getattr(beat, f, '') or '')
    return rec


def fingerprint(root: Path = REAL):
    files = sorted(discover_scripts(root))
    per_file = {}
    az = IncrementalRenPyAnalyzer()
    registry = load_asset_registry(root)
    for f in files:
        with tempfile.TemporaryDirectory(prefix='hydrate_fp_') as td:
            work = Path(td)
            dst = work / Path(f).name
            dst.write_text(Path(f).read_text(encoding='utf-8'), encoding='utf-8')
            pr, _ = az.analyze(files=[dst], graph=None)
            beats = beats_from_parse_result(pr)
            # 生产 `build` 那条路：脚本解析出来的拍也会 hydrate 一次
            hydrate_from_dsl(beats, make_symbols(registry))
            xl = work / 'a.xlsx'
            export_beats_to_excel(beats, xl)
            # 读表那条路**自带** hydrate（`import_beats_from_excel` 里调的）
            back = import_beats_from_excel(xl, registry)
            per_file[Path(f).name] = [_snapshot(b) for b in back]
    text = json.dumps(per_file, ensure_ascii=False, sort_keys=True)
    per_hash = {k: hashlib.sha256(
        json.dumps(v, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest() for k, v in per_file.items()}
    return hashlib.sha256(text.encode()).hexdigest(), text, per_file, per_hash


def main() -> int:
    root = REAL
    argv = sys.argv[1:]
    if argv and argv[0] not in ('--save', '--check'):
        root = Path(argv[0])
        argv = argv[1:]
    fp, text, per_file, per_hash = fingerprint(root)
    print(f'文件 {len(per_file)} 个；拍合计 '
          f'{sum(len(v) for v in per_file.values())} 条')
    print('FINGERPRINT=' + fp)

    # ⚠ 金标**只存逐文件哈希**（53 行）：整份快照有 800+ KB，进仓库太重；
    # 逐文件哈希仍能把"变了"定位到**哪个文件**，字段级差异现算即可。
    if len(argv) == 2 and argv[0] == '--save':
        Path(argv[1]).write_text(
            fp + '\n' + '\n'.join(f'{k} {v}'
                                  for k, v in sorted(per_hash.items())) + '\n',
            encoding='utf-8')
        print(f'saved -> {argv[1]}')
    elif len(argv) == 2 and argv[0] == '--check':
        lines = Path(argv[1]).read_text(encoding='utf-8').split()
        want = lines[0] if lines else ''
        want_files = dict(zip(lines[1::2], lines[2::2]))
        if want != fp:
            print('MISMATCH')
            for name in sorted(per_hash):
                if want_files.get(name) != per_hash[name]:
                    print(f'  {name}: 变了')
                    for i, rec in enumerate(per_file[name]):
                        pass
            return 1
        print('MATCH')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
