"""
契约守卫：**门面不许把私有实现固化成公共契约**（风险清单 P2-7）
==============================================================

清单那条："`script_table` re-export 10 个下划线函数（`_hydrate_audio` 等），
私有实现被固化为公共契约"。

复验结论（2026-10-05）：数量其实远不止 10 个 —— `script_table` 从
`beat_model` / `beat_render` 等模块 re-export 了 **92 个下划线名**。
但要去掉它们之前先看清"谁在用"：**外部通过门面真正用到的只有 2 个**
（`_assign_identity`、`_looks_like_system_name`，都在测试里）。

所以真正的问题不是"90 个会被人误用"，而是**"公共 API 没有声明"**：
`script_table` 连 `__all__` 都没有，于是

  · `from storygraph.story.script_table import *` 会把 92 个私有名一起拖出来；
  · IDE / 文档工具无法区分"这是给外部用的"与"这是实现细节"；
  · 新增一个内部 helper 会自动变成"对外契约"，没人会注意到。

这个文件把"公共面"钉住：门面必须有显式 `__all__`，且里面**不许**有下划线名。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FACADE = REPO / 'storygraph' / 'story' / 'script_table.py'


def _facade_public_names() -> set:
    """门面 `__all__` 里声明的名字。"""
    tree = ast.parse(FACADE.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == '__all__':
                    return {e.value for e in node.value.elts
                            if isinstance(e, ast.Constant)}
    return set()


def _reexported_private_names() -> set:
    """门面从别处 re-export 进来的下划线名。"""
    tree = ast.parse(FACADE.read_text(encoding='utf-8'))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name.startswith('_') and not a.name.startswith('__'):
                    out.add(a.name)
    return out


def test_facade_declares_its_public_surface():
    """
    门面必须有**显式** `__all__`。

    没有它，"对外契约"就是"这个模块恰好 import 了什么" —— 加一个内部
    helper 会无声地变成公共 API。
    """
    declared = _facade_public_names()
    assert declared, (
        '`storygraph/story/script_table.py` 没有 `__all__` —— 它的公共面'
        '就是它 import 的全部名字（含 90+ 个私有 helper），'
        '新增内部实现会自动变成对外契约。')


def test_public_surface_has_no_private_names():
    """`__all__` 里不许出现下划线名 —— 那是实现细节，不是契约。"""
    declared = _facade_public_names()
    bad = sorted(n for n in declared if n.startswith('_'))
    assert not bad, f'门面的 `__all__` 里混进了私有名：{bad}'


def test_private_reexports_are_not_used_as_public_api():
    """
    私有 re-export **保留**（同层模块靠门面拿共享 helper，是刻意的单一
    来源），但**不许有人把它当公共 API 用**。

    判据不是"re-export 了多少私有名"（那有 90+，删掉会打断同层模块），
    而是"**外部**从门面 import 了多少私有名"。实测只有 2 个，都是测试；
    这一条盯着别让它长起来。
    """
    private = _reexported_private_names()
    if not private:
        return
    used = set()
    for path in list((REPO / 'tests').rglob('*.py')) + \
            list((REPO / 'storygraph').rglob('*.py')) + \
            list((REPO / 'tools').rglob('*.py')):
        if path.name == 'script_table.py':
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and \
                    (node.module or '').endswith('script_table'):
                for a in node.names:
                    if a.name in private:
                        used.add(a.name)
    assert len(used) <= 3, (
        f'有 {len(used)} 个私有名被外部从门面 import：{sorted(used)}\n'
        '门面的私有 re-export 是给**同层模块**拿共享 helper 用的，'
        '不该被当成公共 API。要公开就给它一个正式名字（去掉下划线）。')


@pytest.mark.parametrize('name', ['Beat', 'render_beats_to_rpy',
                                  'export_beats_to_excel',
                                  'import_beats_from_excel',
                                  'beats_from_parse_result',
                                  'beats_from_statements',
                                  'write_generation',
                                  'evaluate_generation'])
def test_core_facade_names_are_declared(name):
    """
    真正在用的门面名必须出现在 `__all__` 里。

    否则"声明"与"实际公共面"又对不上了 —— 只是换了个方向。
    """
    declared = _facade_public_names()
    if not declared:
        pytest.skip('门面还没有 __all__（由声明那条测试报）')
    assert name in declared, (
        f'`{name}` 是门面的公共名之一，但没写进 `__all__`：'
        f'这会让它从 `import *` 里消失。')


def test_facade_public_names_actually_exist():
    """`__all__` 里的名字必须真的能从门面拿到（防拼错 / 防删漏）。"""
    declared = _facade_public_names()
    if not declared:
        pytest.skip('门面还没有 __all__')
    import storygraph.story.script_table as st
    missing = sorted(n for n in declared if not hasattr(st, n))
    assert not missing, f'`__all__` 里这些名字门面里没有：{missing}'
