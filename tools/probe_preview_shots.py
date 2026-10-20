"""截图：预览窗口"这一拍还在渲染（顶着上一张 + 角标）" vs "已经画好"。"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
ROOT = Path(r'D:\千连万理\Renpy_Story_Graph_Editor')
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication                      # noqa: E402

from storygraph.app.editor import EditorSession                 # noqa: E402
from storygraph.ui_qt.stage_preview import StagePreviewWindow   # noqa: E402

OUT = Path(os.environ['TEMP']) / 'sg_ui_shots'
OUT.mkdir(parents=True, exist_ok=True)
proj = Path(r'D:\千连万理\seiran_project-2')


def main() -> int:
    app = QApplication.instance() or QApplication([])
    s = EditorSession(registry_provider=None, project_root=proj)
    s.load(str(proj / 'game' / 'chapter' / 'chapter__3_1.rpy'))
    frames = s.frames()

    w = StagePreviewWindow(None, frames, project_root=proj, sound=True)
    w.resize(980, 800)
    w.show()
    app.processEvents()

    # 等第一拍画好
    deadline = time.time() + 10
    while w._canvas._rendered_pix is None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)

    # 挑一拍**有立绘**的（这一章开头几拍是 pure black 的空场，
    # 截出来一片黑，看不出"上一张顶着"的效果）
    rich = next((i for i, f in enumerate(frames)
                 if getattr(f, 'sprites', None)), 0)
    w.show_frame(rich)
    deadline = time.time() + 10
    while w._canvas._rendered_pix is None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    w.grab().save(str(OUT / 'preview_ready.png'))

    # 跳到很后面的一拍（还没合成）：画面是上一张 + 角标。
    # 为了让"正在渲染"这个状态稳定出现，这一段把合成调慢（真机上遇到
    # 大盘立绘解码就是 0.3~1 秒）。
    from storygraph.app import stage_render as R
    real = R.render_frame

    def _slow(*a, **kw):
        time.sleep(1.0)
        return real(*a, **kw)

    R.render_frame = _slow
    far = len(frames) - 2
    w._canvas.show_frame(frames[far], far)     # 不给 ahead：一定在渲染
    app.processEvents()
    time.sleep(0.45)                           # 让角标过 0.25s 的门槛
    app.processEvents()
    w.grab().save(str(OUT / 'preview_pending.png'))
    R.render_frame = real
    print('pending?', w._canvas.is_pending(), ' last_good?',
          w._canvas._last_good_pix is not None)
    w.close()
    print('写好了：', OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
