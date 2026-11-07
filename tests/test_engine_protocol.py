"""
引擎协议层（P0-1）：能力三态 / 版本探测 / 信封 / 退化链
====================================================

> 2026-11-05。对应 `docs/交接_P0与L1_引擎协议层_2026-11-05.md` 的 P0-1 与 L1-1。

这个文件钉四组：

| 组 | 钉什么 | 违反的后果 |
|---|---|---|
| `TestDependencyDirection` | `engine/` **只许 import stdlib** | 依赖环回来，`import_cycles_all` 预算 0 当场红 |
| `TestVersionDetection` | 读 `vc_version.py` 的**内容**，不看目录名 | 本机真事：目录叫 8.4.1，实际 8.5.3 |
| `TestCapabilitiesThreeState` | `True` / `False` / `None` **不许合并** | "不支持"与"没采到"被混成一句话 |
| `TestEnvelope` | 信封四段齐全、`None` 不写成 `False` | 采集端忘了填 degraded |
| `TestRungs` | R0–R4 判定顺序 + **三条不变量** | 退化后把"错"判成"对" |

## 三条不变量（§3.3，最容易破的就是这三条）

1. **退化不改判**：R0 能判"错"的，R1/R2 只能 `unknown`，不许变 `ok`；
2. **退化必须留痕**：档位要能追到 `degraded.reason`；
3. **不许升级**：`source='declared'` 的事实不许因为"没冲突"当成运行时的。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.engine import base as E_base              # noqa: E402
from storygraph.engine import capabilities as E_caps      # noqa: E402
from storygraph.engine import decide as E_decide          # noqa: E402
from storygraph.engine import detect as E_detect          # noqa: E402


# ---------------------------------------------------------------------------
#  零、依赖方向（这一条违反，整轮测试会红，所以放最前）
# ---------------------------------------------------------------------------

class TestDependencyDirection:
    def test_engine_only_imports_stdlib(self):
        """`engine/` 里**不许**出现 `from storygraph.xxx`（除了它自己）。"""
        bad = []
        for p in sorted((ROOT / 'storygraph' / 'engine').glob('*.py')):
            tree = ast.parse(p.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                for m in mods:
                    if m.startswith('storygraph') and not \
                            m.startswith('storygraph.engine'):
                        bad.append(f'{p.name}: {m}')
        assert not bad, 'engine 层引入了 storygraph 依赖（环会回来）：\n  ' + \
                        '\n  '.join(bad)

    def test_init_stays_empty(self):
        """`engine/__init__.py` 不许 re-export 实现（一 export 就拖进 pipeline）。"""
        text = (ROOT / 'storygraph' / 'engine' / '__init__.py').read_text(
            encoding='utf-8')
        tree = ast.parse(text)
        imports = [n for n in ast.walk(tree)
                   if isinstance(n, (ast.Import, ast.ImportFrom))]
        assert not imports, f'__init__.py 里出现了 import：{imports}'


# ---------------------------------------------------------------------------
#  一、版本探测
# ---------------------------------------------------------------------------

def _sdk(tmp_path, version_text, *, dirname='renpy-8.4.1-sdk'):
    root = tmp_path / dirname
    (root / 'renpy').mkdir(parents=True)
    (root / 'renpy' / 'vc_version.py').write_text(
        "# generated\nversion = '" + version_text + "'\n"
        "version_info = (8, 5, 3, 'release', 26051504)\n", encoding='utf-8')
    return root


class TestVersionDetection:
    def test_reads_the_content_not_the_directory_name(self, tmp_path):
        """⭐ 交接文档点名必测：目录叫 8.4.1，内容写 8.5.3 → 返回 **8.5.3**。"""
        got = E_detect.version_of(_sdk(tmp_path, '8.5.3.26051504'))
        assert got is not None
        assert (got.major, got.minor, got.patch) == (8, 5, 3)
        assert got.short == '8.5.3'
        assert str(got) == '8.5.3.26051504'

    def test_three_segment_version(self, tmp_path):
        got = E_detect.version_of(_sdk(tmp_path, '8.4.1'))
        assert got.commit == 0 and str(got) == '8.4.1'

    def test_missing_sdk_is_none(self, tmp_path):
        assert E_detect.version_of(tmp_path / 'nope') is None

    def test_missing_file_is_none(self, tmp_path):
        root = tmp_path / 'sdk'
        root.mkdir()
        assert E_detect.version_of(root) is None

    def test_non_literal_version_is_none(self, tmp_path):
        """`version = compute()` 一律 `None` —— 我们不执行引擎代码。"""
        root = tmp_path / 'sdk'
        (root / 'renpy').mkdir(parents=True)
        (root / 'renpy' / 'vc_version.py').write_text(
            'version = compute_version()\n', encoding='utf-8')
        assert E_detect.version_of(root) is None

    @pytest.mark.parametrize('text,expected', [
        ('8.5.3.26051504', (8, 5, 3, 26051504)),
        ('8.5.3', (8, 5, 3, 0)),
        ('8.5', (8, 5, 0, 0)),
        (' 9.0.0.1 ', (9, 0, 0, 1)),
    ])
    def test_parse_forms(self, text, expected):
        got = E_detect.parse_version(text)
        assert (got.major, got.minor, got.patch, got.commit) == expected

    @pytest.mark.parametrize('text', ['', 'abc', '8.5.x', 'v8.5'])
    def test_parse_rejects_garbage(self, text):
        assert E_detect.parse_version(text) is None

    def test_at_least(self, tmp_path):
        got = E_detect.version_of(_sdk(tmp_path, '8.5.3'))
        assert got.at_least(8, 5)
        assert got.at_least(8, 4)
        assert not got.at_least(8, 6)


# ---------------------------------------------------------------------------
#  二、能力三态
# ---------------------------------------------------------------------------

class TestCapabilitiesThreeState:
    def test_normalize_keeps_only_true_false_none(self):
        assert E_caps.normalize(True) is True
        assert E_caps.normalize(False) is False
        assert E_caps.normalize(None) is None

    @pytest.mark.parametrize('value', [1, 0, 'true', '', [], {}])
    def test_normalize_rejects_everything_else(self, value):
        """宽松转换会把"我不知道"悄悄变成"支持/不支持" —— 一律 `None`。"""
        assert E_caps.normalize(value) is None

    def test_false_and_none_are_told_apart(self):
        assert E_caps.is_available(True)
        assert not E_caps.is_available(False)
        assert not E_caps.is_available(None)
        # "用不上"包含两者，但描述里必须分开
        assert E_caps.is_missing(False) and E_caps.is_missing(None)

    def test_describe_lists_unknown_separately(self):
        text = E_caps.describe({E_caps.CAP_IMAGE_REGISTRY: True,
                                E_caps.CAP_LABELS: None,
                                E_caps.CAP_STORE_SNAPSHOT: False})
        assert 'CAP_IMAGE_REGISTRY=True' in text
        assert 'CAP_STORE_SNAPSHOT=False' in text
        assert '没采到：CAP_LABELS' in text

    def test_every_token_has_a_note(self):
        for name in E_caps.ALL_CAPABILITIES:
            assert E_caps.NOTES.get(name), f'{name} 没有说明'

    def test_unknown_tokens_are_reported_not_swallowed(self):
        got = E_caps.unknown_tokens({'CAP_IMAGE_REGISTRY': True,
                                     'CAP_WHATEVER': True})
        assert got == ['CAP_WHATEVER']


# ---------------------------------------------------------------------------
#  三、信封
# ---------------------------------------------------------------------------

class TestEnvelope:
    def test_empty_batch_is_all_unknown_not_false(self):
        """⭐ 采集失败时全部填 `None`（没采到），**不是** `False`（引擎没有）。"""
        got = E_base.empty_batch()
        assert set(got['capabilities'].values()) == {None}
        assert E_base.problems(got) == []

    def test_batch_fills_missing_tokens_with_none(self):
        got = E_base.batch(capabilities={E_caps.CAP_IMAGE_REGISTRY: True})
        assert got['capabilities'][E_caps.CAP_IMAGE_REGISTRY] is True
        assert got['capabilities'][E_caps.CAP_LABELS] is None
        assert E_base.problems(got) == []

    def test_batch_records_degraded(self):
        got = E_base.batch(
            capabilities={E_caps.CAP_LABELS: None},
            degraded={'reason': 'probe_partial',
                      'missing': [E_caps.CAP_LABELS]})
        assert got['degraded']['reason'] == 'probe_partial'
        assert E_base.problems(got) == []

    def test_clean_batch_has_no_degraded_reason(self):
        caps = {n: True for n in E_caps.ALL_CAPABILITIES}
        got = E_base.batch(capabilities=caps)
        assert got['degraded'] == {'reason': '', 'missing': []}
        assert E_base.problems(got) == []

    def test_missing_without_reason_is_a_problem(self):
        got = E_base.empty_batch()
        got['degraded'] = {'reason': '', 'missing': [E_caps.CAP_LABELS]}
        assert any('没有 reason' in p for p in E_base.problems(got))

    def test_bad_freshness_verdict_is_a_problem(self):
        got = E_base.empty_batch()
        got['freshness'] = {'verdict': 'freshish', 'checked_at': '',
                            'rpy_stamp': ''}
        assert any('verdict' in p for p in E_base.problems(got))

    def test_unknown_token_is_a_problem(self):
        got = E_base.empty_batch()
        got['capabilities']['CAP_MADE_UP'] = True
        assert any('未登记的令牌' in p for p in E_base.problems(got))


# ---------------------------------------------------------------------------
#  四、退化链
# ---------------------------------------------------------------------------

def _facts(caps=None, degraded=None, schema=2):
    return {'schema': schema,
            'capabilities': caps if caps is not None else {},
            'degraded': degraded or {'reason': '', 'missing': []}}


class TestRungs:
    def test_no_facts_is_r4(self):
        assert E_decide.rung(None) == E_decide.R4
        assert E_decide.rung({}) == E_decide.R4

    def test_unknown_schema_is_r4(self):
        assert E_decide.rung({'foo': 1}) == E_decide.R4

    def test_not_collectable_is_r2(self):
        """⭐ 静态是**基线**，不是 fallback：探针没跑成也有声明可用。"""
        assert E_decide.rung(_facts({'CAP_LABELS': None}),
                             collectable=False) == E_decide.R2

    def test_any_none_is_r1(self):
        """`None` = 这次没采到 → 降级（与 `False` 严格分开）。"""
        assert E_decide.rung(_facts({'CAP_LABELS': None,
                                     'CAP_IMAGE_REGISTRY': True})) \
            == E_decide.R1

    def test_false_does_not_degrade(self):
        """`False` = 引擎结构上就没有这项 —— 那是**能力事实**，不是降级。

        P0 里 `CAP_STORE_SNAPSHOT` / `CAP_LAYERED_CONDITIONALS` 恒为 `False`
        却要能到 R0；把它们算成降级会让 R0 永远不可达。
        """
        assert E_decide.rung(_facts({'CAP_STORE_SNAPSHOT': False,
                                     'CAP_IMAGE_REGISTRY': True})) \
            == E_decide.R0

    def test_degraded_missing_is_r1(self):
        assert E_decide.rung(_facts(
            {n: True for n in E_caps.ALL_CAPABILITIES},
            {'reason': 'probe_partial', 'missing': ['CAP_LABELS']})) \
            == E_decide.R1

    def test_stale_is_r1(self):
        caps = {n: True for n in E_caps.ALL_CAPABILITIES}
        assert E_decide.rung(_facts(caps), freshness='stale') == E_decide.R1

    def test_all_true_is_r0(self):
        caps = {n: True for n in E_caps.ALL_CAPABILITIES}
        assert E_decide.rung(_facts(caps)) == E_decide.R0

    def test_v1_facts_without_capabilities_is_r1(self):
        """老 facts（v1）没有 capabilities 段 → 只能说"有运行期事实"（R1）。"""
        assert E_decide.rung({'schema': 1, 'facts': {}}) == E_decide.R1


class TestAllows:
    def test_r0_allows_everything(self):
        ok, miss = E_decide.allows(E_decide.R0, ['CAP_LABELS', 'CAP_DEFINES'])
        assert ok and not miss

    def test_r0_still_checks_actual_caps_when_given(self):
        """标了 R0 但能力表里缺 → **仍然拦**（不许"档位高就放行"）。"""
        ok, miss = E_decide.allows(E_decide.R0, ['CAP_LABELS'],
                                   {'CAP_LABELS': None})
        assert not ok and miss == ['CAP_LABELS']

    def test_r1_checks_caps(self):
        ok, miss = E_decide.allows(E_decide.R1, ['CAP_LABELS', 'CAP_DEFINES'],
                                   {'CAP_LABELS': True, 'CAP_DEFINES': None})
        assert not ok and miss == ['CAP_DEFINES']

    def test_r2_has_no_runtime_capability(self):
        ok, miss = E_decide.allows(E_decide.R2, ['CAP_LABELS'])
        assert not ok and miss == ['CAP_LABELS']

    def test_r2_still_allows_static_ones(self):
        ok, miss = E_decide.allows(E_decide.R2, ['CAP_LAYERED_ATTRIBUTES'])
        assert ok and not miss

    def test_empty_need_is_always_ok(self):
        assert E_decide.allows(E_decide.R4, [])[0] is True


class TestThreeInvariants:
    """§3.3 那三条 —— 最容易破的就是它们。"""

    def test_degrade_never_turns_error_into_ok(self):
        """退化不改判：R0 能判"错"的，R1/R2 只能放弃判（`allows` 拦下来）。"""
        need = ['CAP_LABELS']
        r0_ok, _ = E_decide.allows(E_decide.R0, need, {'CAP_LABELS': True})
        r1_ok, r1_miss = E_decide.allows(E_decide.R1, need,
                                         {'CAP_LABELS': None})
        r2_ok, r2_miss = E_decide.allows(E_decide.R2, need)
        assert r0_ok is True
        assert r1_ok is False and r1_miss
        assert r2_ok is False and r2_miss

    def test_degrade_leaves_a_trace(self):
        """退化必须留痕：档位描述里能追到原因。"""
        facts = _facts({E_caps.CAP_LABELS: None},
                       {'reason': 'probe_partial', 'missing': ['CAP_LABELS']})
        name = E_decide.rung(facts)
        text = E_decide.describe(name, facts)
        assert 'probe_partial' in text

    def test_declared_never_upgrades_to_runtime(self):
        """`source='declared'` 的事实不许因为"没冲突"被当成运行期事实。

        表达方式：这类事实只在 R2 出现；R2 **不许**支撑任何运行期能力。
        """
        ok, miss = E_decide.allows(E_decide.R2,
                                   sorted(E_decide.RUNTIME_ONLY))
        assert not ok
        assert set(miss) == set(E_decide.RUNTIME_ONLY)

    def test_weaker_ordering(self):
        assert E_decide.is_weaker_than(E_decide.R2, E_decide.R0)
        assert not E_decide.is_weaker_than(E_decide.R0, E_decide.R2)
        assert E_decide.is_weaker_than('made_up', E_decide.R4)

    def test_every_rung_has_a_note(self):
        for name in E_decide.RUNGS:
            assert E_decide.RUNG_NOTES.get(name)


# ---------------------------------------------------------------------------
#  五、两源对照（SDK vs 项目日志）—— 2026-11-04 用户提的 8.4.1
# ---------------------------------------------------------------------------

class TestTwoSourcesMustAgree:
    """用户说"game 有明确的 8.4.1"，而 SDK 目录也叫 `renpy-8.4.1-sdk`。

    实测：项目 `log.txt:3` / `errors.txt:9` 与 SDK `vc_version.py:4`
    **三处全是 8.5.3.26051504** —— `8.4.1` 只活在目录名里。

    ⇒ 探测必须**两个来源都比**，不一致就明说，不许静默挑一个。
    """

    def _proj(self, tmp_path, text):
        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True, exist_ok=True)
        (root / 'log.txt').write_text(text, encoding='utf-8')
        return root

    def test_project_version_from_log(self, tmp_path):
        root = self._proj(tmp_path, "2026-11-04\nWindows-11\n"
                                    "Ren'Py 8.5.3.26051504\n\nEarly init\n")
        got = E_detect.project_version(root)
        assert got is not None and str(got) == '8.5.3.26051504'

    def test_project_version_reads_errors_txt_too(self, tmp_path):
        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True)
        (root / 'errors.txt').write_text(
            'Invalid condition.\n\nRen\'Py Version: Ren\'Py 8.4.1.1\n',
            encoding='utf-8')
        got = E_detect.project_version(root)
        assert got is not None and got.short == '8.4.1'

    def test_no_log_is_none(self, tmp_path):
        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True)
        assert E_detect.project_version(root) is None

    def test_agree(self, tmp_path):
        sdk = _sdk(tmp_path, '8.5.3.26051504')
        proj = self._proj(tmp_path, "Ren'Py 8.5.3.26051504\n")
        got = E_detect.versions(sdk, proj)
        assert got['agree'] is True
        assert got['note'] == ''

    def test_disagree_names_both(self, tmp_path):
        """⚠ 不一致时**必须把两个数都说出来**（"别把它当同一件事"）。"""
        sdk = _sdk(tmp_path, '8.5.3.26051504')
        proj = self._proj(tmp_path, "Ren'Py 8.4.1.1182\n")
        got = E_detect.versions(sdk, proj)
        assert got['agree'] is False
        assert '8.5.3' in got['note'] and '8.4.1' in got['note']

    def test_one_source_is_neither_agree_nor_disagree(self, tmp_path):
        """只有一个来源 → `agree=None`（**不是** True：一边说了不算对上）。"""
        sdk = _sdk(tmp_path, '8.5.3')
        got = E_detect.versions(sdk, tmp_path / 'not-a-project')
        assert got['agree'] is None
        assert got['sdk'] is not None and got['project'] is None

    def test_neither_source(self, tmp_path):
        got = E_detect.versions(tmp_path / 'a', tmp_path / 'b')
        assert got['agree'] is None and got['sdk'] is None \
            and got['project'] is None


# ---------------------------------------------------------------------------
#  五、两源对照（SDK vs 项目日志）—— 2026-11-04 用户提的 8.4.1
# ---------------------------------------------------------------------------

class TestTwoSourcesMustAgree:
    """用户说"game 有明确的 8.4.1"，而 SDK 目录也叫 `renpy-8.4.1-sdk`。

    实测：项目 `log.txt:3` / `errors.txt:9` 与 SDK `vc_version.py:4`
    **三处全是 8.5.3.26051504** —— `8.4.1` 只活在目录名里。

    ⇒ 探测必须**两个来源都比**，不一致就明说，不许静默挑一个。
    """

    def _proj(self, tmp_path, text):
        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True, exist_ok=True)
        (root / 'log.txt').write_text(text, encoding='utf-8')
        return root

    def test_project_version_from_log(self, tmp_path):
        root = self._proj(tmp_path, "2026-11-04\nWindows-11\n"
                                    "Ren'Py 8.5.3.26051504\n\nEarly init\n")
        got = E_detect.project_version(root)
        assert got is not None and str(got) == '8.5.3.26051504'

    def test_project_version_reads_errors_txt_too(self, tmp_path):
        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True)
        (root / 'errors.txt').write_text(
            'Invalid condition.\n\nRen\'Py Version: Ren\'Py 8.4.1.1\n',
            encoding='utf-8')
        got = E_detect.project_version(root)
        assert got is not None and got.short == '8.4.1'

    def test_no_log_is_none(self, tmp_path):
        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True)
        assert E_detect.project_version(root) is None

    def test_agree(self, tmp_path):
        sdk = _sdk(tmp_path, '8.5.3.26051504')
        proj = self._proj(tmp_path, "Ren'Py 8.5.3.26051504\n")
        got = E_detect.versions(sdk, proj)
        assert got['agree'] is True
        assert got['note'] == ''

    def test_disagree_names_both(self, tmp_path):
        """⚠ 不一致时**必须把两个数都说出来**（"别把它当同一件事"）。"""
        sdk = _sdk(tmp_path, '8.5.3.26051504')
        proj = self._proj(tmp_path, "Ren'Py 8.4.1.1182\n")
        got = E_detect.versions(sdk, proj)
        assert got['agree'] is False
        assert '8.5.3' in got['note'] and '8.4.1' in got['note']

    def test_one_source_is_neither_agree_nor_disagree(self, tmp_path):
        """只有一个来源 → `agree=None`（**不是** True：一边说了不算对上）。"""
        sdk = _sdk(tmp_path, '8.5.3')
        got = E_detect.versions(sdk, tmp_path / 'not-a-project')
        assert got['agree'] is None
        assert got['sdk'] is not None and got['project'] is None

    def test_neither_source(self, tmp_path):
        got = E_detect.versions(tmp_path / 'a', tmp_path / 'b')
        assert got['agree'] is None and got['sdk'] is None \
            and got['project'] is None
