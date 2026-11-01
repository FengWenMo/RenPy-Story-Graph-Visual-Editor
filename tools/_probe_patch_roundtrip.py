"""临时探针：直接在内存里走 `patch_export`，看特殊分隔符会不会被改写。

CLI 那条路太慢（每个子进程 ~15s），这里只测合并函数本身 —— 它是
"只改你动过的行"这条承诺的唯一实现点。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.patcher import patch_export  # noqa: E402

SAMPLES = {
    'u2028': 'label ch:\n    "第一句。"\n    # 软回车\u2028在这里\n    "第二句。"\n    return\n',
    'formfeed': 'label ch:\n    "第一句。"\n    # 换页符\x0c在这里\n    "第二句。"\n    return\n',
    'nel': 'label ch:\n    "第一句。"\n    # NEL\x85在这里\n    "第二句。"\n    return\n',
    'plain': 'label ch:\n    "第一句。"\n    "第二句。"\n    return\n',
}


def main() -> int:
    work = ROOT / '.pip-tmp' / 'probe_patch'
    work.mkdir(parents=True, exist_ok=True)
    bad = 0
    for name, text in SAMPLES.items():
        p = work / f'{name}.rpy'
        p.write_text(text, encoding='utf-8', newline='')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[p], graph=None)
        beats = beats_from_parse_result(pr)
        res = patch_export(beats, p, generated_text='')
        same = res.text == text
        print(f'[{name}] ok={res.ok} changed={res.changed_lines} '
              f'passthrough={res.passthrough} reasons={res.reasons}')
        print(f'   text == original ? {"是" if same else "**否**"}')
        if not same:
            bad += 1
            out = work / f'{name}.out.rpy'
            out.write_text(res.text, encoding='utf-8', newline='')
            print(f'   → 写出来对比：{out}')
            print(f'   原本 {len(text)} 字符 → 现在 {len(res.text)} 字符')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
