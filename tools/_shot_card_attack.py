"""截图：卡片编辑器面对**极端自定义语句**时的排版（自适应检查）。

    python tools/_shot_card_attack.py

产物：`tools/out_custom_audit/attack/*.png`。临时项目，不碰任何真项目。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication                       # noqa: E402

OUT = ROOT / 'tools' / 'out_custom_audit' / 'attack'

RULES = {'statements': [
    # 一条"很多格"的语句：7 列 + 两个很长的默认值
    {'name': 'stage_effect', 'label': '舞台特效', 'columns': [
        {'name': 'layer', 'label': '图层', 'choices': ['master', 'screens']},
        {'name': 'effect', 'label': '效果',
         'choices': ['flash', 'shake', 'blur', 'vignette']},
        {'name': 'target', 'label': '对象'},
        {'name': 'duration', 'label': '时长'},
        {'name': 'delay', 'label': '延迟'},
        {'name': 'intensity', 'label': '强度'},
        {'name': 'note', 'label': '备注'}]},
    # 只有关键字（一格都没有）
    {'name': 'beat_pulse', 'label': '节拍点'},
    # 一个字面量块语句（头行 + 块体）
    {'name': 'mycue', 'label': '我的提示', 'pattern': [
        {'kind': 'keyword', 'text': 'mycue', 'column': '动作名'},
        {'kind': 'variable', 'column': '内容'},
        {'kind': 'optional', 'text': 'intensity', 'column': '强度'}]},
]}

SCRIPT = '''\
label ch(flag=True, who="凌跃捷"):
    "开场台词，带{color=#ff0000}红色{/color}与{i}斜体{/i}。"
    stage_effect layer=master effect=flash target="eileen happy" duration=1.5 delay=0.2 intensity=0.8 note="只在回忆里用，别和黑屏连用"
    beat_pulse
    mycue "闪电" intensity=3:
        target = ling
        $ renpy.pause(0.2)
    pause 1.0 hard
    with fade
    window hide
    play sound "se/hit.ogg" volume 0.5
    play music "bgm/a.ogg" fadein 2.0 if_changed
    "收尾"
'''


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from storygraph.ui_qt.main_window import MainWindowQt

    td = Path(tempfile.mkdtemp(prefix='shot-attack-'))
    root = td / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.rules.json').write_text(
        json.dumps(RULES, ensure_ascii=False, indent=2), encoding='utf-8')
    script = root / 'game' / 'ch.rpy'
    script.write_text(SCRIPT, encoding='utf-8')

    class _Engine:
        project_root = str(root)

        def get_registry(self):
            return None

    win = MainWindowQt(_Engine(), script)
    win.resize(1500, 980)
    win.show()
    app.processEvents()
    win._set_page(win.PAGE_CARD)
    OUT.mkdir(parents=True, exist_ok=True)

    print('beats:')
    for i, b in enumerate(win.beats):
        print(' ', i, b.kind, repr((b.text or '')[:20]),
              '| head=', repr(getattr(b, 'spec_head', '')[:30]),
              '| raw=', repr((b.raw or '')[:30]))

    for name, idx in {
        '01_dialogue_inline_tags': 0,
        '02_many_columns': 1,
        '03_keyword_only': 2,
        '04_custom_block': 3,
        '05_pause_and_with': 4,
        '06_audio_params': len(win.beats) - 1,
    }.items():
        win._show_index(idx)
        app.processEvents()
        app.processEvents()
        prev = win._panel._rich_preview
        print(f'  [{name}] preview visible={prev.isVisibleTo(win._panel)} '
              f'text={prev.text()[:60]!r}')
        png = OUT / f'{name}.png'
        win.grab().save(str(png))
        print('saved', png)
    win.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
