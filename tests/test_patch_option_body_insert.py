"""
tests/test_patch_option_body_insert.py
======================================
**在菜单选项体里插一张卡**——补丁写回的落点必须和模型说的一致。

2026-10-23 实测（真机形状，见 `.pip-tmp/probe_option_body2.py`）：

  卡片里选中选项头 `"去屋顶":` → 插一张控制流卡 `call roof`
  → 模型把这一拍放进选项体（`nest_path == 'menu:1>A'`），
    全量渲染也落在选项体里：

        "去屋顶":
            call roof
            e "上去了"

  → 但 `patch_export` 写出来的是：

        "去屋顶":
            e "上去了"
        "留下":
            e "留下了"
    call roof              ← 跑到 menu 之外、而且是选项同级
    e "结束"

`_insert_anchor` 的规矩是"锚点是块头就退到最外层包围块之后"（2026-10-05，
为了让产物语法合法）。这对"选项体的兄弟卡"是对的，对"**放进选项体**的卡"
是错的：它把 `call roof` 从分支里挪了出来 —— 语法合法，语义已经改了，
而 `patch_export` 报的是 `ok=True, reasons=[]`。

判据（本条测试）：写回的那一行**在下一个选项之前**，且比选项头更深。
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from storygraph.story.beat_render import render_beats_to_rpy  # noqa: E402
from storygraph.story.patcher import patch_export            # noqa: E402

SRC = '''label main:
    e "开始"
    menu:
        "去屋顶":
            e "上去了"
        "留下":
            e "留下了"
    e "结束"
    return

label roof:
    e "屋顶"
    return
'''


def _project(tmp_path):
    proj = tmp_path / 'proj'
    (proj / 'game' / 'script').mkdir(parents=True)
    fp = proj / 'game' / 'script' / 'm.rpy'
    fp.write_text(SRC, encoding='utf-8')
    (proj / 'game' / 'options.rpy').write_text('define config.name = "x"\n',
                                               encoding='utf-8')
    return proj, fp


def _indent(line):
    return len(line) - len(line.lstrip())


def _insert_after_option_head(fp, proj):
    from storygraph.app.editor import EditorSession
    s = EditorSession(project_root=proj)
    s.load(fp)
    head = next(i for i, b in enumerate(s.beats)
                if b.kind == 'menu_option' and '屋顶' in (b.text or ''))
    res = s.insert_flow_beat(head, after=True, flow_type='call',
                             target='roof')
    assert res['ok'], res
    return s, res


def test_flow_card_after_an_option_head_lands_inside_that_option(tmp_path):
    proj, fp = _project(tmp_path)
    s, _res = _insert_after_option_head(fp, proj)
    new = s.beats[3]
    assert getattr(new, 'nest_path', '') == 'menu:1>A', '前置条件：模型把它放进选项体'

    out = patch_export(list(s.beats), fp, render_beats_to_rpy(s.beats))
    assert out.ok, out.reasons
    lines = out.text.splitlines()
    i = next(k for k, ln in enumerate(lines) if 'call roof' in ln)
    head = next(k for k, ln in enumerate(lines) if '去屋顶' in ln)
    nxt = next(k for k, ln in enumerate(lines) if '留下' in ln)
    assert i < nxt, f'被挪到了下一个选项之后：{lines[max(0, i - 4):i + 2]}'
    assert _indent(lines[i]) > _indent(lines[head]), (
        f'写成了选项同级（选项头 {_indent(lines[head])}，这一行 '
        f'{_indent(lines[i])}）：{lines[max(0, i - 4):i + 2]}')


def test_the_other_option_stays_untouched(tmp_path):
    proj, fp = _project(tmp_path)
    s, _res = _insert_after_option_head(fp, proj)
    out = patch_export(list(s.beats), fp, render_beats_to_rpy(s.beats))
    assert out.ok, out.reasons
    lines = out.text.splitlines()
    i = next(k for k, ln in enumerate(lines) if '留下' in ln)
    assert lines[i + 1].strip() == 'e "留下了"', lines[i:i + 3]
