"""截图：语句拍（声音 / 文字）在**卡片编辑器**里长什么样。

    python tools/_shot_card_statements.py

产物：`tools/out_custom_audit/card7/*.png`。造的是**临时项目**，不碰任何真项目。
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

OUT = ROOT / 'tools' / 'out_custom_audit' / 'card7'

RULES = {'statements': [
    {'name': 'play_music', 'label': '播放音乐', 'columns': [
        {'name': 'track', 'label': '音轨', 'choices': ['bgm', 'bgm2', 'voice']},
        {'name': 'file', 'label': '文件'},
        {'name': 'fade', 'label': '淡入秒数'}]},
    {'name': 'sfx_mix', 'label': '多轨音效', 'pattern': [
        {'kind': 'keyword', 'text': 'sfx_mix', 'column': '动作名'},
        {'kind': 'choice', 'column': '声道', 'choices': ['se', 'se2', 'amb'],
         'multi': True},
        {'kind': 'variable', 'column': '文件'},
        {'kind': 'optional', 'text': 'gain', 'column': '音量'}]},
    {'name': 'styled', 'label': '花字', 'pattern': [
        {'kind': 'keyword', 'text': 'styled', 'column': '动作名'},
        {'kind': 'variable', 'column': '文字'},
        {'kind': 'variable', 'column': '颜色'},
        {'kind': 'variable', 'column': '字号'}]},
]}

SCRIPT = '''\
label ch:
    "开场"
    play_music track=bgm file="bgm/theme.ogg" fade=1.5
    sfx_mix se se2 "se/a.ogg" gain=-6
    styled "他说\\"你好\\"" "#ff0000" 32
    play music "bgm/a.ogg" fadeout 1.0
    play ambience "amb/wind.ogg"
    play sound "se/hit.ogg"
    "收尾"
'''


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from storygraph.ui_qt.main_window import MainWindowQt

    td = Path(tempfile.mkdtemp(prefix='shot-card7-'))
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

    shots = {
        '01_dialogue': 0,                 # 普通台词拍（对照）
        '02_play_music': 1,               # 声音：具名参数
        '03_sfx_mix': 2,                  # 声音：多音轨 + 多值格
        '04_styled': 3,                   # 文字：花字（颜色/字号）
        '05_audio_mix': len(win.beats) - 1,   # 多音轨 + 原样行
    }
    for name, idx in shots.items():
        win._show_index(idx)
        app.processEvents()
        app.processEvents()
        png = OUT / f'{name}.png'
        win.grab().save(str(png))
        print('saved', png)

    # 语句拍上把舞台点开（对照"收起来 / 打开"）
    win._show_index(3)
    win._panel._stage_toggle.click()
    app.processEvents()
    win.grab().save(str(OUT / '06_styled_stage_open.png'))
    print('saved', OUT / '06_styled_stage_open.png')
    win.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
