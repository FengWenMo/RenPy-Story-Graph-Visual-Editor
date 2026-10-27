"""截图：给《新手快速操作手册》用（**只读用户的 demo，不碰仓库里的 demo/**）。

    python tools/_shot_manual.py

产物：`docs/manual_img/01_structure.png`（剧情结构页）、`02_card.png`（卡片页）。

为什么要把 demo 拷到临时目录再打开：`📁 打开项目` 会往项目根写
`storygraph.session.json`（折叠状态 / 上次项目）。对着仓库里的 `demo/` 跑，
截图脚本就变成"改工作区"了 —— 截图不能有副作用。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
os.environ.setdefault('STORYGRAPH_NO_SESSION', '1')   # 不写用户偏好里的 session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication                       # noqa: E402

OUT = ROOT / 'docs' / 'manual_img'


def _pump(app, seconds: float):
    """让事件循环真的跑起来（舞台图是后台线程渲染的，睡一下才画得出来）。"""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.05)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from storygraph.ui_qt.main_window import MainWindowQt

    tmp = Path(tempfile.mkdtemp(prefix='shot-manual-'))
    demo = tmp / 'demo'
    shutil.copytree(ROOT / 'demo', demo,
                    ignore=shutil.ignore_patterns('build*', '__pycache__',
                                                  '*.db', '.pytest_cache'))
    chapter = demo / 'game' / 'script' / 'ch01.rpy'

    class _Engine:
        project_root = str(demo)

        def get_registry(self):
            return None

    win = MainWindowQt(_Engine(), chapter)
    win.resize(1600, 1000)
    win.show()
    _pump(app, 0.6)
    win.open_project_path(str(demo))          # 资源表 + 左栏章节导航
    _pump(app, 1.2)

    OUT.mkdir(parents=True, exist_ok=True)

    # ① 剧情结构页：把这一章导入图里，再按文件顺序摆一次
    win.import_chapter_files([str(chapter)])
    win._set_page(win.PAGE_GRAPH)
    _pump(app, 0.5)
    try:
        win.arrange_by_file()
    except Exception:                         # noqa: BLE001 - 排不开就用默认位置
        pass
    # 只有一个块时 `fit_to_content` 会放大到 300%+（一块顶满屏幕，看不出这是画布），
    # 所以固定放到 200% 并把它摆到中间。
    try:
        win._canvas.zoom_reset()
        win._canvas.zoom_by(2.0)
        win._canvas.center_on_node(next(iter(win.graph.nodes)))
    except Exception:                         # noqa: BLE001
        pass
    _pump(app, 0.8)
    p1 = OUT / '01_structure.png'
    win.grab().save(str(p1))
    print('saved', p1)

    # ② 卡片页：第一拍（舞台小图要等后台线程画出来）
    win._set_page(win.PAGE_CARD)
    win._show_index(0)
    _pump(app, 3.0)
    p2 = OUT / '02_card.png'
    win.grab().save(str(p2))
    print('saved', p2)

    # ⚠ 不能 `win.close()`：导入过章节 = 有"未导出"改动，关窗会弹
    #    「保存 / 不保存 / 取消」**模态框**，无人点击就永远卡住（实测 45s 超时）。
    #    图已经存盘，直接退出；临时目录留给系统清。
    sys.stdout.flush()
    os._exit(0)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
