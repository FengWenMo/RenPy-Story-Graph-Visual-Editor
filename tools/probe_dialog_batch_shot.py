# -*- coding: utf-8 -*-
"""把「按规则批处理」弹窗渲染成 PNG（offscreen Qt）—— 人眼验收用。

    python tools/probe_dialog_batch_shot.py [输出路径]

用的是**真项目**的一章 + `examples/batch/master-dissolve.json`：
规则文件是项目资产，界面不内置任何写法。
"""

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8')

from PySide6.QtWidgets import QApplication                     # noqa: E402

from storygraph.ui_qt.dialog_batch import BatchDialog          # noqa: E402

PROJECT = Path(r'D:\千连万理\sr_general')
RULE = ROOT / 'examples' / 'batch' / 'master-dissolve.json'

app = QApplication.instance() or QApplication([])
dlg = BatchDialog(PROJECT if PROJECT.is_dir() else None, RULE)
dlg._chapters.setText('game/chapter/*.rpy')
plan = dlg.preview()
print('计划：', 'errors' if plan is None else
      f'{len(plan.files)} 文件 / {plan.pending} 处')
dlg.show()
app.processEvents()
out = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    ROOT / 'docs' / 'shots' / 'dialog_batch_2026-11-07.png')
out.parent.mkdir(parents=True, exist_ok=True)
print('截图', 'ok' if dlg.grab().save(str(out)) else '失败', '->', out)
