"""
项目钩子的信任边界（调研 P1-4）
==============================

`storygraph.host.py` 是用 `exec` 跑的。命名空间里没有工具内部对象，但
Python 会自动注入 `__builtins__` —— 项目代码可以 `import os`、读写文件、
发网络请求。`unknown_statements()` 扫描、MCP 的 `host_validate` 都会走到它。
只要存在"打开别人发来的项目 / 让 AI 分析一个下载的工程"，这就是
**任意代码执行**入口。

所以现在**默认不执行**；授权三选一（显式参数 / 环境变量 / 项目
`storygraph.rules.json` 的 `host_hooks.trust`）。能力清单里它也必须标成
`execute`，不能装成只读的 `judge`。

这个文件守：默认拒绝且不留执行痕迹；三种授权都真的生效；
未授权与"根本没有钩子"能分清（前者 `denied=True`，报告要说实话）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story.host_hooks import (TRUST_ENV,           # noqa: E402
                                         load_host_hooks)


def _project(tmp_path, marker: Path):
    """造一个带钩子的项目；钩子一被 exec 就写 marker 文件。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.host.py').write_text(
        'from pathlib import Path\n'
        f'Path({str(marker)!r}).write_text("executed", encoding="utf-8")\n'
        'def declarations():\n'
        '    return [{"name": "weather"}]\n'
        'def validate_statement(head, text):\n'
        '    return (True, "") if head == "weather" else None\n',
        encoding='utf-8')
    return root


def test_default_is_deny(tmp_path, monkeypatch):
    """没授权 → **一个字节的项目代码都不执行**，而且要说得清怎么授权。"""
    monkeypatch.delenv(TRUST_ENV, raising=False)
    marker = tmp_path / 'executed.txt'
    root = _project(tmp_path, marker)
    hooks = load_host_hooks(root)
    assert not marker.exists(), '未授权却执行了项目代码（任意代码执行）'
    assert hooks.available is False
    assert hooks.denied is True
    assert any('storygraph.rules.json' in n for n in hooks.notes), hooks.notes
    assert hooks.declarations() == []


def test_explicit_trust_executes(tmp_path, monkeypatch):
    monkeypatch.delenv(TRUST_ENV, raising=False)
    marker = tmp_path / 'executed.txt'
    root = _project(tmp_path, marker)
    hooks = load_host_hooks(root, trust=True)
    assert marker.exists()
    assert hooks.available is True and hooks.denied is False
    assert hooks.declarations() == [{'name': 'weather'}]


def test_project_manifest_can_authorize(tmp_path, monkeypatch):
    """
    项目级授权写在 `storygraph.rules.json`（可评审、可 diff）——
    这是推荐方式：换台机器打开同一个工程，授权跟着工程走。
    """
    monkeypatch.delenv(TRUST_ENV, raising=False)
    marker = tmp_path / 'executed.txt'
    root = _project(tmp_path, marker)
    (root / 'storygraph.rules.json').write_text(
        json.dumps({'host_hooks': {'trust': True}}), encoding='utf-8')
    hooks = load_host_hooks(root)
    assert marker.exists()
    assert hooks.available is True


def test_env_var_can_authorize(tmp_path, monkeypatch):
    monkeypatch.setenv(TRUST_ENV, '1')
    marker = tmp_path / 'executed.txt'
    root = _project(tmp_path, marker)
    hooks = load_host_hooks(root)
    assert marker.exists()
    assert hooks.available is True


def test_manifest_false_beats_the_default(tmp_path, monkeypatch):
    """显式 `trust: false` 与默认一致，但理由要说"来自清单"。"""
    monkeypatch.delenv(TRUST_ENV, raising=False)
    marker = tmp_path / 'executed.txt'
    root = _project(tmp_path, marker)
    (root / 'storygraph.rules.json').write_text(
        json.dumps({'host_hooks': {'trust': False}}), encoding='utf-8')
    hooks = load_host_hooks(root)
    assert not marker.exists()
    assert hooks.denied is True
    assert any('host_hooks.trust' in n for n in hooks.notes), hooks.notes


def test_the_capability_is_marked_as_execute():
    """
    能力清单不许把它写成 `judge`（"只是判断一下"）——
    它执行项目代码，必须标 `execute` 并写清"任意代码执行"。
    """
    from storygraph import mcp_surface as M

    cap = next(t for t in M.capabilities()['tools']
               if t['name'] == 'storygraph_host_validate')
    assert cap['kind'] == 'execute', cap
    blob = ' '.join(cap['known_limits'])
    assert '任意代码执行' in blob and 'trust' in blob, blob


def test_scanner_also_obeys_the_policy(tmp_path, monkeypatch):
    """
    扫描（`unknown_statements`）不是"只读所以随便跑"：
    默认不执行；显式信任后才用钩子的声明。
    """
    monkeypatch.delenv(TRUST_ENV, raising=False)
    marker = tmp_path / 'executed.txt'
    root = _project(tmp_path, marker)
    (root / 'game' / 'ch.rpy').write_text(
        'label ch01:\n    weather rain 强度=3\n    return\n', encoding='utf-8')

    from storygraph.story.project_conventions import unknown_statements
    from storygraph.story.statement_registry import clear_cache

    clear_cache()
    default = unknown_statements(root)
    assert not marker.exists()
    assert 'weather' in default, '未授权时应当按"没有钩子"处理'

    clear_cache()
    trusted = unknown_statements(root, trust=True)
    assert marker.exists()
    assert 'weather' not in trusted
