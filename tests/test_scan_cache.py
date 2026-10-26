"""
tests/test_scan_cache.py
========================
**"每一次连线都很卡"的判据**（用户 2026-10-26，真机 `sr_general`）。

报的是手感，根因是算法：接线窗口要问"编排层是哪个文件 / 这一章的 `_control`
包装在不在 / 谁 jump 了这一章"，每个问题都要读一遍全部 `.rpy`，其中
`detect_placement` 还要把每个文件**整份解析**。真机账（91 个文件 / 1.2 MB）：

    detect_placement(root)                1435 ms
    find_control_file(root)               1498 ms   ← 里面又调一次上面那个
    plan_control_route（起点章没包装）      3389 ms   ← 上面两样 ×2
    一次连线（开窗 + 点创建）              10~20 s

这一组钉两件事：

1. **单候选不再跑排序**：只有一个文件带 `*_control` 时，`find_control_file`
   必须**一次 `detect_placement` 都不调**（排序只有一个元素时结论相同）；
2. **同一份项目不重复读盘**：第二次算出来的结论必须与第一次一致，但
   文件一次都不许重读/重解析 —— 而文件**改了**之后必须立刻看见新结论。

第 2 条的后半句是这类缓存最容易翻车的地方（缓存了却不失效 = "改了没反应"），
所以每条缓存都配一条"改文件 → 结论跟着变"的用例。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.story import scan_cache  # noqa: E402
from storygraph.story.control_route import (  # noqa: E402
    find_control_file, plan_control_route)
from storygraph.story.placement import detect_placement  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_cache():
    """每条用例从空缓存开始，结束后也清 —— 免得把上一条的运气当结论。"""
    scan_cache.clear()
    yield
    scan_cache.clear()


def _project(tmp_path: Path, files: dict) -> Path:
    root = tmp_path / 'proj'
    game = root / 'game'
    game.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        fp = game / name
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(text, encoding='utf-8')
    return root


#: 一个"真在讲故事"的章节（有 say 语句 = 内容文件）
CHAPTER = """
label ch1:
    "第一句台词"
    return
"""

#: 编排层（只有 jump/call，没有演出 = 纯控制流）
CONTROL = """
label ch1_control:
    call ch1
    return
"""


class TestSingleCandidateNeverRanks:
    """只有一个编排层文件时，排序的三个判据（精确 / placement / 文件名）
    一个都改变不了答案 —— 那就一个都别跑。"""

    def test_one_control_file_skips_detect_placement(self, tmp_path,
                                                    monkeypatch):
        root = _project(tmp_path, {'ch1.rpy': CHAPTER, 'flow.rpy': CONTROL})

        def _boom(*_a, **_k):
            raise AssertionError('只有一个候选，不该为了排序去整份解析全部脚本')

        monkeypatch.setattr('storygraph.story.placement.detect_placement',
                            _boom)
        # 没有 src（接线窗口要显示"章末（编排层）"时就是这么问的）
        assert find_control_file(root) == (root / 'game' / 'flow.rpy')
        # 有 src、但它没有 `_control` 包装（真机上 35/45 章都是这一档）
        assert find_control_file(root, 'ch1') == (root / 'game' / 'flow.rpy')

    def test_no_control_file_returns_none_without_ranking(self, tmp_path,
                                                          monkeypatch):
        root = _project(tmp_path, {'ch1.rpy': CHAPTER})

        def _boom(*_a, **_k):
            raise AssertionError('没有任何编排层，不该去解析全部脚本')

        monkeypatch.setattr('storygraph.story.placement.detect_placement',
                            _boom)
        assert find_control_file(root) is None
        assert find_control_file(root, 'ch1') is None


class TestMultipleCandidatesKeepTheOldOrder:
    """两个候选以上时，原来的排序**一个字都不许变**（缓存只省重复读，不改结论）。"""

    def test_exact_control_label_wins(self, tmp_path):
        root = _project(tmp_path, {
            'ch1.rpy': CHAPTER,
            'a_flow.rpy': CONTROL,
            'b_flow.rpy': 'label other_control:\n    call ch1\n    return\n',
        })
        # `src_label_control` 在哪，就选哪个文件
        assert find_control_file(root, 'other') == (
            root / 'game' / 'b_flow.rpy')
        assert find_control_file(root, 'ch1') == (root / 'game' / 'a_flow.rpy')

    def test_name_rank_still_applies(self, tmp_path):
        root = _project(tmp_path, {
            'ch1.rpy': CHAPTER,
            'a_flow.rpy': 'label q_control:\n    return\n',
            'z_controller.rpy': 'label w_control:\n    return\n',
        })
        # 两个都不是"纯控制流文件"（只有 return、没有 jump/call），精确匹配
        # 也不成立 → 落到旧的"文件名带 control/controller 的优先"。
        assert find_control_file(root, 'ch1') == (
            root / 'game' / 'z_controller.rpy')


class TestCacheInvalidatesOnRealChanges:
    """缓存必须**看得见文件改动** —— 否则修完源文件还得重启工具才生效。"""

    def test_adding_a_control_label_is_seen(self, tmp_path):
        root = _project(tmp_path, {'ch1.rpy': CHAPTER})
        assert find_control_file(root) is None
        flow = root / 'game' / 'flow.rpy'
        flow.write_text(CONTROL, encoding='utf-8')
        assert find_control_file(root) == flow

    def test_removing_the_control_label_is_seen(self, tmp_path):
        root = _project(tmp_path, {'ch1.rpy': CHAPTER, 'flow.rpy': CONTROL})
        flow = root / 'game' / 'flow.rpy'
        assert find_control_file(root) == flow
        flow.write_text('label plain:\n    call ch1\n    return\n',
                        encoding='utf-8')
        assert find_control_file(root) is None

    def test_appending_a_control_wrapper_is_seen_by_plan(self, tmp_path):
        """真机最常见的动作：把 `ch2_control` 补进编排层 → 立刻能算章末落点。"""
        root = _project(tmp_path, {
            'ch1.rpy': CHAPTER, 'flow.rpy': CONTROL,
            'ch2.rpy': 'label ch2:\n    "二"\n    return\n'})
        plan = plan_control_route(root, 'ch1', 'ch1')
        assert not plan.ok                      # 同一章，拦住了

        # 目标章还没有 `ch2_control`：算得出"要补什么"，但这一条写不进去
        plan = plan_control_route(root, 'ch1', 'ch2')
        assert not plan.ok and 'ch2_control' in plan.reason

        flow = root / 'game' / 'flow.rpy'
        flow.write_text(
            CONTROL + '\nlabel ch2_control:\n    call ch2\n    return\n',
            encoding='utf-8')
        plan = plan_control_route(root, 'ch1', 'ch2')
        assert plan.ok, plan.reason
        assert plan.kind == 'replace' and 'ch2_control' in plan.target_label


    def test_project_created_after_first_scan_is_seen(self, tmp_path):
        """"项目还没建"时问过一次，不许把"什么都没有"记住。"""
        root = tmp_path / 'later'
        assert detect_placement(root).content_files == []
        (root / 'game').mkdir(parents=True)
        (root / 'game' / 'ch1.rpy').write_text(CHAPTER, encoding='utf-8')
        assert detect_placement(root).content_files == ['game/ch1.rpy']


class TestRepeatedScansDoNotTouchDiskAgain:
    """同一份项目、文件没变 → 第二次只许 stat，不许再读全文 / 再解析。"""

    def test_plan_does_not_reread_scripts(self, tmp_path, monkeypatch):
        both = ('\nlabel ch1_control:\n    call ch1\n    return\n'
                '\nlabel ch2_control:\n    call ch2\n    return\n')
        root = _project(tmp_path, {
            'ch1.rpy': CHAPTER,
            'ch2.rpy': 'label ch2:\n    "二"\n    return\n',
            'flow.rpy': both})
        first = plan_control_route(root, 'ch1', 'ch2')
        assert first.ok, first.reason

        reads = []
        from storygraph.story import source_style
        real = source_style.read_text

        def _count(path):
            reads.append(str(path))
            return real(path)

        monkeypatch.setattr(source_style, 'read_text', _count)
        second = plan_control_route(root, 'ch1', 'ch2')
        assert reads == [], f'第二次还在读盘：{reads}'
        assert second.kind == first.kind
        assert second.target_label == first.target_label
        assert second.line_no == first.line_no

    def test_detect_placement_does_not_reparse(self, tmp_path, monkeypatch):
        root = _project(tmp_path, {'ch1.rpy': CHAPTER})
        first = detect_placement(root)
        assert first.content_files == ['game/ch1.rpy']

        from storygraph.parsing.renpy_parser import RenPyParser
        calls = []
        real = RenPyParser.parse

        def _count(self, *a, **k):
            calls.append(a)
            return real(self, *a, **k)

        monkeypatch.setattr(RenPyParser, 'parse', _count)
        second = detect_placement(root)
        assert calls == [], f'第二次还在解析：{calls}'
        assert second.to_dict() == first.to_dict()

    def test_edited_file_is_reparsed_not_served_from_cache(self, tmp_path):
        root = _project(tmp_path, {'ch1.rpy': CHAPTER})
        assert detect_placement(root).content_files == ['game/ch1.rpy']
        (root / 'game' / 'ch1.rpy').write_text(
            'label ch1:\n    jump ch2\n    return\n', encoding='utf-8')
        after = detect_placement(root)
        assert after.content_files == []          # 改成纯路由了
        assert after.evidence['control_only_files'] == ['game/ch1.rpy']


class TestCacheIsBounded:
    """缓存不许无限长（开一百个项目也不会把内存撑爆）。"""

    def test_entries_are_capped(self, tmp_path):
        assert scan_cache.MAX_ENTRIES > 0
        root = tmp_path / 'p'
        (root / 'game').mkdir(parents=True)
        for i in range(scan_cache.MAX_ENTRIES + 40):
            (root / 'game' / f'c{i}.rpy').write_text(
                f'label c{i}:\n    "台词 {i}"\n    return\n', encoding='utf-8')
        scan_cache.clear()
        for fp in sorted((root / 'game').glob('*.rpy')):
            scan_cache.labels_of(fp)
        assert scan_cache.stats()['labels'] <= scan_cache.MAX_ENTRIES
