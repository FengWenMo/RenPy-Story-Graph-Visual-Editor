"""渲染金标：**同输入 → 同产物**（M4 拆 `render_beat_to_rpy` 的护栏）。

渲染是"真源"，拆它一旦改动一个字节就是改剧情 —— 所以先录指纹：

  · 真机 53 个 `.rpy` 各自：解析 → 拍 → `render_beats_to_rpy(...)` → 文本；
  · 每份文本按**文件名**喂进 sha256（也单独算一个"逐行哈希"，
    这样报差异时能指出是哪个文件、哪一行）。

    python tools/probe_render_fingerprint.py                 # 打印指纹
    python tools/probe_render_fingerprint.py --save <file>
    python tools/probe_render_fingerprint.py --check <file>

⚠ 与 `tools/audit_idempotence.py` 用**同一个调用**（`render_beats_to_rpy(beats)`，
不带 registry）—— 免得"金标过了但审计不过"。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.discover import discover_scripts          # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result   # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy      # noqa: E402

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')


def fingerprint(root: Path = REAL):
    az = IncrementalRenPyAnalyzer()
    h = hashlib.sha256()
    per_file = {}
    count = 0
    for f in sorted(discover_scripts(root)):
        pr, _ = az.analyze(files=[f], graph=None)
        beats = beats_from_parse_result(pr)
        text = render_beats_to_rpy(beats)
        name = Path(f).name
        per_file[name] = hashlib.sha256(text.encode()).hexdigest()
        h.update(f'{name}\n{text}\n'.encode())
        count += 1
    return h.hexdigest(), per_file, count


def main() -> int:
    root = REAL
    argv = sys.argv[1:]
    if argv and argv[0] not in ('--save', '--check'):
        root = Path(argv[0])
        argv = argv[1:]
    fp, per_file, count = fingerprint(root)
    print(f'文件 {count} 个')
    print('FINGERPRINT=' + fp)

    if len(argv) == 2 and argv[0] == '--save':
        Path(argv[1]).write_text(
            fp + '\n' + '\n'.join(f'{k} {v}' for k, v in sorted(per_file.items()))
            + '\n', encoding='utf-8')
        print(f'saved -> {argv[1]}')
    elif len(argv) == 2 and argv[0] == '--check':
        lines = Path(argv[1]).read_text(encoding='utf-8').split()
        want = lines[0] if lines else ''
        want_files = dict(zip(lines[1::2], lines[2::2]))
        if want != fp:
            print('MISMATCH')
            for k, v in sorted(per_file.items()):
                if want_files.get(k) != v:
                    print(f'  变了：{k}')
            for k in sorted(set(want_files) - set(per_file)):
                print(f'  消失：{k}')
            return 1
        print('MATCH')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
