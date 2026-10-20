"""量预览窗口：首帧多久出图、连续播放时有多少拍是"黑着等"的。"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
ROOT = Path(r'D:\千连万理\Renpy_Story_Graph_Editor')
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QTimer                 # noqa: E402
from PySide6.QtWidgets import QApplication                    # noqa: E402

from storygraph.app.editor import EditorSession               # noqa: E402
from storygraph.ui_qt.stage_preview import StagePreviewWindow  # noqa: E402

proj = Path(sys.argv[1] if len(sys.argv) > 1
            else r'D:\千连万理\seiran_project-2')
script = proj / 'game' / 'chapter' / 'chapter__3_1.rpy'


def spin(ms):
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


app = QApplication.instance() or QApplication([])
s = EditorSession(registry_provider=None, project_root=proj)
s.load(str(script))
frames = s.frames()

t0 = time.perf_counter()
w = StagePreviewWindow(None, frames, project_root=proj, sound=False)
w.show()
first = None
while time.perf_counter() - t0 < 10:
    if w._canvas._rendered_pix is not None:
        first = time.perf_counter() - t0
        break
    spin(10)
print(f'拍数 {len(frames)}  首帧出图 {first*1000 if first else float("nan"):.0f} ms  '
      f'缓存 {w._canvas.cached_frames()}')

# 连续播放：每拍看"轮到它的时候有没有图"
BLACK = []
TIMES = []
N = 30
for i in range(N):
    t = time.perf_counter()
    w.start_play()
    w.show_frame(i)
    got = w._canvas._rendered_pix is not None or w._canvas._last_good_pix is not None
    if not got:
        BLACK.append(i)
    # 等到这一拍自己画好（或者超时）
    deadline = time.perf_counter() + 5
    while w._canvas._rendered_pix is None and time.perf_counter() < deadline:
        spin(5)
    TIMES.append((time.perf_counter() - t) * 1000)
    w.stop_play()

print(f'顺序翻 {N} 拍：黑屏 {len(BLACK)} 次  '
      f'每拍等到出图 平均 {sum(TIMES)/len(TIMES):.0f} ms 最慢 {max(TIMES):.0f} ms')
print(f'缓存帧数 {w._canvas.cached_frames()}')

# 回翻（缓存命中）：应该 0ms 出图
hits = 0
t = time.perf_counter()
for i in range(N - 1, -1, -1):
    w.show_frame(i)
    if w._canvas._rendered_pix is not None:
        hits += 1
print(f'回翻 {N} 拍：缓存命中 {hits} 次，总耗时 {(time.perf_counter()-t)*1000:.0f} ms')

# 播放：跟着定时器走，记录有没有"黑着"的拍
black_play = 0
w.show_frame(0)
spin(300)
w.start_play()
for _ in range(40):
    spin(60)
    if w._canvas._rendered_pix is None and w._canvas._last_good_pix is None:
        black_play += 1
w.stop_play()
print(f'播放中（40×60ms）黑屏采样 {black_play} 次，走到第 {w.idx + 1} 拍')
w.close()
