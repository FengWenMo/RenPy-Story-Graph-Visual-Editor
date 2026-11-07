"""给立绘卡片的提示条拍证据图（2026-11-05）。

用法：

    python tools/_shot_hint_verdict.py

输出到 `docs/shots/`。缩略图那格写"没有"是这里没接资源解析器，
**提示条**才是要证明的东西。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROJECT = Path(r'D:\千连万理\sr_general')
OUT = ROOT / 'docs' / 'shots'


class _Loader:
    project_root = str(PROJECT)

    @staticmethod
    def request(*_args, **_kwargs):
        return None


def _symbols():
    from storygraph.assets.asset_registry import AssetRegistry
    from storygraph.story.dsl import Symbols

    reg = AssetRegistry()
    reg.project_root = str(PROJECT)
    reg.images['lan_base'] = {
        'ID': 'lan_base', 'Tag': 'lan', 'Attributes': 'happy',
        'Path': 'game/images/characters/lan/base.png',
        'Category': 'character',
    }
    return Symbols(reg)


def main() -> int:
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    from storygraph.ui_qt.sprite_grid import SpriteCardWidget

    app = QApplication.instance() or QApplication(sys.argv)
    # 这台机器的 PySide6 没带字体目录；不显式挂一个 Windows 字体，
    # 中文会全是豆腐块（旧证据图能显示中文，就是挂过字体）。
    for font in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\msyh.ttf'):
        if Path(font).is_file():
            QFontDatabase.addApplicationFont(font)
            break
    app.setFont(QFont('Microsoft YaHei', 9))
    sym = _symbols()
    OUT.mkdir(parents=True, exist_ok=True)

    cases = [
        ('lan aa happy', 'hell_detector_aa_silent_2026-11-05.png'),
        ('lan based', 'hell_detector_based_resolved_2026-11-05.png'),
    ]
    for spec, name in cases:
        card = SpriteCardWidget(
            {'image': spec, 'expression': '', 'position': '', 'at': '',
             'speaking': False, 'order_index': 0, 'extra_rows': []},
            symbols=sym,
            loader=_Loader(),
            on_resolve_path=lambda _s: None,
            assets_ready=True,
        )
        card.resize(620, 300)
        card.show()
        app.processEvents()
        path = OUT / name
        card.grab().save(str(path))
        print(f'{spec!r} -> {path.name}  hint={card._hint_text!r}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
