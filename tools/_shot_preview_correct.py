"""给“预览里显示的到底是哪张图”拍证据（2026-11-05，第三轮）。

用法：

    python tools/_shot_preview_correct.py

输出到 `docs/shots/`：

    hell_detector_preview_aa_2026-11-05.png        `lan aa happy` → happy.png
    hell_detector_preview_clothes2_2026-11-05.png  `lan clothes2 happy` → 叠好的图
    hell_detector_preview_row_2026-11-05.png       两张并排（同一张脸、不同衣服）

为什么用真项目而不是造小图：这里要证明的正是**真机结构**
（`layeredimage lan` 的双 expressions 组 + `crop_lan` 的 AlphaMask 裁切）
在预览里能算出正确的那张图 —— 小图项目证明不了 4320×7680 的裁切路径。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROJECT = Path(r'D:\千连万理\sr_general')
OUT = ROOT / 'docs' / 'shots'


def _app():
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    # 这台机器的 PySide6 没带字体目录；不显式挂一个 Windows 字体，
    # 中文会全是豆腐块（旧证据图能显示中文，就是挂过字体）。
    for font in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\msyh.ttf'):
        if Path(font).is_file():
            QFontDatabase.addApplicationFont(font)
            break
    app.setFont(QFont('Microsoft YaHei', 9))
    return app


def _symbols():
    from storygraph.assets.asset_registry import AssetRegistry
    from storygraph.story.dsl import Symbols

    reg = AssetRegistry()
    reg.project_root = str(PROJECT)
    # 缩略图路径现在由 `resolve_sprite_thumb_path` 的分层兜底算，
    # 这条表数据只负责让 `known_tag_warning` 肯判（与真机同一条件）。
    reg.images['lan_happy'] = {
        'ID': 'lan_happy', 'Tag': 'lan', 'Attributes': 'happy indifferent',
        'Path': 'game/images/characters/lan/happy.png',
        'Category': 'character',
    }
    return Symbols(reg)


def _card(spec: str, loader):
    from storygraph.ui_qt.sprite_grid import SpriteCardWidget

    return SpriteCardWidget(
        {'image': spec, 'expression': '', 'position': '', 'at': '',
         'speaking': False, 'order_index': 0, 'extra_rows': []},
        symbols=_symbols(),
        loader=loader,
        on_resolve_path=None,          # 故意不接解析器：证明“分层兜底”自己也能算对
        assets_ready=True,
    )


def _wait_thumb(app, card, timeout=40.0) -> bool:
    """等缩略图真的解出来（工作线程 → 主线程信号 → `_thumb.setPixmap`）。"""
    from PySide6.QtCore import QThreadPool

    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        pix = card._thumb.pixmap()
        if pix is not None and not pix.isNull():
            return True
        QThreadPool.globalInstance().waitForDone(30)
    app.processEvents()
    pix = card._thumb.pixmap()
    return pix is not None and not pix.isNull()


def main() -> int:
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from storygraph.ui_qt.thumb_loader import ThumbLoader

    app = _app()
    OUT.mkdir(parents=True, exist_ok=True)
    made = []

    def shot_one(spec: str, name: str):
        loader = ThumbLoader(None, max_size=(160, 220),
                            project_root=str(PROJECT))
        card = _card(spec, loader)
        loader.setParent(card)
        card.resize(360, 330)
        card.show()
        app.processEvents()
        ok = _wait_thumb(app, card)
        print(f'{spec!r} -> {name}  thumb_loaded={ok}  '
              f'hint={card._hint_text!r}  thumb_path={card._thumb_path(spec)}')
        card.grab().save(str(OUT / name))
        made.append(OUT / name)
        return card

    shot_one('lan aa happy', 'hell_detector_preview_aa_2026-11-05.png')
    shot_one('lan clothes2 happy',
             'hell_detector_preview_clothes2_2026-11-05.png')

    # 并排：同一张脸（aa=普通）vs 换衣服（clothes2）—— 预览**必须换图**。
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(8, 8, 8, 8)
    lay.setSpacing(8)
    cards = []
    for spec in ('lan aa happy', 'lan clothes2 happy'):
        loader = ThumbLoader(None, max_size=(160, 220),
                            project_root=str(PROJECT))
        card = _card(spec, loader)
        loader.setParent(card)
        cards.append(card)
        lay.addWidget(card)
    row.resize(760, 350)
    row.show()
    app.processEvents()
    for card, spec in zip(cards, ('lan aa happy', 'lan clothes2 happy')):
        print(f'row {spec!r} thumb_loaded={_wait_thumb(app, card)}')
    row.grab().save(str(OUT / 'hell_detector_preview_row_2026-11-05.png'))
    made.append(OUT / 'hell_detector_preview_row_2026-11-05.png')

    print('wrote:', ', '.join(p.name for p in made))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
