"""校验金标：`validate_level1` 的**同输入 → 同报告**（M4 拆它的护栏）。

校验的问题清单是"给人看的结论"：拆它时多报一条假警、少报一条真错，都算行为变了
（而且很容易**悄悄**变 —— 少一条错误没人会发现）。

  · 真机 53 个 `.rpy` → 拍；
  · **两种输入**：带资源表（真机 `game/asset_tables`）/ 不带（降级路径，走的是另一批分支）；
  · 指纹 = 每条 `(code, severity, where, message)` 排序后的列表 + 按 code 的条数。

    python tools/probe_validate_fingerprint.py
    python tools/probe_validate_fingerprint.py --save <file>
    python tools/probe_validate_fingerprint.py --check <file>
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.discover import discover_scripts          # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.pipeline.build import load_asset_registry         # noqa: E402
from storygraph.pipeline.validate import validate_level1          # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result   # noqa: E402

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')


def _beats(root: Path):
    az = IncrementalRenPyAnalyzer()
    out = []
    for f in sorted(discover_scripts(root)):
        pr, _ = az.analyze(files=[f], graph=None)
        out.extend(beats_from_parse_result(pr))
    return out


def fingerprint(root: Path = REAL):
    beats = _beats(root)
    registry = load_asset_registry(root)
    cases = {}
    for tag, reg in (('with_registry', registry), ('no_registry', None)):
        issues = validate_level1(beats, reg)
        cases[tag] = sorted(
            f'{i.level}|{i.code}|{i.severity}|{i.where}|{i.message}|{i.hint}'
            for i in issues)
    counts = {tag: dict(Counter(x.split('|')[1] for x in v))
              for tag, v in cases.items()}
    text = json.dumps(cases, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest(), text, counts, len(beats)


def main() -> int:
    root = REAL
    argv = sys.argv[1:]
    if argv and argv[0] not in ('--save', '--check'):
        root = Path(argv[0])
        argv = argv[1:]
    fp, text, counts, n = fingerprint(root)
    print(f'拍 {n} 条；问题条数：'
          + json.dumps({k: sum(v.values()) for k, v in counts.items()},
                       ensure_ascii=False, sort_keys=True))
    print('按 code：' + json.dumps(counts, ensure_ascii=False, sort_keys=True))
    print('FINGERPRINT=' + fp)

    if len(argv) == 2 and argv[0] == '--save':
        Path(argv[1]).write_text(fp + '\n' + text + '\n', encoding='utf-8')
        print(f'saved -> {argv[1]}')
    elif len(argv) == 2 and argv[0] == '--check':
        body = Path(argv[1]).read_text(encoding='utf-8').split('\n', 1)
        want = body[0].strip()
        if want != fp:
            old = json.loads(body[1]) if len(body) > 1 else {}
            new = json.loads(text)
            print('MISMATCH')
            for tag in sorted(set(old) | set(new)):
                a, b = old.get(tag) or [], new.get(tag) or []
                if a != b:
                    print(f'  {tag}: {len(a)} -> {len(b)}')
                    for x in sorted(set(a) - set(b))[:6]:
                        print(f'    少了: {x[:110]}')
                    for x in sorted(set(b) - set(a))[:6]:
                        print(f'    多了: {x[:110]}')
            return 1
        print('MATCH')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
