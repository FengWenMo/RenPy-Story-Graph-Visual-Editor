"""第二层判据：**隔离合成工程**（拼一个能编译的最小工程）。

为什么这一层重要：引擎**只要有一个 parse error 就停**，所以"项目里有未知写法"
这件事本身会让第一层（问注册表）与第三层（行为断言）**全跑不起来**。
隔离工程就是把"能编译的部分"单独拿出来问。

⚠ 但有一条**它也解决不了**的根本限制（真机实测 + 引擎源码确认）：
未知写法就是 parse error，而 `init python`（注册 CDS 的地方）属于**后面的
解析组** —— `script.py:447-452` 写明"一组出错，后面所有组都不解析"。
所以在隔离工程里问"一个**未知**写法"，答案仍然是"引擎编译不过"。
这一层真正管用的是：**在能编译的工程上做第一层/第三层**（例如查注册表、
跑行为断言），而不是"让未知写法变得可问"。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.pipeline.isolated_project import (                     # noqa: E402
    build_isolated, files_with_unknown_heads, rpy_files)


def _project(tmp_path):
    root = tmp_path / 'proj'
    (root / 'game' / 'images').mkdir(parents=True)
    (root / 'game' / 'chapter').mkdir(parents=True)
    (root / 'game' / 'a.rpy').write_text('label a:\n    return\n', encoding='utf-8')
    (root / 'game' / 'chapter' / 'b.rpy').write_text(
        'label b:\n    weather rain\n    return\n', encoding='utf-8')
    (root / 'game' / 'big.png').write_bytes(b'\x89PNG' + b'x' * 1000)
    (root / 'storygraph.rules.json').write_text('{}', encoding='utf-8')
    return root


def test_build_isolated_keeps_scripts_and_drops_heavy_files(tmp_path):
    root = _project(tmp_path)
    got = build_isolated(root, out_dir=tmp_path / 'out')
    assert got['ok'], got
    work = Path(got['root'])
    assert (work / 'game' / 'a.rpy').is_file()
    assert (work / 'game' / 'chapter' / 'b.rpy').is_file(), '嵌套目录也要保留'
    assert not (work / 'game' / 'big.png').exists(), '图像不该进隔离工程（磁盘）'
    assert (work / 'game' / 'images').is_dir(), '空目录也要建（引擎扫描要用）'


def test_build_isolated_excludes_what_we_ask_it_to(tmp_path):
    """`exclude` 就是"含未知写法、让引擎编译不过"的那些文件。"""
    root = _project(tmp_path)
    got = build_isolated(root, out_dir=tmp_path / 'out',
                         exclude=['game/chapter/b.rpy'])
    work = Path(got['root'])
    assert got['excluded'] == ['game/chapter/b.rpy'], got
    assert not (work / 'game' / 'chapter' / 'b.rpy').exists()
    assert (work / 'game' / 'a.rpy').is_file(), '别的文件要留着'


def test_build_isolated_refuses_a_non_project(tmp_path):
    got = build_isolated(tmp_path / 'nope')
    assert got['ok'] is False and '不是工程根' in got['hint']


def test_files_with_unknown_heads_finds_the_offenders(tmp_path):
    root = _project(tmp_path)
    got = files_with_unknown_heads(root, ['weather', 'sg_nope'])
    assert got == ['game/chapter/b.rpy'], got
    assert files_with_unknown_heads(root, []) == []


def test_rpy_files_lists_relative_paths(tmp_path):
    root = _project(tmp_path)
    got = rpy_files(root)
    assert 'game/a.rpy' in got and 'game/chapter/b.rpy' in got
    assert all(x.endswith('.rpy') for x in got)
