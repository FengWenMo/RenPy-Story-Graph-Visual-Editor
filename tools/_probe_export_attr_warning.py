# -*- coding: utf-8 -*-
"""真机冒烟：导出前的属性门禁在 chapter2_5 上能抓到那两行。只读。"""
import sys
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')

from storygraph.app.editor import EditorSession                  # noqa: E402
from storygraph.ui_qt.window_export import _scan_sprite_attrs    # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')
CHAPTER = ROOT / 'game' / 'chapter' / 'chapter2_5.rpy'

session = EditorSession(project_root=ROOT)
from storygraph.assets.registry_load import load_asset_registry  # noqa: E402
session._registry_provider = lambda: load_asset_registry(ROOT)
session.load(CHAPTER)
# 模拟"从 xlsx 导回时会话没调过 refresh_symbols"：symbols 空，但资源表在。
session.symbols = None


class Host:
    _session = session
    _registry_provider = staticmethod(lambda: load_asset_registry(ROOT))
    _log = staticmethod(lambda *a, **k: None)


res = _scan_sprite_attrs(Host(), session.beats)
print('checked=%s judged=%s degraded=%s n=%s'
      % (res.checked, res.judged, res.degraded, len(res)))
print(res.report())
