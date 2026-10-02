"""
tests/test_code_health.py
=========================
审计结论「错误处理靠压制」的守卫（用户 2026-10-05 拿来的那份审计表）：

    321 个 except 里 99 个 body 只有 pass，全项目 196 处 # noqa: BLE001

数字逐条核过：**属实**。`except: pass` 不都是错的（清理、Qt 控件已销毁、
可选缓存写不进去……这些本来只该忽略），但它有两个真实代价：

  1. 出事时**没有任何痕迹** —— 用户看到"功能没反应"，我们看不到原因；
  2. **真 bug 会被它吃掉** —— 审计顺手抓到的两条就在这一片里：
     `export_to_renpy` 把内部标记写进 .rpy、`default renpy = 0` 遮蔽引擎模块。

所以这一版把"什么都不说"换成 `safeguard.swallow(context, exc)`（**不删异常
处理**，行为不变），并在本文件锁两条不变量：

  1. `storygraph/**` 里**不许再有 body 只有 `pass` 的 except**
     （唯一豁免：`core/safeguard.py` 自己的记录失败路径 —— 再调一次自己
     就是无限递归，见那里的注释）；
  2. `swallow()` 真的记下来了（context / 次数 / 最后详情 / 报告文本）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / 'storygraph'

#: 允许保留 `except …: pass` 的文件（附理由）
ALLOWED_SILENT = {
    # `swallow` 自己就是错误处理路径上的东西，记录失败再调一次自己 = 递归
    'core/safeguard.py',
}


def _silent_handlers(path: Path) -> list:
    """这个文件里"body 只有 pass"的 except 处理器行号。"""
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = [b for b in node.body
                if not (isinstance(b, ast.Expr)
                        and isinstance(b.value, ast.Constant)
                        and isinstance(b.value.value, str))]
        if len(body) == 1 and isinstance(body[0], ast.Pass):
            out.append(node.lineno)
    return out


def test_no_silent_except_left_in_the_codebase():
    """
    全项目不许再有"吞掉异常且什么都不说"的地方。

    修完的形态是 `except Exception as e:  # noqa: BLE001 - 理由\n    swallow('模块.函数', e)`。
    """
    offenders = []
    for path in sorted(PKG.rglob('*.py')):
        rel = path.relative_to(PKG).as_posix()
        if rel in ALLOWED_SILENT:
            continue
        for line in _silent_handlers(path):
            offenders.append(f'{rel}:{line}')
    assert offenders == [], (
        '这些地方还在静默吞异常 —— 换成 '
        f'`safeguard.swallow("<模块.函数>", e)`：{offenders}')


def test_swallow_is_actually_used_everywhere():
    """
    反向守卫：`swallow` 得真的被广泛使用（防止有人把这次修改整体回退）。

    只数**调用点**，不数 import / 文档里的名字。
    """
    calls = 0
    for path in sorted(PKG.rglob('*.py')):
        if path.name == 'safeguard.py':
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and \
                    getattr(node.func, 'id', '') == 'swallow':
                calls += 1
    assert calls >= 90, f'只找到 {calls} 个 swallow 调用点（改动被回退了？）'


class TestSharedRulesBetweenGenerators:
    """
    审计结论「两套 .rpy 生成器，规则链完全独立」—— 属实。

    产物不同（`renpy_exporter` 写**控制流**、`beat_render` 写**章节内容**），
    所以不该强合成一个函数；但**共用约定**必须只有一份，否则就会像这次
    实测到的那样各错一半：

      · `beat_render` 认得行首 `\\x01`（紧凑 `$code`），`renpy_exporter`
        不认 → 产物里出现带控制字符的行；
      · `renpy_exporter` 对**所有出现过的名字**写 `default x = 0`，
        `beat_render` 从不管这事 → `default renpy = 0` 遮蔽引擎模块。

    本文件锁"共用规则只有一份"这条边界。
    """

    def test_exporter_reuses_the_shared_op_renderer(self):
        exporter = (PKG / 'pipeline/renpy_exporter.py').read_text(encoding='utf-8')
        assert 'render_code_block' in exporter, \
            '控制流导出又自己拼 `$ …` 了（约定会再次分叉）'

    def test_exporter_reuses_the_shared_default_variable_rule(self):
        exporter = (PKG / 'pipeline/renpy_exporter.py').read_text(encoding='utf-8')
        assert 'declared_variables_of_graph' in exporter
        # 只看**调用**形式：注释里提它是为了解释为什么不这么做
        assert 'collect_all_variables(' not in exporter, \
            '"出现过的名字"不能拿来写 default（会写出 default renpy = 0）'

    def test_marker_is_hardcoded_in_exactly_one_place(self):
        """标记（`\\x01`）只许在 `core/statement.py` 里硬编码。"""
        offenders = []
        for path in sorted(PKG.rglob('*.py')):
            if path.name == 'statement.py':
                continue
            src = path.read_text(encoding='utf-8')
            if "'\\x01'" in src or '"\\x01"' in src:
                offenders.append(path.relative_to(PKG).as_posix())
        assert offenders == [], f'这些文件又硬编码了标记：{offenders}'


class TestSafeguardHelper:
    def setup_method(self):
        from storygraph.core import safeguard
        safeguard.clear()
        safeguard.set_debug(False)

    def teardown_method(self):
        from storygraph.core import safeguard
        safeguard.clear()
        safeguard.set_debug(None)

    def test_records_context_count_and_detail(self):
        from storygraph.core import safeguard
        safeguard.swallow('demo.one', ValueError('坏了'))
        safeguard.swallow('demo.one', ValueError('又坏了'))
        safeguard.swallow('demo.two')
        assert safeguard.total() == 3
        got = dict((c, n) for c, n, _last in safeguard.recent())
        assert got == {'demo.one': 2, 'demo.two': 1}
        assert safeguard.recent()[-2][2] == 'ValueError: 又坏了'
        assert set(safeguard.contexts()) == {'demo.one', 'demo.two'}

    def test_report_is_readable_and_empty_when_clean(self):
        from storygraph.core import safeguard
        assert safeguard.report() == ''
        safeguard.swallow('demo.one', RuntimeError('炸了'))
        text = safeguard.report()
        assert '静默失败 1 次' in text and 'demo.one' in text and '炸了' in text

    def test_never_raises_even_with_garbage(self):
        from storygraph.core import safeguard
        safeguard.swallow(None, None)
        safeguard.swallow('x', object())
        assert safeguard.total() == 2

    def test_buffer_is_bounded(self, monkeypatch):
        from storygraph.core import safeguard
        monkeypatch.setattr(safeguard, 'MAX_CONTEXTS', 5)
        for i in range(20):
            safeguard.swallow(f'demo.{i}')
        assert len(safeguard.contexts()) == 5
        # 留下的是**最近**的
        assert safeguard.contexts()[-1] == 'demo.19'

    def test_debug_prints_to_stderr(self, capsys):
        from storygraph.core import safeguard
        safeguard.set_debug(True)
        safeguard.swallow('demo.one', ValueError('x'))
        assert 'demo.one' in capsys.readouterr().err
        safeguard.set_debug(False)
        safeguard.swallow('demo.two', ValueError('y'))
        assert 'demo.two' not in capsys.readouterr().err
