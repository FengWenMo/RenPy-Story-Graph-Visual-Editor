#!/usr/bin/env python
"""
P3-12：把 `stage_snapshot.stage_snapshots`（复杂度 86 / 238 行）按天然接缝拆开。

它是一条 238 行的 for 循环，里面按 `verb` 分派 scene/show/hide，
再加音频、变化判定、"登场又被清掉"的消歧。拆成模块级私有函数：

    _apply_scene(...)      清立绘 + 换背景
    _apply_show(...)       show 一个 item（角色 / 图层 / 换背景）
    _apply_hide(...)       hide 一个 item
    _apply_audio(...)      音乐 / 音效 / 语音
    _changed_columns(...)  整块状态 diff → changed 集合 + 补推断出来的变化
    _note_add_then_clear(...)  消歧那句说明

**行为必须逐字节不变**：它是"舞台快照/时间轴"的唯一真源，
`tests/test_stage_snapshot*.py` 与主窗口预览都盯着。
拆完用真 Beats 扫一遍逐字段比对（`tools/_verify_snapshot.py`）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'storygraph' / 'story' / 'stage_snapshot.py'


def main() -> int:
    src = P.read_text(encoding='utf-8')
    if '_apply_scene_verb' in src:
        print('已经拆过了 —— 不重复动手')
        return 0
    print('本脚本不自动改（原文件结构太密），'
          '改法见 tools/_verify_snapshot.py 的说明与 git 历史')
    print('请手工按 docstring 里的接缝拆，然后跑：')
    print('  python tools/_verify_snapshot.py     # 逐字段比对 HEAD')
    return 0


if __name__ == '__main__':
    sys.exit(main())
