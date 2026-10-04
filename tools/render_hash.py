"""
把"解析 → 拍 → 渲染"与"解析 → 拍 → xlsx → 拍 → 渲染"两条路的产物**逐字节哈希**，
用来证明 B 阶段一**没有改行为**：

    # 在本仓库（改了）
    python tools/render_hash.py <项目根>
    # 在 HEAD 的 worktree（没改）里跑同一个脚本 → 哈希必须完全一样

只写临时目录。
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CWD = Path.cwd()
# 先让 `storygraph` 从**当前工作目录**解析（worktree 里跑时要指到那一份）
for cand in (str(_CWD), str(_HERE.parent)):
    if cand in sys.path:
        sys.path.remove(cand)
sys.path.insert(0, str(_CWD))


def main(argv) -> int:
    project = Path(argv[1]) if len(argv) > 1 else Path(
        r'D:\千连万理\RenPyTest\sr_general')
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beats_to_rpy

    files = sorted(f for f in (project / 'game').rglob('*.rpy')
                   if not f.name.endswith('.generated.rpy'))
    total_a = hashlib.sha1()
    total_b = hashlib.sha1()
    per_file = []
    with tempfile.TemporaryDirectory(prefix='rh_') as td:
        from storygraph.story.excel_export import export_beats_to_excel
        from storygraph.story.excel_import import import_beats_from_excel
        for i, f in enumerate(files):
            pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
            beats = beats_from_parse_result(pr)
            a = render_beats_to_rpy(beats)
            xl = Path(td) / f'{i}.xlsx'
            export_beats_to_excel(beats, xl)
            b = render_beats_to_rpy(import_beats_from_excel(xl))
            ha = hashlib.sha1(a.encode('utf-8')).hexdigest()[:12]
            hb = hashlib.sha1(b.encode('utf-8')).hexdigest()[:12]
            total_a.update(a.encode('utf-8'))
            total_b.update(b.encode('utf-8'))
            per_file.append(f'{f.name}\t{ha}\t{hb}')
    print('\n'.join(per_file))
    print(f'== 直接渲染 sha1[:12] = {total_a.hexdigest()[:12]}')
    print(f'== 过表渲染 sha1[:12] = {total_b.hexdigest()[:12]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
