# -*- coding: utf-8 -*-
"""
控制流导出**不许把章节文件里的代码搬过来**（2026-10-28，用户的 P0）。

现场：用户在编辑器里导出了控制流，产物 484 行里有 209 条
`renpy.transition(...)`、28 条 `msg_add(...)`、10 条 `persistent.lock_lst[…]`
—— 全部是从 `chapter1_*.rpy` **原样搬过来的**，而且被塞在 `call chapter1_1`
**之前**，原来"夹在两句台词之间"的位置没了。

用户的原话：

> 你把它擅自搬过来了会不会把 1_4 这些原本的文件毁掉？？？
> 不该擅自搬代码的……不然也没有作用！本来在那里的就先放着吧……

判据（`renpy_exporter._lives_in_its_source_file`）：

  · 来源是**别的 .rpy**、那个文件还在、**第 N 行还是这句代码** → 不写；
  · 人改过 / 删过那一行 → 照写（宁可重复，也不许把代码吞掉）；
  · 来源就是**目标文件本身** → 照写（否则等于把文件内容删掉）。

以及一条配套的：老产物里那些搬过去的行，重导出时不该再被当成"手写内容"
拦一道（`control_write.find_manual_lines(also_allowed=…)`）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ui_project import build_project  # noqa: E402

from storygraph.app.editor import EditorSession  # noqa: E402
from storygraph.graph.models import Graph  # noqa: E402
from storygraph.pipeline.renpy_exporter import (  # noqa: E402
    imported_operation_lines, render_control_text)
from storygraph.story.control_write import plan_control_write  # noqa: E402

CODE = 'renpy.transition(Dissolve(0.1), layer="master")'


def _write_chapter(root, name, *, keep_code=True):
    body = [
        f'label {name}:',
        f'    e "{name} 第一句"',
    ]
    if keep_code:
        body.append(f'    $ {CODE}')
    body += [
        f'    e "{name} 第二句"',
        '    return',
        '',
    ]
    p = root / 'game' / 'script' / f'{name}.rpy'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('\n'.join(body), encoding='utf-8')
    return p


def _import(root, path):
    session = EditorSession(project_root=root)
    res = session.import_chapters([path], Graph())
    assert res['ok'], res.get('error')
    return session, res['graph']


@pytest.fixture
def project(tmp_path):
    return build_project(tmp_path / 'proj')


def test_imported_code_is_not_copied_into_control(project, tmp_path):
    chapter = _write_chapter(project, 'ch01')
    before = chapter.read_bytes()
    _session, graph = _import(project, chapter)

    out = tmp_path / 'controller.rpy'
    text = render_control_text(graph, wrapper=True, out_path=out)

    assert 'call ch01' in text
    assert CODE not in text, '章节文件里的代码被搬进控制流了'
    assert chapter.read_bytes() == before, '导出不许动章节文件一个字节'


def test_code_the_author_removed_is_still_exported(project, tmp_path):
    """人在章节文件里把那一行删了 → 图里那句是**唯一**一份，必须照写。"""
    chapter = _write_chapter(project, 'ch01')
    _session, graph = _import(project, chapter)
    _write_chapter(project, 'ch01', keep_code=False)   # 外部编辑：删掉那行

    out = tmp_path / 'controller.rpy'
    text = render_control_text(graph, wrapper=True, out_path=out)
    assert CODE in text


def test_code_whose_source_file_is_the_target_is_still_written(project,
                                                               tmp_path):
    """控制流图导回它自己（`--out` == 来源文件）时不能把内容整段吞掉。"""
    chapter = _write_chapter(project, 'ch01')
    _session, graph = _import(project, chapter)

    # 模拟"来源就是目标"：把语句的来源指向目标文件，并让那一行真的在那
    node = next(iter(graph.nodes.values()))
    stmt = next(s for s in node.statements
                if getattr(s, 'payload', {}).get('code') == CODE)
    target = tmp_path / 'self.rpy'
    target.write_text(f'label ch01:\n    $ {CODE}\n', encoding='utf-8')
    stmt.source = (str(target), 2)

    text = render_control_text(graph, wrapper=True, out_path=target)
    assert CODE in text


def test_old_hoisted_lines_are_not_treated_as_manual(project, tmp_path):
    """
    上一版工具搬过去的行，重导出时不许拦成"手写内容"。

    这就是用户"只能把原来的删掉再导"的一半原因：老产物里那些 `$` 看着像
    人写的，其实是我们自己写的。
    """
    chapter = _write_chapter(project, 'ch01')
    _session, graph = _import(project, chapter)

    target = tmp_path / 'controller.rpy'
    old = ('label ch01_control:\n'
           f'    $ {CODE}\n'
           '    call ch01\n'
           '    return\n')
    target.write_text(old, encoding='utf-8')

    plan = plan_control_write(project, graph, target, wrapper=True)
    assert plan.manual_lines == [], plan.manual_lines
    assert plan.ok is True, plan.blocking


def test_a_real_handwritten_line_is_still_refused(project, tmp_path):
    """对照：真正人加的那行（不在任何章节文件里）照旧要拦。"""
    chapter = _write_chapter(project, 'ch01')
    _session, graph = _import(project, chapter)

    target = tmp_path / 'controller.rpy'
    target.write_text('label ch01_control:\n'
                      '    $ persistent.saw_ch1 = True\n'
                      '    call ch01\n'
                      '    return\n', encoding='utf-8')

    plan = plan_control_write(project, graph, target, wrapper=True)
    assert plan.manual_lines, '人手加的行必须被看见'
    assert plan.forceable is True


def test_imported_lines_helper_only_lists_what_lives_in_files(project,
                                                              tmp_path):
    chapter = _write_chapter(project, 'ch01')
    _session, graph = _import(project, chapter)
    out = tmp_path / 'controller.rpy'
    assert f'$ {CODE}' in imported_operation_lines(graph, out)
    # 来源就是目标时它们**会**被写 → 不算"被跳过"
    assert not imported_operation_lines(graph, chapter)
