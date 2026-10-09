"""增量补丁金标：`patch_export` / `_replace_matched` 的**同输入 → 同产物**
（M4 拆 `patcher.py` 两个热点用的护栏）。

补丁是**写用户 `.rpy`** 的那条路，而且是全仓最贵的一类缺陷的来源（"半对"、
静默丢第三类内容）。所以金标覆盖**三条路径**，每条都逐文件记录：

  ① `direct`     ：拍 → `render_beats_to_rpy` → `merge_incrementally(source_path=…)`
  ② `via_excel`  ：拍 → 过表（导→读）→ `compile_beats` → 增量  ← 生产主路径
  ③ `edited`     ：② 之后再**改几条拍**（每 7 条改一次文案）→ 增量 ← 压配对逻辑

指纹 = 每文件 × 每路径的 `(used_patch, 产物 sha256, PatchResult 的几个计数, reasons)`。

    python tools/probe_patch_fingerprint.py
    python tools/probe_patch_fingerprint.py --save <file>
    python tools/probe_patch_fingerprint.py --check <file>
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
from storygraph.pipeline.compile import (compile_beats,           # noqa: E402
                                         merge_incrementally)
from storygraph.story.beat_build import beats_from_parse_result   # noqa: E402
from storygraph.story.excel_export import export_beats_to_excel   # noqa: E402
from storygraph.story.excel_import import import_beats_from_excel  # noqa: E402

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')

#: `PatchResult` 里要记账的字段（**都是计数**，最容易看出"少配了几条"）
STATS = ('matched_beats', 'total_beats', 'changed_lines', 'stale_beats',
         'unlanded_lines', 'dropped_beats')


def _parse_copy(src: Path, work: Path):
    """把源文件复制到临时目录再解析（金标只读真机，不动它）。"""
    dst = work / src.name
    dst.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')
    az = IncrementalRenPyAnalyzer()
    pr, _ = az.analyze(files=[dst], graph=None)
    return dst, beats_from_parse_result(pr)


def _via_excel(beats, work: Path, tag: str):
    xl = work / f'{tag}.xlsx'
    export_beats_to_excel(beats, xl)
    return import_beats_from_excel(xl)


def _rec(outcome) -> dict:
    res = getattr(outcome, 'patch', None) or getattr(outcome, 'result', None)
    rec = {'used_patch': bool(getattr(outcome, 'used_patch', False)),
           'text': hashlib.sha256(
               str(getattr(outcome, 'text', '') or '').encode()).hexdigest()[:16]}
    for f in STATS:
        rec[f] = getattr(res, f, None)
    rec['reasons'] = sorted(str(x) for x in
                            (getattr(res, 'reasons', None) or []))
    return rec


def fingerprint(root: Path = REAL):
    files = sorted(discover_scripts(root))
    per_file = {}
    for i, f in enumerate(files):
        with tempfile.TemporaryDirectory(prefix='patch_fp_') as td:
            work = Path(td)
            src, beats = _parse_copy(Path(f), work)
            rec = {}
            # ① direct
            gen = compile_beats(beats).rpy_text
            rec['direct'] = _rec(merge_incrementally(beats, gen,
                                                     source_path=src))
            # ② via_excel
            back = _via_excel(beats, work, 'a')
            gen2 = compile_beats(back).rpy_text
            rec['via_excel'] = _rec(merge_incrementally(back, gen2,
                                                        source_path=src))
            # ③ edited（每 7 条改一次文案 —— 确定性）
            for j, b in enumerate(back):
                if j % 7 == 3 and getattr(b, 'text', ''):
                    b.text = b.text + 'X'
            gen3 = compile_beats(back).rpy_text
            rec['edited'] = _rec(merge_incrementally(back, gen3,
                                                     source_path=src))
            per_file[Path(f).name] = rec
    text = json.dumps(per_file, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest(), text, per_file


def main() -> int:
    root = REAL
    argv = sys.argv[1:]
    if argv and argv[0] not in ('--save', '--check'):
        root = Path(argv[0])
        argv = argv[1:]
    fp, text, per_file = fingerprint(root)
    inc = sum(1 for rec in per_file.values()
              for v in rec.values() if v['used_patch'])
    print(f'文件 {len(per_file)} 个；三条路径里 used_patch={inc} 次')
    print('FINGERPRINT=' + fp)

    if len(argv) == 2 and argv[0] == '--save':
        Path(argv[1]).write_text(fp + '\n' + text + '\n', encoding='utf-8')
        print(f'saved -> {argv[1]}')
    elif len(argv) == 2 and argv[0] == '--check':
        body = Path(argv[1]).read_text(encoding='utf-8').split('\n', 1)
        want = body[0].strip()
        if want != fp:
            old = json.loads(body[1]) if len(body) > 1 else {}
            print('MISMATCH')
            for name in sorted(set(old) | set(per_file)):
                a, b = old.get(name) or {}, per_file.get(name) or {}
                for k in sorted(set(a) | set(b)):
                    if a.get(k) != b.get(k):
                        print(f'  {name} [{k}]\n    旧: {a.get(k)}'
                              f'\n    新: {b.get(k)}')
            return 1
        print('MATCH')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
