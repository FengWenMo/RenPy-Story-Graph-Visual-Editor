# -*- coding: utf-8 -*-
"""
B 方案：跨章连续性体检（2026-10-28）
====================================
用户两轮澄清后的**最终判据**：

    **只看后继的单入边 —— N 恰好一条入边就判 P→N；不看 P 有几条出边。**
    （分支点自己的章末状态是确定的：它演完是什么样，不取决于玩家接下来
    选哪条 → 每个分支章继承的都是同一份状态，这是事实不是猜。）

        线性章节（单入边）                → 判
        分支点（多出边）                  → 它自己的入口接缝照判（若单入边）；
                                            从它分出去的每个分支章（单入边）也判
        分支后的第一章（单入边）          → 判（同一份章末状态）
        汇聚点（多入边）                  → 汇进来的接缝**不判**
        后续线性章节                      → 接着判

这个文件把"能判的判对、不能判的绝不猜"逐条钉住 —— 重点是**防假阳性**：
分支汇聚处哪怕各前驱状态完全一样，也不许出现"继承自某一条"的断言。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from storygraph.pipeline.continuity import (
    LEVEL_INFO,
    LEVEL_WARN,
    analyze_project,
    analyze_files,
)
from storygraph.story.beat_model import Beat
from storygraph.story.stage_snapshot import stage_exit_state


# ------------------------------------------------------------------
#  夹具：在临时目录里造一个"编排层 + 内容章"的最小项目
# ------------------------------------------------------------------

def _write(root, name: str, text: str) -> Path:
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def _chain_project(tmp_path, first: str, second: str):
    """
    两章线性项目：start → c1_control → c2_control → return。

    `first` / `second` 是两章 label 的块体（缩进后的语句）。
    """
    _write(tmp_path, 'chapters.rpy', f'''label c1:
{first}
    return

label c2:
{second}
    return
''')
    _write(tmp_path, 'controls.rpy', '''label start:
    call c1_control
    return

label c1_control:
    call c1
    jump c2_control

label c2_control:
    call c2
    return
''')
    return analyze_project(tmp_path)


def _seam(report, from_label, to_label):
    for seam in report.seams:
        if seam.from_label == from_label and seam.to_label == to_label:
            return seam
    raise AssertionError(f'没有接缝 {from_label} → {to_label}；'
                         f'实际有：{[ (s.from_label, s.to_label) for s in report.seams ]}')


def _codes(report, level):
    return {f.code for f in report.findings if f.level == level}


# ------------------------------------------------------------------
#  一、能判的：线性链两章
# ------------------------------------------------------------------

class TestLinearChainLeak:
    def test_music_scene_actors_leak(self, tmp_path):
        """上一章章末有 BGM/背景/立绘，下一章首句前什么都不做 → 三条警告。"""
        rep = _chain_project(
            tmp_path,
            first='    scene bg room\n    show ling happy\n'
                  '    play music song1\n    "c1 的台词"',
            second='    "c2 的第一句"')
        seam = _seam(rep, 'c1', 'c2')
        assert seam.judged is True, seam.reason
        assert seam.outcomes['music'] == 'leak'
        assert seam.outcomes['scene'] == 'leak'
        assert seam.outcomes['actors'] == 'leak'
        codes = _codes(rep, LEVEL_WARN)
        assert {'M1', 'S1', 'C1'} <= codes, codes
        # 链：c1 → c2；c1 是链根（入口 start）
        assert any(chain[:2] == ['c1', 'c2'] for chain in rep.chains), rep.chains
        # 章节出口：c1 的章末状态必须是"确定"的（不是未知）
        c1 = rep.chapter('c1')
        assert 'music' not in c1.exit['unknown']
        assert c1.exit['music'] == 'song1'

    def test_changed_in_first_beat_is_clean(self, tmp_path):
        """下一章第 1 拍就换景/换曲/退场（第一句台词之前）→ 不报。"""
        rep = _chain_project(
            tmp_path,
            first='    scene bg room\n    show ling happy\n'
                  '    play music song1\n    "c1 的台词"',
            second='    scene bg yard\n    hide ling\n'
                   '    play music song2\n    "c2 的第一句"')
        seam = _seam(rep, 'c1', 'c2')
        assert seam.judged is True
        assert seam.outcomes['music'] == 'ok'
        assert seam.outcomes['scene'] == 'ok'
        assert seam.outcomes['actors'] == 'ok'
        assert not _codes(rep, LEVEL_WARN), _codes(rep, LEVEL_WARN)

    def test_changed_at_second_beat_is_info_only(self, tmp_path):
        """第 1 句台词沿用、第 2 拍就换 → 只报事实（info），不是缺陷。"""
        rep = _chain_project(
            tmp_path,
            first='    scene bg room\n    show ling happy\n'
                  '    play music song1\n    "c1 的台词"',
            second='    "c2 的第一句"\n    scene bg yard\n    hide ling\n'
                   '    play music song2\n    "c2 的第二句"')
        seam = _seam(rep, 'c1', 'c2')
        assert seam.outcomes['music'] == 'short'
        assert seam.outcomes['scene'] == 'short'
        assert seam.outcomes['actors'] == 'short'
        assert not _codes(rep, LEVEL_WARN), _codes(rep, LEVEL_WARN)
        info_codes = _codes(rep, LEVEL_INFO)
        assert {'M0', 'S0', 'C0'} <= info_codes, info_codes


# ------------------------------------------------------------------
#  二、分支：从分支点出去的接缝不判；分支后的线性章节接着判
# ------------------------------------------------------------------

_BRANCH_PROJECT = '''label start:
    call b1_control
    return

label b1_control:
    call b1
    menu:
        "选 A":
            jump ba_control
        "选 B":
            jump bb_control

label ba_control:
    call ba
    jump ba2_control

label ba2_control:
    call ba2
    jump m_control

label bb_control:
    call bb
    jump m_control

label m_control:
    call m
    jump after_control

label after_control:
    call after
    return
'''


def _branch_project(tmp_path):
    _write(tmp_path, 'chapters.rpy', '''label b1:
    scene bg room
    show ling happy
    play music song1
    "b1 的台词"
    return

label ba:
    "A 分支的开始"
    return

label ba2:
    "A 分支的第二段"
    return

label bb:
    "B 分支的开始"
    return

label m:
    "汇聚章"
    return

label after:
    "汇聚之后"
    return
''')
    _write(tmp_path, 'controls.rpy', _BRANCH_PROJECT)
    return analyze_project(tmp_path)


class TestBranchAndMerge:
    def test_branch_children_are_judged_by_single_in_edge(self, tmp_path):
        """
        用户 2026-10-28 第二轮裁定：**只看后继的单入边、不管前驱出边**。

        b1 有两条出边，但它的章末状态是**确定的**（演完是什么样，不取决于
        玩家接下来选哪条）→ b1→ba / b1→bb 都要判，而且继承同一份状态。
        """
        rep = _branch_project(tmp_path)
        for to in ('ba', 'bb'):
            seam = _seam(rep, 'b1', to)
            assert seam.judged is True, seam.reason
            assert '单入边' in seam.reason or '入边' in seam.reason
            assert seam.outcomes['music'] == 'leak', seam.outcomes
        # 分支点本身仍要在报告里列出全部去向（事实，给人看）
        branch = [u for u in rep.undetermined if u.kind == 'branch']
        assert branch, [u.title for u in rep.undetermined]
        assert 'ba' in branch[0].message and 'bb' in branch[0].message

    def test_branch_children_share_the_same_exit_state(self, tmp_path):
        """两个分支章继承的是**同一份** b1 章末状态（不是猜"反正一样"）。"""
        rep = _branch_project(tmp_path)
        ba = rep.chapter('ba')
        bb = rep.chapter('bb')
        assert ba.entry == bb.entry, (ba.entry, bb.entry)
        assert rep.chapter('b1').exit['music'] in (ba.entry['music'],
                                                   bb.entry['music'])

    def test_linear_after_branch_still_judged(self, tmp_path):
        """分支后的第一章→第二章（两边都一条边）照判：继承分支后第一章的出口。"""
        rep = _branch_project(tmp_path)
        seam = _seam(rep, 'ba', 'ba2')
        assert seam.judged is True
        assert seam.outcomes['music'] == 'leak'
        codes = _codes(rep, LEVEL_WARN)
        assert 'M1' in codes, codes
        # 链条从分支后第一章继续
        assert any(chain[:2] == ['ba', 'ba2'] for chain in rep.chains), \
            rep.chains

    def test_merge_is_never_judged_and_does_not_guess(self, tmp_path):
        """m 有两条入边（ba2 / bb）→ 汇进来的接缝不判。"""
        rep = _branch_project(tmp_path)
        for from_label in ('ba2', 'bb'):
            seam = _seam(rep, from_label, 'm')
            assert seam.judged is False
            assert '入边' in seam.reason, seam.reason
        merge = [u for u in rep.undetermined if u.kind == 'merge']
        assert merge, [u.title for u in rep.undetermined]
        # 各前驱的章末状态当事实列出来（不合并、不排序、不猜）
        detail = merge[0].detail
        assert any('ba2' in d for d in detail), detail
        assert any('bb' in d for d in detail), detail
        # 汇聚点自己的入口状态未知 → 后面的接缝不报漏章
        m = rep.chapter('m')
        assert 'music' in m.entry['unknown']
        assert 'scene' in m.entry['unknown']

    def test_after_merge_resumes_with_unknown_state(self, tmp_path):
        """汇聚之后的线性章节接缝仍可判，但状态未知 → 不报问题、只报未知。"""
        rep = _branch_project(tmp_path)
        seam = _seam(rep, 'm', 'after')
        assert seam.judged is True
        assert seam.outcomes['music'] == 'unknown'
        assert seam.outcomes['scene'] == 'unknown'
        assert not any(f.title.startswith('m →') for f in rep.findings), \
            [f.title for f in rep.findings]


# ------------------------------------------------------------------
#  三、判不出来的：动态跳转 / 环 / 旁支
# ------------------------------------------------------------------

class TestUndetermined:
    def test_python_dynamic_jump_is_undetermined(self, tmp_path):
        """`$ renpy.jump(x)` → 记"判不出来"，不猜目标、不崩。"""
        _write(tmp_path, 'chapters.rpy', '''label d1:
    scene bg room
    "d1 的台词"
    return
''')
        _write(tmp_path, 'controls.rpy', '''label start:
    call d_control
    return

label d_control:
    call d1
    $ renpy.jump(target)
''')
        rep = analyze_project(tmp_path)
        kinds = {u.kind for u in rep.undetermined}
        assert 'dynamic' in kinds, kinds
        assert any('renpy.jump' in u.message or
                   any('renpy.jump' in d for d in u.detail)
                   for u in rep.undetermined if u.kind == 'dynamic')

    def test_cycle_is_marked_and_not_judged(self, tmp_path):
        """两章互跳成环 → 标"环"，相关接缝不判。"""
        _write(tmp_path, 'chapters.rpy', '''label k1:
    scene bg room
    "k1 的台词"
    return

label k2:
    "k2 的台词"
    return
''')
        _write(tmp_path, 'controls.rpy', '''label start:
    call k1_control
    return

label k1_control:
    call k1
    jump k2_control

label k2_control:
    call k2
    jump k1_control
''')
        rep = analyze_project(tmp_path)
        assert rep.chapter('k1').cycle is True
        assert rep.chapter('k2').cycle is True
        seam = _seam(rep, 'k1', 'k2')
        assert seam.judged is False
        assert '环' in seam.reason
        kinds = {u.kind for u in rep.undetermined}
        assert 'cycle' in kinds, kinds

    def test_side_chapter_is_listed_not_judged(self, tmp_path):
        """从入口走不到的章节（调试入口）→ 列进口袋，不判继承。"""
        _write(tmp_path, 'chapters.rpy', '''label c1:
    "主线的台词"
    return

label debug_ch:
    "调试入口的台词"
    return
''')
        _write(tmp_path, 'controls.rpy', '''label start:
    call c1_control
    return

label c1_control:
    call c1
    return

label debug_control:
    call debug_ch
    return
''')
        rep = analyze_project(tmp_path)
        labels = [ch.label for ch in rep.chapters]
        assert 'c1' in labels
        assert 'debug_ch' not in labels, labels
        side = [u for u in rep.undetermined if u.kind == 'side_branch']
        assert any('debug_ch' in u.title for u in side), [u.title for u in side]

    def test_side_edges_do_not_pollute_main_chain_degrees(self, tmp_path):
        """
        旁支 → 主线 的边**不算进主线的入度/出度**（否则主线会假成"汇聚"）。

        这里调试路径 `debug_ch → c1` 指向主线；c1 应该仍然是链根，
        而不是被当成"有两个前驱"（start 那条 + 调试那条）。
        """
        _write(tmp_path, 'chapters.rpy', '''label c1:
    scene bg room
    play music song1
    "主线的台词"
    return

label debug_ch:
    "调试入口的台词"
    return
''')
        _write(tmp_path, 'controls.rpy', '''label start:
    call c1_control
    return

label c1_control:
    call c1
    return

label debug_control:
    call debug_ch
    jump c1_control
''')
        rep = analyze_project(tmp_path)
        c1 = rep.chapter('c1')
        assert c1.root is True
        assert c1.in_degree == 0, [s.from_label for s in c1.in_edges]
        # 旁支仍如实列在"不判定"里
        side = [u for u in rep.undetermined if u.kind == 'side_branch']
        assert any('debug_ch' in u.title for u in side), [u.title for u in side]


# ------------------------------------------------------------------
#  四、本地事实 / Python 音频 / 未建模声道 / 无台词
# ------------------------------------------------------------------

class TestLocalFacts:
    def test_zero_music_chapters_are_reported_as_fact(self, tmp_path):
        """整章零音乐/零舞台 → 事实（不是缺陷），不能进"需要人看"。"""
        rep = _chain_project(tmp_path, first='    "c1 的台词"',
                             second='    "c2 的台词"')
        codes = _codes(rep, LEVEL_INFO)
        assert 'L1' in codes, codes
        assert 'L2' in codes, codes
        assert not _codes(rep, LEVEL_WARN)

    def test_python_audio_is_undetermined(self, tmp_path):
        """直接调 `renpy.music.play` → 判不出来（不当作 play 语句）。"""
        rep = _chain_project(
            tmp_path,
            first='    scene bg room\n    play music song1\n    "c1"',
            second='    $ renpy.music.play("song2", channel="music")\n'
                   '    "c2 的第一句"')
        kinds = {u.kind for u in rep.undetermined}
        assert 'python_audio' in kinds, kinds
        seam = _seam(rep, 'c1', 'c2')
        assert seam.outcomes['music'] == 'uncertain'
        assert 'M1' not in _codes(rep, LEVEL_WARN)

    def test_unmodeled_channel_is_undetermined(self, tmp_path):
        """`play ambience` 这类未建模声道 → 单独声明，不参与继承判断。"""
        rep = _chain_project(
            tmp_path,
            first='    scene bg room\n    play music song1\n    "c1"',
            second='    play ambience rain\n    "c2 的第一句"')
        kinds = {u.kind for u in rep.undetermined}
        assert 'unmodeled_channel' in kinds, kinds


# ------------------------------------------------------------------
#  五、输出：JSON 可序列化、稳定、空项目不炸
# ------------------------------------------------------------------

class TestReportOutput:
    def test_render_and_json(self, tmp_path):
        rep = _branch_project(tmp_path)
        text = rep.render_text()
        assert '跨章连续性体检' in text
        assert '【确定链】' in text
        assert '【不判定（不猜）】' in text
        data = json.loads(json.dumps(rep.to_dict(), ensure_ascii=False))
        assert data['stats']['chapters'] == len(rep.chapters)
        assert isinstance(data['chapters'][0]['exit']['unknown'], list)
        # 每个章节都有可序列化的出口状态
        for ch in data['chapters']:
            assert 'unknown' in ch['exit']

    def test_two_runs_are_deterministic(self, tmp_path):
        rep1 = _branch_project(tmp_path).to_dict()
        rep2 = _branch_project(tmp_path).to_dict()
        rep1.pop('elapsed')
        rep2.pop('elapsed')
        assert rep1 == rep2

    def test_empty_project_does_not_crash(self, tmp_path):
        rep = analyze_project(tmp_path)
        assert rep.chapters == []
        assert '没有识别到章节' in rep.render_text()

    def test_analyze_files_accepts_explicit_list(self, tmp_path):
        _write(tmp_path, 'a.rpy', 'label c1:\n    "台词"\n    return\n')
        rep = analyze_files([tmp_path / 'a.rpy'], project_root=str(tmp_path))
        assert rep.labels_total == 1


# ------------------------------------------------------------------
#  六、跨章状态推导（stage_snapshot 的可选入口状态）
# ------------------------------------------------------------------

class _StateProject:
    pass


class TestStageExitState:
    def _unknown(self):
        return {'scene': '', 'actors': [], 'layers': {}, 'music': '',
                'voice': '', 'unknown': set(
                    ('scene', 'actors', 'layers', 'music', 'voice'))}

    def test_scene_and_music_become_known(self):
        beats = [Beat(seq=1, label='x', scene='bg room', show='ling happy',
                      music='song1')]
        state = stage_exit_state(beats, initial_state=self._unknown())
        assert state['scene'] == 'bg room'
        assert state['actors'] == [['ling happy', '']]
        assert state['music'] == 'song1'
        assert 'scene' not in state['unknown']
        assert 'actors' not in state['unknown']
        assert 'music' not in state['unknown']
        # 这一拍没碰图层 → 图层仍然未知（不猜）
        assert 'layers' in state['unknown']

    def test_stop_music_makes_it_known_silence(self):
        beats = [Beat(seq=1, label='x', music='', music_action='stop')]
        state = stage_exit_state(beats, initial_state=self._unknown())
        assert state['music'] == ''
        assert 'music' not in state['unknown']

    def test_no_beats_returns_entry_state(self):
        init = {'scene': 'bg room', 'actors': [['ling happy', '']],
                'layers': {}, 'music': 'song1', 'voice': '',
                'unknown': {'layers'}}
        state = stage_exit_state([], initial_state=init)
        assert state['scene'] == 'bg room'
        assert state['music'] == 'song1'
        assert 'layers' in state['unknown']

    def test_snapshot_default_has_no_unknown_key(self):
        from storygraph.story.stage_snapshot import snapshot_rows
        row = snapshot_rows([Beat(seq=1, label='x', scene='bg room')])[0]
        assert 'unknown' not in row['state']
        assert row['scene'] == 'bg room'


# ------------------------------------------------------------------
#  七、真机（只读）：真实项目的确定链与汇聚点
# ------------------------------------------------------------------

#: 与其它真机测试同一份路径（`STORYGRAPH_REAL_PROJECT` 可覆盖，换机器自动 skip）。
from _source_paths import REAL_PROJECT as _REAL  # noqa: E402


@pytest.mark.skipif(not _REAL.is_dir(), reason='本机没有真实项目 sr_general')
class TestRealProject:
    def test_real_chains_and_merge(self):
        rep = analyze_project(_REAL)
        labels = {ch.label for ch in rep.chapters}
        assert 'chapter1_4' in labels
        assert 'chapter2_1' in labels
        # 1.4 → 2.1 的漏章（音乐 + 背景）必须抓到
        codes = {f.code for f in rep.findings if f.level == LEVEL_WARN}
        assert 'M1' in codes, [f.title for f in rep.findings]
        assert {'chapter1_4', 'chapter2_1'} <= set(
            next(f.chapters for f in rep.findings if f.code == 'M1'))
        # 2.2 是 4 条入边的汇聚点 → 不判
        merge = [u for u in rep.undetermined if u.kind == 'merge'
                 and 'chapter2_2' in u.title]
        assert merge, [u.title for u in rep.undetermined]
        assert len(merge[0].detail) >= 4, merge[0].detail
        # 分支点 2.1 的 4 条出边：每个分支章只有一条入边 → **判**（用户第二轮裁定）
        for child in ('chapter2_1_1', 'chapter2_1_2', 'chapter2_1_3',
                      'chapter2_1_4'):
            seam = _seam(rep, 'chapter2_1', child)
            assert seam.judged is True, seam.reason
            assert seam.outcomes['music'] == 'leak', (child, seam.outcomes)
        # 汇聚之后那条接缝（2.2 的某个子章 → 2.3）不判
        merge_in = _seam(rep, 'chapter2_1_1', 'chapter2_2')
        assert merge_in.judged is False
        assert '入边' in merge_in.reason

    def test_real_analysis_is_read_only(self):
        files = sorted((_REAL / 'game').rglob('*.rpy'))
        before = {str(p): (p.stat().st_mtime_ns, p.stat().st_size)
                  for p in files}
        analyze_project(_REAL)
        after = {str(p): (p.stat().st_mtime_ns, p.stat().st_size)
                 for p in files}
        assert before == after, '跨章分析竟然改了文件（必须只读）'
