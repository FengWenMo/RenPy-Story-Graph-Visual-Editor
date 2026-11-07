# -*- coding: utf-8 -*-
"""
P0/L1 与「一维一格」的**临时地狱测试**（2026-11-05）
=====================================================

用户：

> 「我专门修改了一块东西：你之前说的 P0/L1，和一个 `show lan aa happy` 这种的
>   卡片编辑器内的显示错位问题……点击切换表情会变为 `show lan happy aa`
>   却不影响导出……奇奇怪怪的 bug」

这一份不测"函数返回值长什么样"，而是把**真机上会出事的那几处**摆出来反复折腾：

## A. 引擎协议层（P0/L1）

| 坑 | 为什么值得钉死 |
|---|---|
| 目录名骗人（`renpy-8.4.1-sdk` 里其实是 8.5.3） | 版本判错 → 选错 API 阶梯 |
| `vc_version.py` 里只有 `version`，没有 `version_tuple` | 找错变量 → 版本永远是空的 |
| 三态能力（`True`/`False`/`None`） | 塌成 bool 后"这次没采到"会被当成"引擎没有" |
| lint 的 exit 1 **不是**失败（项目有错时正常） | 按 exit code 退阶 → 每次白跑一轮最简 lint |
| 老 facts（schema 1） | 升级后把用户昨天的文件读崩 |

## B. 一维一格（`show lan aa happy`）

真机形状：`aa` 是**项目服装表里的代号**（`CC_OUTFIT_LABEL['aa'] = '普通'`），
不是引擎属性；`rules.json` 声明的列序是 **人物 → 服装 → 表情**。于是：

- 卡片上「属性 1」必须是**服装**、「属性 2」才是表情；
- 切表情只能动表情那个词，**服装一个字节都不许挪**；
- `at` / `with` 那些子句更是一个字节都不许动。

最后一条是用户最在意的："不影响导出"不是借口 —— 看见的顺序和写下去的顺序
必须是同一个。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


# ---------------------------------------------------------------------------
#  A. 引擎协议层
# ---------------------------------------------------------------------------

class TestVersionTellsTheTruth:
    def _sdk(self, tmp_path, text: str, name='renpy-8.4.1-sdk'):
        root = tmp_path / name
        (root / 'renpy').mkdir(parents=True)
        (root / 'renpy' / 'vc_version.py').write_text(text, encoding='utf-8')
        return root

    def test_directory_name_lies_but_file_does_not(self, tmp_path):
        """⭐ 本机真事：目录叫 8.4.1，`vc_version.py` 里是 8.5.3.26051504。"""
        from storygraph.engine import detect
        sdk = self._sdk(tmp_path, "version = '8.5.3.26051504'\n")
        got = detect.version_of(sdk)
        assert got is not None
        assert str(got) == '8.5.3.26051504'
        assert got.short == '8.5.3'
        assert got.at_least(7, 4) is True

    def test_missing_file_is_none_not_guessed_from_dirname(self, tmp_path):
        """读不到就是 `None` —— 绝不从目录名反推（`8.4.1` 就是这么骗人的）。"""
        from storygraph.engine import detect
        root = tmp_path / 'renpy-8.4.1-sdk'
        root.mkdir()
        assert detect.version_of(root) is None

    def test_version_tuple_is_not_a_literal(self, tmp_path):
        """`version_tuple` 是算出来的，不认它；只认 `version = '…'` 字面量。"""
        from storygraph.engine import detect
        sdk = self._sdk(tmp_path, "version = '8.5.3.26051504'\n"
                                  "version_tuple = version.split('.')\n")
        assert str(detect.version_of(sdk)) == '8.5.3.26051504'

    def test_junk_is_rejected_not_half_parsed(self):
        from storygraph.engine import detect
        assert detect.parse_version('8.5.3-beta') is None
        assert detect.parse_version('') is None
        assert str(detect.parse_version('8.5')) == '8.5.0'

    def test_two_sources_disagreeing_is_said_out_loudly(self, tmp_path):
        """SDK 与项目日志对不上 → `agree=False` + 一句人话（不静默挑一个）。"""
        from storygraph.engine import detect
        sdk = self._sdk(tmp_path, "version = '8.5.3.26051504'\n")
        proj = tmp_path / 'proj'
        proj.mkdir()
        (proj / 'log.txt').write_text(
            "Ren'Py 8.4.1.1\nSome other line\n", encoding='utf-8')
        got = detect.versions(sdk, proj)
        assert got['sdk'] is not None and got['project'] is not None
        assert got['agree'] is False
        assert '8.5.3' in got['note'] and '8.4.1' in got['note']


class TestCapabilitiesAreThreeValued:
    def test_strict_normalize(self):
        from storygraph.engine import capabilities as C
        assert C.normalize(True) is True
        assert C.normalize(False) is False
        assert C.normalize(None) is None
        # 宽松转换会把"我不知道"悄悄变成"支持/不支持" —— 一个都不许过
        for loose in (1, 0, 'true', 'false', 'yes', [], {}):
            assert C.normalize(loose) is None, loose

    def test_false_and_none_are_told_apart(self):
        from storygraph.engine import capabilities as C
        caps = {C.CAP_LABELS: None, C.CAP_STORE_SNAPSHOT: False,
                C.CAP_DEFINES: True}
        assert C.missing_tokens(caps) == [C.CAP_LABELS, C.CAP_STORE_SNAPSHOT]
        assert C.is_available(caps[C.CAP_DEFINES]) is True
        text = C.describe(caps)
        assert 'False' in text and '没采到' in text, text

    def test_unknown_token_is_visible_not_fatal(self):
        from storygraph.engine import capabilities as C
        assert C.unknown_tokens({'CAP_NEW_THING': True}) == ['CAP_NEW_THING']


class TestEnvelopeAndFacts:
    def test_empty_batch_is_all_unknown(self):
        """采不到 ≠ 引擎没有：空信封里全是 `None`，一个 `False` 都没有。"""
        from storygraph.engine import base as E
        from storygraph.engine import capabilities as C
        got = E.empty_batch()
        assert got['schema'] == 2
        assert set(got['capabilities']) == set(C.ALL_CAPABILITIES)
        assert all(v is None for v in got['capabilities'].values())
        assert got['degraded']['missing'] == list(C.ALL_CAPABILITIES)
        assert E.problems(got) == []

    def test_batch_envelope_matches_what_collect_writes(self):
        """⚠ 信封有两处实现（`engine.base` 与 `EF.build_facts`）——
        形状**必须**一样，否则两边的 `problems()`/`validate_facts()` 会各说各话。"""
        from storygraph.core import engine_facts as EF
        from storygraph.engine import base as E
        from storygraph.engine import capabilities as C

        caps = {name: True for name in C.ALL_CAPABILITIES}
        caps[C.CAP_STORE_SNAPSHOT] = False
        batch = E.batch(capabilities=caps)
        facts = EF.build_facts({}, adapter={'id': 'renpy', 'version': 1},
                               capabilities=caps,
                               degraded=batch['degraded'],
                               freshness=batch['freshness'])
        assert facts['schema'] == 2
        for key in ('adapter', 'capabilities', 'degraded', 'freshness'):
            assert key in facts, f'build_facts 少了 {key}'
            assert key in batch, f'base.batch 少了 {key}'
        assert EF.validate_facts(facts) == [], EF.validate_facts(facts)

    def test_degraded_must_be_traceable(self):
        from storygraph.engine import base as E
        bad = E.empty_batch()
        bad['degraded'] = {'reason': '', 'missing': ['CAP_LABELS']}
        assert any('reason' in p for p in E.problems(bad))

    def test_v1_file_still_reads(self):
        """用户昨天写的 facts（schema 1）不许因为升级读崩。"""
        from storygraph.core import engine_facts as EF
        # 用真实装配路径造一份 v1：撤掉 v2 那四段、schema 改回 1
        old = EF.build_facts(
            {'images': [(EF.SRC_PROBE, ['a'])]},
            collection={'parse_clean': True, 'probe_complete': True,
                        'lint_exit': 0},
            derived={'overridden_builtins': [], 'undeclared_heads': [],
                     'scan_disclaimer': '以上是「我认出的」'})
        old['schema'] = 1
        for key in ('adapter', 'capabilities', 'degraded', 'freshness'):
            old.pop(key, None)

        assert EF.validate_facts(old) == [], EF.validate_facts(old)
        assert EF.value_of(old, 'images') == ['a']
        assert EF.loads(EF.dumps(old))['schema'] == 1, '读回来还得是 v1'

    def test_freshness_verdict_is_checked(self):
        from storygraph.core import engine_facts as EF
        facts = EF.build_facts({}, freshness={'verdict': '大概吧'})
        assert any('verdict' in p for p in EF.validate_facts(facts))


class TestLintLadder:
    def test_ladder_order_and_old_version_skips_dump(self):
        from storygraph.engine import compat
        from storygraph.engine import detect
        full = compat.lint_ladder(detect.parse_version('8.5.3'), 'dump.json')
        assert full[0][:2] == ['lint', '--error-code']
        assert '--json-dump' in full[0]
        assert full[-1] == ['lint']
        old = compat.lint_ladder(detect.parse_version('7.3.5'), 'dump.json')
        assert old == [['lint']], '7.3 没有 --json-dump，别浪费一轮'

    def test_reject_hint_only_for_flags_not_for_project_errors(self):
        from storygraph.engine import compat
        assert compat.reject_reason(
            'error: unrecognized arguments: --json-dump') == 'unrecognized arguments'
        assert compat.reject_reason(
            "File 'game/x.rpy', line 3: expected statement.") == ''

    def test_first_rung_rejected_falls_back_and_cleans_dump(self, tmp_path):
        """⭐ 引擎不认 `--json-dump` → 退到 `lint`；半截 dump 必须清掉。"""
        from storygraph.pipeline import engine_collect as EC
        dump = tmp_path / 'dump.json'
        dump.write_text('{"half": true', encoding='utf-8')
        calls = []

        def fake_run(engine_root, work, argv, timeout=0):
            calls.append(list(argv))
            if '--json-dump' in argv:
                return 1, 'error: unrecognized arguments: --json-dump'
            return 1, "File 'game/x.rpy', line 1: some lint finding"

        EC.run_engine = fake_run
        rc, out, used = EC._run_lint_ladder(tmp_path, tmp_path, dump, 5)
        assert len(calls) == 2, calls
        assert used == ['lint']
        assert rc == 1 and 'lint finding' in out
        assert not dump.exists(), '上一档写了一半的 dump 必须清掉'

    def test_project_error_does_not_retry(self, tmp_path):
        """项目有语法错时 lint 也 exit 1 —— 那是**正常结果**，重试只会白跑。"""
        from storygraph.pipeline import engine_collect as EC
        calls = []

        def fake_run(engine_root, work, argv, timeout=0):
            calls.append(list(argv))
            return 1, "File 'game/x.rpy', line 3: expected statement."

        EC.run_engine = fake_run
        rc, _out, used = EC._run_lint_ladder(tmp_path, tmp_path,
                                             tmp_path / 'd.json', 5)
        assert len(calls) == 1, calls
        assert used[:2] == ['lint', '--error-code']


class TestRungs:
    def _facts(self, caps, missing=()):
        return {'schema': 2, 'facts': {}, 'capabilities': caps,
                'degraded': {'reason': 'probe_partial' if missing else '',
                             'missing': list(missing)}}

    def test_matrix(self):
        from storygraph.engine import capabilities as C
        from storygraph.engine import decide as D
        all_ok = {n: True for n in C.ALL_CAPABILITIES}
        all_ok[C.CAP_STORE_SNAPSHOT] = False        # 引擎压根没有 ≠ 降级
        assert D.rung(self._facts(all_ok)) == D.R0
        assert D.rung(self._facts(all_ok), collectable=False) == D.R2
        assert D.rung(self._facts(all_ok), freshness='stale') == D.R1
        assert D.rung(None) == D.R4 and D.rung({}) == D.R4
        assert D.rung({'schema': 1, 'facts': {}}) == D.R1
        # `None` 才降级；`False` 是"能力清单的事实"
        one_none = dict(all_ok)
        one_none[C.CAP_LABELS] = None
        assert D.rung(self._facts(one_none, ['CAP_LABELS'])) == D.R1

    def test_degraded_never_turns_into_pass(self):
        """R2 及以下：运行期能力一个都不许放行；静态能力照给。"""
        from storygraph.engine import capabilities as C
        from storygraph.engine import decide as D
        ok, miss = D.allows(D.R2, [C.CAP_LABELS])
        assert ok is False and miss == [C.CAP_LABELS]
        ok, _miss = D.allows(D.R2, [C.CAP_LAYERED_ATTRIBUTES])
        assert ok is True
        ok, miss = D.allows(D.R1, [C.CAP_LABELS], {C.CAP_LABELS: None})
        assert ok is False and miss == [C.CAP_LABELS]


# ---------------------------------------------------------------------------
#  B. 一维一格：`show lan aa happy`
# ---------------------------------------------------------------------------

RULES = {
    'statements': [{
        'name': 'show',
        'pattern': [
            {'kind': 'keyword', 'text': 'show'},
            {'kind': 'choice', 'column': '角色', 'options': ['lan', 'ye']},
            {'kind': 'choice', 'column': '服装',
             'options': ['aa', 'clothes2', 'base_hand_up']},
            {'kind': 'choice', 'column': '表情',
             'options': ['happy', 'angry', 'close_happy']},
        ],
    }],
}

DECL = """\
layeredimage lan:
    image_format 'images/characters/lan/{image}.png'
    group outfit:
        attribute clothes2:
            'clothes2'
        attribute base_hand_up:
            'base_hand_up'
        attribute aa:
            'aa'
    group expressions:
        attribute happy default:
            'happy'
        attribute angry:
            'angry'
        attribute close_happy:
            'close_happy'
"""


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / 'two'
    g = root / 'game'
    d = g / 'images' / 'characters' / 'lan'
    d.mkdir(parents=True)
    for n in ('aa', 'clothes2', 'base_hand_up', 'happy', 'angry', 'close_happy'):
        (d / f'{n}.png').write_bytes(PNG)
    (g / 'definitions.rpy').write_text(DECL, encoding='utf-8')
    (g / 'script.rpy').write_text('label start:\n    return\n', encoding='utf-8')
    (root / 'storygraph.rules.json').write_text(
        json.dumps(RULES, ensure_ascii=False), encoding='utf-8')
    return root


def _sym(root):
    from storygraph.assets.asset_registry import AssetRegistry
    from storygraph.story.dsl import Symbols
    reg = AssetRegistry()
    reg.project_root = str(root)
    return Symbols(reg)


class TestSlotLayout:
    def test_outfit_comes_first_per_declared_columns(self, proj):
        """⭐ 用户拍桌子的那一条：他注册的是 人物 → 服装 → 表情。"""
        from storygraph.app import beats as B
        rows = B.attr_slot_rows('lan aa happy', _sym(proj))
        assert [r['group'] for r in rows] == ['outfit', 'expressions'], rows
        assert [r['value'] for r in rows] == ['aa', 'happy'], rows

    def test_switching_expression_keeps_outfit_in_place(self, proj):
        """⭐⭐ 真机事故那一对：切表情**不许**变成 `lan happy aa`。"""
        from storygraph.app import beats as B
        sym = _sym(proj)
        assert B.apply_attr_slot('lan aa happy', 'expressions', 'angry',
                                 symbols=sym) == 'lan aa angry'
        assert B.apply_attr_slot('lan aa happy', 'outfit', 'base_hand_up',
                                 symbols=sym) == 'lan base_hand_up happy'

    def test_clicking_the_current_value_changes_nothing(self, proj):
        """同一个词再点一次：顺序也不许变（第一版就是这么把词挪到末尾的）。"""
        from storygraph.app import beats as B
        sym = _sym(proj)
        for spec, group, value in (
                ('lan aa happy', 'expressions', 'happy'),
                ('lan aa happy', 'outfit', 'aa'),
                ('lan base_hand_up close_happy', 'outfit', 'base_hand_up')):
            assert B.apply_attr_slot(spec, group, value, symbols=sym) == spec

    def test_every_option_from_every_row_is_a_one_word_edit(self, proj):
        """⭐ 穷举：每一格 × 每个候选（含「不写」）→ 只许动一个词、位置不变。"""
        from storygraph.app import beats as B
        sym = _sym(proj)
        for spec in ('lan aa happy', 'lan base_hand_up close_happy',
                     'lan happy', 'ye aa happy'):
            s = _sym(proj) if spec.startswith('ye') else sym
            before = spec.split()
            for row in B.attr_slot_rows(spec, s):
                for value in list(row.get('options') or []) + ['']:
                    got = B.apply_attr_slot(spec, row['group'], value,
                                            symbols=s)
                    assert got.split()[0] == 'lan' or got.split()[0] == 'ye'
                    after = got.split()
                    if value == '':
                        assert after == [w for w in before if w != row['value']]
                        continue
                    if not row['value']:
                        # 这一维原来没写 → 只能**追加**（没有"原位"可言）
                        assert after == before + [value], (spec, got)
                        continue
                    assert len(after) == len(before), (spec, row['group'], value, got)
                    diff = [i for i, (a, b) in enumerate(zip(before, after))
                            if a != b]
                    assert len(diff) <= 1, (spec, row['group'], value, got)
                    if diff:
                        assert after[diff[0]] == value

    def test_clauses_are_byte_identical(self, proj):
        """`at i11` / `with tt` 这些子句一个字节都不许动。"""
        from storygraph.app import beats as B
        sym = _sym(proj)
        got = B.apply_attr_slot('lan aa happy at i11 with dissolve',
                                'expressions', 'angry', symbols=sym)
        assert got == 'lan aa angry at i11 with dissolve', got
        assert got.endswith(' at i11 with dissolve')

    def test_unknown_word_is_never_dropped(self, proj):
        """项目自造的内部件（`_hd_cut` 这类）：不认识，但一个字节都不许丢。"""
        from storygraph.app import beats as B
        sym = _sym(proj)
        got = B.apply_attr_slot('lan _hd_cut aa happy', 'expressions', 'angry',
                                symbols=sym)
        assert got == 'lan _hd_cut aa angry', got

    def test_without_rules_json_order_falls_back_to_engine(self, tmp_path):
        """没有 `rules.json` → 没有"列序"可依，退回引擎组序（**不猜**）。

        这条是**现状记录**：真机 `sr_general` 就没有 `storygraph.rules.json`
        （标准版不写项目），所以卡片上「属性 1/2」只能按 `layeredimage` 的
        组顺序来 —— 那是"没声明"，不是"声明反了"。
        """
        from storygraph.app import beats as B
        root = tmp_path / 'bare'
        g = root / 'game'
        d = g / 'images' / 'characters' / 'lan'
        d.mkdir(parents=True)
        for n in ('aa', 'clothes2', 'happy', 'angry'):
            (d / f'{n}.png').write_bytes(PNG)
        (g / 'definitions.rpy').write_text(DECL, encoding='utf-8')
        rows = B.attr_slot_rows('lan aa happy', _sym(root))
        assert [r['group'] for r in rows] == ['outfit', 'expressions'], rows
        got = B.apply_attr_slot('lan aa happy', 'expressions', 'angry',
                                symbols=_sym(root))
        assert got == 'lan aa angry', '顺序可以没声明，但切一维不许挪位置'
