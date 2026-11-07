"""
L1 加固：**兼容读取阶梯** + 失败回退静态（2026-11-05）
====================================================

用户原话：

> 「如果引擎读不到或者版本不对应的话，尝试一下兼容地读取，如果还是失败……
> 不要弹窗，直接在控制台界面说失败，回退回静态解析就好。」

## 三件事，三组判据

| 组 | 钉什么 |
|---|---|
| `TestLadder` | 命令阶梯：全 → 简；版本太老就不试带 dump 的那一档 |
| `TestRejectDetection` | **只有"引擎不认旗"才退阶**，项目有语法错不许退（白跑一轮） |
| `TestFallbackToStatic` | 两档都失败 → 不抛、不弹窗、档位落到 R2 |

## 为什么"退阶"这条特别容易做错

第一版想法是"exit code 非 0 就退阶"。**错了**：项目有语法错时 lint 的
exit code 是 1，那是**正常结果**（我们正需要那些诊断）。按 exit code 退阶会
把每一次"项目有错"都变成"再跑一遍最简 lint"—— 真机多花几十秒，还什么都
没多拿到。判据必须是**引擎不认这面旗**。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.engine import compat as C          # noqa: E402
from storygraph.engine import decide               # noqa: E402
from storygraph.engine.detect import parse_version  # noqa: E402


class TestLadder:
    def test_unknown_version_tries_full_then_bare(self):
        ladder = C.lint_ladder(None, 'dump.json')
        assert len(ladder) == 2
        assert ladder[0][0] == 'lint' and '--json-dump' in ladder[0]
        assert ladder[1] == ['lint'], '最简档必须是没有旗的 lint'

    def test_modern_version_tries_full_first(self):
        ladder = C.lint_ladder(parse_version('8.5.3'), 'dump.json')
        assert '--error-code' in ladder[0]
        assert '--json-dump' in ladder[0]

    def test_old_version_skips_the_dump_tier(self):
        """7.4 之前没有 `--json-dump` —— 别浪费一轮去试。"""
        ladder = C.lint_ladder(parse_version('7.3.0'), 'dump.json')
        assert ladder == [['lint']]

    def test_no_dump_path_means_no_dump_flag(self):
        ladder = C.lint_ladder(parse_version('8.5.3'), '')
        assert all('--json-dump' not in argv for argv in ladder)

    def test_ladder_is_always_ordered_rich_to_plain(self):
        for ver in (None, parse_version('8.5.3'), parse_version('6.0')):
            ladder = C.lint_ladder(ver, 'x.json')
            assert ladder[-1] == ['lint'], ver

    def test_accepts_a_string_version_without_crashing(self):
        """传了个字符串（不是 EngineVersion）→ 当未知处理，不给错。"""
        assert len(C.lint_ladder('8.5.3', 'x.json')) == 2


class TestRejectDetection:
    @pytest.mark.parametrize('text', [
        'error: unrecognized arguments: --json-dump',
        'renpy.py: error: no such option: --error-code',
        'invalid option: --json-dump',
        'unknown option --error-code',
    ])
    def test_flags_rejection_is_detected(self, text):
        assert C.reject_reason(text)

    @pytest.mark.parametrize('text', [
        '', 'File "game/x.rpy", line 3: expected statement',
        'error: 项目里有个语法错',                    # 有 error 但不是"不认旗"
        'The game contains 0 menus',
    ])
    def test_project_errors_are_not_rejections(self, text):
        """⭐ 项目自己的 error 不许触发退阶（否则每次有错都多跑一轮）。"""
        assert C.reject_reason(text) == ''

    def test_describe_ladder(self):
        text = C.describe_ladder(C.lint_ladder(None, 'd.json'))
        assert '→' in text and '--json-dump' in text


class TestLadderRunner:
    """`_run_lint_ladder`：第一档被拒 → 退第二档；两档都拒 → 原样返回。"""

    def _run(self, monkeypatch, replies):
        from storygraph.pipeline import engine_collect as EC
        calls = []

        def fake_run_engine(sdk, work, argv, timeout=None):
            calls.append(list(argv))
            return replies[len(calls) - 1]

        monkeypatch.setattr(EC, 'run_engine', fake_run_engine)
        monkeypatch.setattr(EC, '_engine_version', lambda _r: '8.5.3.1')
        rc, out, used = EC._run_lint_ladder(Path('sdk'), Path('work'),
                                             Path('dump.json'), 10)
        return rc, out, used, calls

    def test_first_tier_succeeds(self, monkeypatch):
        rc, out, used, calls = self._run(
            monkeypatch, [(0, 'lint ok: 0 errors')])
        assert len(calls) == 1
        assert '--json-dump' in used

    def test_falls_back_when_flags_are_rejected(self, monkeypatch):
        rc, out, used, calls = self._run(monkeypatch, [
            (1, 'error: unrecognized arguments: --json-dump'),
            (0, 'lint ok'),
        ])
        assert len(calls) == 2, calls
        assert used == ['lint'], f'没退到最简档：{used}'
        assert rc == 0

    def test_project_error_does_not_retry(self, monkeypatch):
        """⭐ 语法错（exit 1 + 正常诊断）**不许**触发第二轮。"""
        rc, out, used, calls = self._run(
            monkeypatch, [(1, 'File "a.rpy", line 2: expected statement')])
        assert len(calls) == 1, f'项目有错却重试了：{calls}'
        assert rc == 1

    def test_both_tiers_rejected_returns_last(self, monkeypatch):
        rc, out, used, calls = self._run(monkeypatch, [
            (-1, 'boom'),
            (-1, 'boom again'),
        ])
        assert len(calls) == 2
        assert rc == -1 and used == ['lint']


class TestFallbackToStatic:
    def test_failed_collection_lands_on_r2(self):
        """采不到 → **静态仍在**（R2），不是 R4、更不是"没问题"。"""
        facts = {'schema': 2,
                 'capabilities': {'CAP_LABELS': None},
                 'degraded': {'reason': 'probe_failed',
                              'missing': ['CAP_LABELS']}}
        assert decide.rung(facts, collectable=False) == decide.R2
        ok, miss = decide.allows(decide.R2, ['CAP_LABELS'])
        assert not ok and miss

    def test_failed_collection_does_not_raise(self):
        """`is_collectable=False` 是**返回值**，不是异常 —— 调用方不该崩。"""
        from storygraph.core import engine_facts as EF
        facts = EF.empty_facts()
        ok, why = EF.is_collectable(facts)
        assert ok is False and why

    def test_collection_path_never_imports_a_dialog(self):
        """⭐ 用户："不要弹窗" —— 采集链路里**不许**出现任何 MessageBox。

        扫的是 AST（不是文本）：注释里写 "QQMessageBox" 不会误伤。
        """
        targets = [ROOT / 'storygraph' / 'pipeline' / 'engine_collect.py',
                   ROOT / 'storygraph' / 'pipeline' / 'renpy_adapter.py',
                   ROOT / 'storygraph' / 'engine' / 'compat.py']
        bad = []
        for p in targets:
            if not p.is_file():
                continue
            tree = ast.parse(p.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                    names += [a.name for a in node.names]
                for n in names:
                    if 'QMessageBox' in n or 'PySide' in n or 'PyQt' in n:
                        bad.append(f'{p.name}: {n}')
        assert not bad, '采集链路里出现了 GUI 依赖（要 console 报，不要弹窗）：\n  ' \
                        + '\n  '.join(bad)
