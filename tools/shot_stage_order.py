#!/usr/bin/env python
"""
视觉复核：**拍内演出顺序**改前/改后的**收尾画面**截图（缺口 4 第七·八轮）
============================================================================

为什么要图：这一轮修的是"顺序"，而顺序的意义是**玩家最后看到什么**。
文字判据（产物行序）已经钉住，但"收尾画面反了"这类结论用眼睛看最直观 ——
用户 2026-10-07 明确说"可以视觉截图复核"。

产出两张 PNG（`docs/shots/`）：

    stage_order_fixed.png   ← 修好之后的顺序：show A / hide A / show A
                              **收尾 = A 在台上**
    stage_order_broken.png  ← 修之前的顺序（去重 + 分组后的产物）：
                              show A / hide A  → **收尾 = 台上没人**

两张图跑的是**同一个演示工程**（`tests/_ui_project.build_project`，带真 PNG）、
同一套舞台预览控件（`ui_qt.stage_preview.StageCanvas`，offscreen 真 Qt），
只把那一拍的指令换成"修前/修后"两种顺序 —— 差异因此只可能来自顺序。

    python tools/shot_stage_order.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from PySide6.QtWidgets import QApplication                       # noqa: E402

from storygraph.app.editor import EditorSession                  # noqa: E402
from storygraph.ui_qt.stage_preview import StageCanvas           # noqa: E402
from _ui_project import build_project                            # noqa: E402

#: 修前 / 修后：同一拍、同一批图像，**只有顺序不同**
CASES = {
    # 作者要的：最后一条 show 让 A 重新站上台（收尾 = A 在台上）
    'fixed': ['show ling happy', 'hide ling', 'show ling happy'],
    # 修之前工具吐出来的：按内容去重 + 按 verb 分组 → 只剩 show/hide（收尾 = 没人）
    'broken': ['show ling happy', 'hide ling'],
}


def _project(tmp: Path) -> Path:
    return build_project(tmp / 'proj')


def _beats_for(project: Path, lines: list):
    """把这几行写成一个小章节，解析成拍。"""
    game = project / 'game'
    f = game / 'order_probe.rpy'
    f.write_text('label order_probe:\n'
                 + ''.join(f'    {ln}\n' for ln in lines)
                 + '    return\n', encoding='utf-8')
    s = EditorSession(project_root=project)
    s.load(f)
    return s


def main() -> int:
    import tempfile
    out = ROOT / 'docs' / 'shots'
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix='shot_stage_order_'))
    made = []
    for tag, lines in CASES.items():
        project = _project(tmp / tag)
        session = _beats_for(project, lines)
        frames = session.frames()
        canvas = StageCanvas(project_root=project, real_images=True)
        canvas.setFixedSize(1280, 720)
        canvas.show_frame(frames[-1], len(frames) - 1)
        app.processEvents()
        png = out / f'stage_order_{tag}.png'
        ok = canvas.grab().save(str(png))
        made.append((tag, png, ok))
        print(f'[{tag}] {lines} → {png}（存成功={ok}）')
        print(f'    收尾舞台：{session.stage_digest_text(len(frames) - 1)!r}')
        canvas.shutdown() if hasattr(canvas, 'shutdown') else None
    print()
    for tag, png, ok in made:
        print(f'  {tag}: {"OK" if ok else "失败"} {png}')
    return 0 if all(ok for _t, _p, ok in made) else 1


if __name__ == '__main__':
    raise SystemExit(main())
