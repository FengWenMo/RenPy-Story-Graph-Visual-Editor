# -*- coding: utf-8 -*-
"""
"导出只能先删掉再导" —— 目标是手写 `.rpy` 时的**带备份覆盖**（2026-10-28）。

用户原话：

> 另外我导出控制流时只能把原来的删掉再导……连替换都被拦下了……

护栏（`HANDWRITTEN_TARGET`）本身是对的：全量重生成只认得图里的东西，
覆盖手写文件会静默删掉 `default` / `define` / 手写 label。问题是没有正路
—— 用户被逼去资源管理器里删文件，那一步**没有任何备份**。

这里钉住：

  · 默认仍然拒写，并且把 `HANDWRITTEN_TARGET` 带回去（界面才能问）；
  · `force_overwrite=True` 时先备份成 `<名>.bak` 再写；
  · 备份是**旧内容**，写完之后目标文件是**新产物**；
  · 它只越过"手写目标"这一条 —— 产物自己坏掉（比如 label 重名）照样拦。
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

OLD = ('label ch01_control:\n'
       '    call ch01\n'
       '    return\n')


@pytest.fixture
def session_and_graph(tmp_path):
    root = build_project(tmp_path / 'proj')
    chapter = root / 'game' / 'script' / 'ch01.rpy'
    chapter.parent.mkdir(parents=True, exist_ok=True)
    chapter.write_text('label ch01:\n    e "台词"\n    return\n',
                       encoding='utf-8')
    session = EditorSession(project_root=root)
    res = session.import_chapters([chapter], Graph())
    assert res['ok'], res.get('error')
    return session, res['graph'], tmp_path / 'controller.rpy'


def test_handwritten_target_is_refused_by_default(session_and_graph):
    session, graph, target = session_and_graph
    target.write_text(OLD, encoding='utf-8')

    res = session.export_control_flow(graph, target, wrapper=True)

    assert res['ok'] is False
    assert 'HANDWRITTEN_TARGET' in (res.get('codes') or []), res
    assert target.read_text(encoding='utf-8') == OLD, '拒写时一个字节都不许动'


def test_force_overwrite_backs_up_then_writes(session_and_graph):
    session, graph, target = session_and_graph
    target.write_text(OLD, encoding='utf-8')

    res = session.export_control_flow(graph, target, wrapper=True,
                                      force_overwrite=True)

    assert res['ok'] is True, res.get('error')
    backup = Path(res['backup'])
    assert backup.is_file(), '必须先留一份备份'
    assert backup.read_text(encoding='utf-8') == OLD, '备份要存旧内容'
    text = target.read_text(encoding='utf-8')
    assert 'label ch01_control:' in text
    assert '由剧本表自动生成' in text.splitlines()[0], '新文件要带生成标记'


def test_second_forced_overwrite_does_not_clobber_the_first_backup(
        session_and_graph):
    session, graph, target = session_and_graph
    target.write_text(OLD, encoding='utf-8')
    assert session.export_control_flow(graph, target, wrapper=True,
                                       force_overwrite=True)['ok']
    assert session.export_control_flow(graph, target, wrapper=True,
                                       force_overwrite=True)['ok']

    bak = Path(str(target) + '.bak')
    bak1 = Path(str(target) + '.1.bak')
    assert bak.read_text(encoding='utf-8') == OLD
    assert bak1.is_file()


def test_force_does_not_bypass_label_collision(session_and_graph):
    """产物里的 label 和项目里别的文件撞了 —— force 也不许写（那会起不来）。"""
    session, graph, _tmp = session_and_graph
    # 必须落在 game/ 里：只有会被 Ren'Py 扫描的产物才查"和原文抢 label"
    target = session.project_root / 'game' / 'chapter' / 'controller.rpy'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(OLD, encoding='utf-8')
    other = session.project_root / 'game' / 'script' / 'other.rpy'
    other.write_text('label ch01_control:\n    return\n', encoding='utf-8')

    res = session.export_control_flow(graph, target, wrapper=True,
                                      force_overwrite=True)

    assert res['ok'] is False, res
    assert 'LABEL_COLLISION' in (res.get('codes') or []), res.get('codes')
    assert target.read_text(encoding='utf-8') == OLD
