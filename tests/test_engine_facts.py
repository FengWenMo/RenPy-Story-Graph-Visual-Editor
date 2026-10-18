"""
判据 · **引擎事实**（E1，2026-10-10）
=====================================

分两半：

· **纯层**（`core.engine_facts`）—— 结构 / 校验 / 查询 / 合并。永远跑，不依赖 SDK。
· **采集器**（`pipeline.engine_collect`）—— 需要真 SDK 的用例在**找不到 SDK 时整条 skip**
  （本仓库既有做法：真机 / 真工具缺失就 skip，而不是假装通过）。

最要紧的几条（都对着实测踩过的坑）：
  · **没有 source 的事实校验不过** —— 查不出来源就没法判"引擎变了没有"；
  · **两个来源不一致 → `conflicts` + `source='conflict'`** —— 不许静默挑一个；
  · **`parse_clean=False` / `probe_complete=False` → `is_collectable` 为假**（坑 2 / 坑 4）；
  · **删探针必须连 `.rpyc` 一起删**（N7：实测孤儿 `.rpyc` 引擎不会自动清）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.core import engine_facts as EF                      # noqa: E402
from storygraph.pipeline import engine_collect as EC                # noqa: E402


# ---------------------------------------------------------------------------
#  纯层：结构 / 校验
# ---------------------------------------------------------------------------

class TestValidate:
    def test_empty_facts_passes(self):
        assert EF.validate_facts(EF.empty_facts()) == []

    def test_not_a_dict(self):
        assert EF.validate_facts([]) != []

    def test_missing_schema(self):
        f = EF.empty_facts()
        del f['schema']
        assert any('schema' in p for p in EF.validate_facts(f))

    def test_unknown_schema_version(self):
        f = EF.empty_facts()
        f['schema'] = 999
        assert any('版本不认识' in p for p in EF.validate_facts(f))

    def test_fact_without_source_is_rejected(self):
        """**最重要的一条**：没有来源的事实无法用于漂移判断。"""
        f = EF.empty_facts()
        f['facts']['images'] = {'value': ['a']}
        problems = EF.validate_facts(f)
        assert any('缺 source' in p for p in problems), problems

    def test_unknown_fact_key_is_reported(self):
        """多出来的键要**报出来**，不能悄悄留着。"""
        f = EF.empty_facts()
        f['facts']['whatever'] = {'value': 1, 'source': EF.SRC_PROBE}
        assert any('未知键' in p for p in EF.validate_facts(f))

    def test_missing_collection_keys(self):
        f = EF.empty_facts()
        f['collection'] = {}
        problems = EF.validate_facts(f)
        assert any('parse_clean' in p for p in problems)
        assert any('probe_complete' in p for p in problems)


class TestCollectable:
    def test_gate_closed_when_parse_dirty(self):
        f = EF.empty_facts()
        f['collection']['parse_clean'] = False
        f['collection']['probe_complete'] = True
        ok, reason = EF.is_collectable(f)
        assert not ok and 'parse error' in reason

    def test_gate_closed_when_probe_missing(self):
        """坑 4：parse error 会让探针也跑不到 → 采不到必须当失败。"""
        f = EF.empty_facts()
        f['collection']['parse_clean'] = True
        f['collection']['probe_complete'] = False
        ok, reason = EF.is_collectable(f)
        assert not ok and '探针' in reason

    def test_gate_open(self):
        f = EF.empty_facts()
        f['collection'].update(parse_clean=True, probe_complete=True)
        f['facts']['images'] = {'value': ['x'], 'source': EF.SRC_PROBE}
        ok, reason = EF.is_collectable(f)
        assert ok and reason == ''


# ---------------------------------------------------------------------------
#  纯层：合并（不许静默挑一个）
# ---------------------------------------------------------------------------

class TestMerge:
    def test_single_source(self):
        entry, conflicts = EF.merge_fact([(EF.SRC_PROBE, ['a'])])
        assert entry['value'] == ['a'] and entry['source'] == EF.SRC_PROBE
        assert conflicts == []

    def test_empty_contributions_dropped(self):
        entry, _ = EF.merge_fact([(EF.SRC_PROBE, []), (EF.SRC_JSON_DUMP, None)])
        assert entry is None

    def test_equal_sources_not_a_conflict(self):
        entry, conflicts = EF.merge_fact([(EF.SRC_PROBE, ['a']),
                                          (EF.SRC_JSON_DUMP, ['a'])])
        assert entry['value'] == ['a'] and conflicts == []
        assert entry['sources'] == [EF.SRC_PROBE, EF.SRC_JSON_DUMP]

    def test_conflict_takes_union_and_marks(self):
        entry, conflicts = EF.merge_fact([(EF.SRC_PROBE, ['a']),
                                          (EF.SRC_JSON_DUMP, ['b'])])
        assert entry['source'] == EF.SRC_CONFLICT
        assert set(entry['value']) == {'a', 'b'}       # 并集，不静默丢
        assert conflicts and conflicts[0]['sources'] == [EF.SRC_PROBE, EF.SRC_JSON_DUMP]


class TestBuildFacts:
    def test_assembles_and_tags_conflicts_with_field(self):
        facts = EF.build_facts({
            'images': [(EF.SRC_PROBE, ['lan'])],
            'transforms': [(EF.SRC_JSON_DUMP, ['t1']), (EF.SRC_PROBE, ['t2'])],
        })
        assert EF.value_of(facts, 'images') == ['lan']
        assert EF.source_of(facts, 'images') == EF.SRC_PROBE
        assert EF.source_of(facts, 'transforms') == EF.SRC_CONFLICT
        assert facts['conflicts'][0]['field'] == 'transforms'
        assert EF.validate_facts(facts) == []

    def test_ignores_unknown_payload_keys(self):
        facts = EF.build_facts({'nope': [(EF.SRC_PROBE, ['x'])]})
        assert 'nope' not in facts['facts']
        assert EF.validate_facts(facts) == []


# ---------------------------------------------------------------------------
#  纯层：查询
# ---------------------------------------------------------------------------

def _facts_with_attrs():
    return EF.build_facts({
        'attributes': [(EF.SRC_PROBE, {
            'lan': [{'group': 'expressions', 'attribute': 'happy'},
                    {'group': 'outfit', 'attribute': 'clothes2'},
                    {'group': 'outfit', 'attribute': 'base_swimsuit'}],
        })],
        'candidates': [(EF.SRC_PROBE, {'lan': ['happy', 'clothes2']})],
    })


class TestQueries:
    def test_attributes_of(self):
        f = _facts_with_attrs()
        assert len(EF.attributes_of(f, 'lan')) == 3
        assert EF.attributes_of(f, '不存在') == []

    def test_groups_of_reads_engine_groups(self):
        """分组来自**引擎**（layeredimage 的 group），不是我们发明的。"""
        g = EF.groups_of(_facts_with_attrs(), 'lan')
        assert g['outfit'] == ['clothes2', 'base_swimsuit']
        assert g['expressions'] == ['happy']

    def test_candidates_of(self):
        f = _facts_with_attrs()
        assert EF.candidates_of(f, 'lan') == ['happy', 'clothes2']
        assert EF.candidates_of(f, 'missing') == []

    def test_broken_section_returns_empty_not_crash(self):
        f = EF.build_facts({'images': [(EF.SRC_PROBE, ['lan'])]})
        assert EF.attributes_of(f, 'lan') == []
        assert EF.candidates_of(f, 'lan') == []
        assert EF.groups_of(f, 'lan') == {}


class TestNarrowProblem:
    """收窄断言写**性质**，不写死期望值（否则引擎升级后 CI 天天误报）。"""

    def test_subset_ok(self):
        assert EF.narrow_problem(['a', 'b', 'c'], ['a', 'b']) == ''

    def test_single_element_before_is_ok(self):
        assert EF.narrow_problem(['a'], ['a']) == ''

    def test_superset_is_problem(self):
        assert '原集合里没有' in EF.narrow_problem(['a'], ['a', 'z'])

    def test_no_shrink_is_problem(self):
        assert '没有变小' in EF.narrow_problem(['a', 'b'], ['a', 'b'])

    def test_empty_after_is_problem(self):
        assert '空集' in EF.narrow_problem(['a', 'b'], [])


class TestDumps:
    def test_roundtrip(self):
        f = _facts_with_attrs()
        assert EF.loads(EF.dumps(f)) == f

    def test_loads_rejects_non_object(self):
        with pytest.raises(ValueError):
            EF.loads('[1,2,3]')

    def test_facts_path_name(self):
        assert EF.facts_path('/tmp/p').name == EF.FACTS_FILENAME


# ---------------------------------------------------------------------------
#  采集器：不需要 SDK 的部分
# ---------------------------------------------------------------------------

class TestProbeSource:
    def test_is_an_init_hook_writing_outside_the_project(self):
        src = EC.probe_source()
        assert 'init 999 python' in src
        assert EC.FACTS_ENV in src          # 写到环境变量指定的路径（项目之外）
        assert 'renpy.list_images' in src
        assert 'get_ordered_image_attributes' in src
        assert 'get_all_labels' in src

    def test_does_not_register_anything(self):
        """探针只读 —— 不注册语句、不改配置。"""
        src = EC.probe_source()
        assert 'register_statement' not in src


class TestClassifyLint:
    def test_splits_three_buckets(self):
        text = ('File "game/a.rpy", line 4: expected statement.\n'
                'game/b.rpy:2 \'lan x\' is not an image.\n'
                'game/c.rpy:9 SG-LINT: 我方判据\n')
        got = EC.classify_lint(text, clean=True)
        assert len(got['parse_errors']) == 1
        assert len(got['semantic_errors']) == 1
        assert len(got['program_errors']) == 1

    def test_dirty_run_reclassifies_semantic_as_parse(self):
        """**坑 2**：有 parse error 时引擎是中止的，语义检查没跑，
        所以那时"没有语义错误"不可信 —— 一律归到阻塞类。"""
        text = 'game/b.rpy:2 \'lan x\' is not an image.\n'
        got = EC.classify_lint(text, clean=False)
        assert got['semantic_errors'] == []
        assert len(got['parse_errors']) == 1

    def test_exception_line_is_parse_whatever_the_wording(self):
        """`File "…", line N:` 是**加载/编译抛异常** —— 不看措辞，一律阻塞。

        2026-10-18：`'{' was never closed` 不在 `_PARSE_MARK` 那 4 个词里，
        从前掉进 `program_errors`（"可修"，不阻塞）。构建闸门那边同一个
        根因已经改成按出处判（`engine_lint`），这里也要一致。
        """
        text = ('File "game/a.rpy", line 13850: \'{\' was never closed\n'
                'define x = {\n')
        got = EC.classify_lint(text, clean=True)
        assert len(got['parse_errors']) == 1, got
        assert not got['program_errors'], got


class TestFindSdk:
    def test_raises_with_actionable_message(self, monkeypatch):
        monkeypatch.setattr(EC, 'DEFAULT_SDK_CANDIDATES', ())
        monkeypatch.delenv(EC.SDK_ENV, raising=False)
        with pytest.raises(FileNotFoundError) as ei:
            EC.find_sdk()
        assert 'SDK' in str(ei.value)

    def test_collect_reports_missing_sdk_instead_of_raising(self, tmp_path,
                                                            monkeypatch):
        monkeypatch.setattr(EC, 'DEFAULT_SDK_CANDIDATES', ())
        monkeypatch.delenv(EC.SDK_ENV, raising=False)
        (tmp_path / 'game').mkdir()
        result = EC.collect(tmp_path)
        assert result['ok'] is False and 'SDK' in result['reason']


class TestManifestAndProbeCleanup:
    def test_manifest_covers_scripts_not_cache(self, tmp_path):
        game = tmp_path / 'game'
        (game / 'cache').mkdir(parents=True)
        (game / 'a.rpy').write_text('label start:\n', encoding='utf-8')
        (game / 'cache' / 'bytecode.rpyb').write_text('x', encoding='utf-8')
        (game / 'pic.png').write_bytes(b'\x89PNG')
        m = EC._sha1_manifest(tmp_path)
        assert 'a.rpy' in m
        assert not any(k.startswith('cache/') for k in m)
        assert not any(k.endswith('.png') for k in m)

    def test_manifest_detects_change(self, tmp_path):
        game = tmp_path / 'game'
        game.mkdir()
        f = game / 'a.rpy'
        f.write_text('label start:\n', encoding='utf-8')
        before = EC._sha1_manifest(tmp_path)
        f.write_text('label start:\n    return\n', encoding='utf-8')
        assert EC._sha1_manifest(tmp_path) != before

    def test_remove_probe_deletes_rpyc_too(self, tmp_path):
        """**N7**：只删 .rpy 会留下孤儿 .rpyc（实测引擎不会自动清）。"""
        game = tmp_path / 'game'
        game.mkdir()
        probe = game / EC.PROBE_FILENAME
        probe.write_text('# probe\n', encoding='utf-8')
        rpyc = game / (EC.PROBE_FILENAME[:-4] + '.rpyc')
        rpyc.write_bytes(b'binary')
        removed = EC._remove_probe(tmp_path)
        assert not probe.exists() and not rpyc.exists(), removed
        assert sorted(removed) == sorted([EC.PROBE_FILENAME, rpyc.name])


# ---------------------------------------------------------------------------
#  采集器：端到端（需要真 SDK；没有就整条 skip）
# ---------------------------------------------------------------------------

#: 最小工程：用 Solid 当图，**不需要任何图片文件**（与地狱测试同形）。
MINI_GAME = '''layeredimage lan:
    group expressions:
        attribute happy default:
            Solid("#0f0")
        attribute sad:
            Solid("#ff0")
    group outfit:
        attribute clothes2:
            Solid("#f00")
        attribute base_swimsuit:
            Solid("#00f")

label start:
    show lan clothes2 happy
    return
'''


def _sdk_or_skip():
    try:
        return EC.find_sdk()
    except FileNotFoundError:
        pytest.skip('本机没有 Ren\'Py SDK —— 采集类用例整条跳过')


def _mini_project(tmp_path, script=MINI_GAME):
    (tmp_path / 'game').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'game' / 'a_script.rpy').write_text(script, encoding='utf-8')
    return tmp_path


class TestCollectEndToEnd:
    """E1 的 6 条验收（用最小工程；真机验收另见 docs 的 E1 清单）。"""

    def test_collects_groups_labels_and_is_collectable(self, tmp_path):
        _sdk_or_skip()
        proj = _mini_project(tmp_path)
        result = EC.collect(proj, mode='copy')
        assert result['ok'], result['reason']
        facts = result['facts']
        coll = facts['collection']
        assert coll['parse_clean'] is True
        assert coll['probe_complete'] is True

        # 属性与**分组**来自引擎（不是我们编的）
        groups = EF.groups_of(facts, 'lan')
        assert groups.get('outfit') == ['clothes2', 'base_swimsuit']
        assert 'happy' in (groups.get('expressions') or [])

        # 候选（引擎排过序的那份）
        assert 'clothes2' in EF.candidates_of(facts, 'lan')

        # label 从探针补（--json-dump 的 location.label 恒为 0）
        assert 'start' in (EF.value_of(facts, 'labels') or [])

        assert EF.validate_facts(facts) == []

    def test_probe_and_rpyc_are_cleaned_up(self, tmp_path):
        _sdk_or_skip()
        proj = _mini_project(tmp_path)
        EC.collect(proj)
        leftovers = sorted(p.name for p in (proj / 'game').glob('zz_storygraph_probe*'))
        assert leftovers == [], leftovers

    def test_parse_error_closes_the_gate(self, tmp_path):
        """**坑 2 / 坑 4**：有 parse error 时既不能采集，也**必须报失败**。"""
        _sdk_or_skip()
        proj = _mini_project(tmp_path, MINI_GAME + '\nlabel bad:\n    weather 雨 0.5\n')
        result = EC.collect(proj)
        assert result['ok'] is False
        assert result['facts']['collection']['probe_complete'] is False
        assert result['reason']

    def test_manifest_unchanged(self, tmp_path):
        _sdk_or_skip()
        proj = _mini_project(tmp_path)
        result = EC.collect(proj)
        assert result['manifest_ok'] is True, '采集动了项目文本文件'

    def test_existing_errors_txt_is_restored(self, tmp_path):
        """用户的 `errors.txt` **不能被我们删掉** —— 是「挪开→跑→还原」。"""
        _sdk_or_skip()
        proj = _mini_project(tmp_path)
        mine = '# 用户自己的错误日志\nkeep me\n'
        (proj / 'errors.txt').write_text(mine, encoding='utf-8')
        EC.collect(proj)
        assert (proj / 'errors.txt').read_text(encoding='utf-8') == mine
        assert not list(proj.glob('errors.txt.sg-parked')), '备份没还原掉'

    def test_no_errors_txt_left_behind_on_clean_project(self, tmp_path):
        """干净工程跑完不该多出 errors.txt（也不该留下任何 .sg-parked）。"""
        _sdk_or_skip()
        proj = _mini_project(tmp_path)
        EC.collect(proj)
        assert not (proj / 'errors.txt').exists()
        assert not list(proj.glob('*.sg-parked'))


# ---------------------------------------------------------------------------
#  守卫：落盘点必须登记
# ---------------------------------------------------------------------------

def test_write_facts_is_the_only_write_point_here():
    """
    `write_facts` 是全包唯一写 `engine_facts.json` 的地方，
    且必须在 `test_single_write_entry.WRITERS` 里登记过（否则那条守卫会红）。
    """
    guard = (Path(__file__).resolve().parent /
             'test_single_write_entry.py').read_text(encoding='utf-8')
    assert "'pipeline/engine_collect.py::write_facts'" in guard
