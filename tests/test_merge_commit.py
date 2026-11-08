# -*- coding: utf-8 -*-
"""
tests/test_merge_commit.py
==========================
**别人也动了这个文件**（2026-11-08 第十四轮）。

上一轮把"退回去"补齐了；这一轮治另一半：计划算完之后文件被别人改过时，
从前**只有一条路 —— 拒写**。于是用户的操作变成"被退回、还得重新载入重做"，
哪怕别人改的那一行离我要动的地方有三百行远。

三句话概括这一轮的规矩：

  1. 盘上还是我算计划时那份 → 照写（与从前逐字节一致，不许多一个字节）；
  2. 别人改的是**别处** → 三路合并：他的留下、我的落上，**并且说出来**；
  3. 撞在同一处（他改的正是我要动的那行、或紧贴着我删的区间）→ 一个字不写。

⚠ 第 3 条里的"紧贴着"是**故意保守**的：他插在我删掉那行的下一行，先后顺序
认不出（`write_gate._touch` 的插入判据），工具不猜。

判据分四组：`commit_text` 本体 / 各家族接上之后的行为 / 撤销那道闸 /
"合并了要吭声"。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, str(Path(__file__).resolve().parent))

from storygraph.story.write_gate import (  # noqa: E402
    GATE_CONFLICT, MERGED, SAME, WROTE, Commit, Kept, commit_text,
    edits_between, merge_text)

SRC = '''label ch01:
    e "第一句"
    e "第二句"
    jump roof
    return

label roof:
    e "屋顶"
    return
'''

#: 我要做的改动：删掉 `jump roof`（与 `source_line_delete` 家族同一种形状）
DRAFT = SRC.replace('    jump roof\n', '')

#: 别人改在**别处**（另一个 label 里加一行）
THEIR_FAR = SRC.replace('label roof:\n    e "屋顶"',
                        'label roof:\n    $ flag = True\n    e "屋顶"')

#: 别人改在**同一处**（给要删的那行加个注释）
THEIR_SAME = SRC.replace('    jump roof\n', '    jump roof  # 先别删\n')

#: 别人**紧贴**着我删的那行下面插一行
THEIR_ADJACENT = SRC.replace('    jump roof\n    return\n',
                             '    jump roof\n    $ flag = True\n    return\n')


def project(tmp_path, body=SRC, name='ch01.rpy'):
    """一个最小项目：`<root>/game/<name>`（`.storygraph/` 要能建在 root 下）。"""
    root = tmp_path / 'proj'
    game = root / 'game'
    game.mkdir(parents=True, exist_ok=True)
    fp = game / name
    fp.write_text(body, encoding='utf-8')
    return root, fp


# ===========================================================================
#  一、`commit_text` 本体：四种结局
# ===========================================================================

class TestCommitStates:
    def test_untouched_file_is_written_as_before(self, tmp_path):
        """盘上还是基线 → 照写，内容与"自己拼一份"逐字节相同。"""
        _root, fp = project(tmp_path)
        assert fp.read_text(encoding='utf-8') == SRC
        c = commit_text(fp, SRC, DRAFT)
        assert c.state == WROTE and c.ok and not c.merged
        assert c.wrote_bytes is True
        assert fp.read_text(encoding='utf-8') == DRAFT
        assert c.kept == () and c.note() == '' and c.error() == ''

    def test_already_written_is_not_written_twice(self, tmp_path):
        """
        盘上已经是草稿 → `same`，**一个字节都不碰**。

        为什么要这一态：再写一遍会白烧一代历史版本、让 mtime 跳一下
        （第十轮给 `ext.registry.save` 立过同一条规矩）。
        """
        _root, fp = project(tmp_path, body=DRAFT)
        before = fp.stat().st_mtime_ns
        c = commit_text(fp, SRC, DRAFT)
        assert c.state == SAME and c.ok
        assert c.wrote_bytes is False
        assert fp.stat().st_mtime_ns == before, '说好不写却动了 mtime'

    def test_disjoint_changes_are_merged(self, tmp_path):
        """两边不重叠 → 合并：他的那一行留着，我删的那行真删掉。"""
        _root, fp = project(tmp_path, body=THEIR_FAR)
        c = commit_text(fp, SRC, DRAFT)
        assert c.state == MERGED and c.ok and c.merged
        now = fp.read_text(encoding='utf-8')
        assert '$ flag = True' in now, '别人的改动被吞了'
        assert 'jump roof' not in now, '我的改动没落上'
        assert c.kept and len(c.kept) == 1
        assert '$ flag = True' in c.kept[0].head
        # 短句进 Toast：必须**短**（那是一行 `wordWrap=False` 的浮层，超宽被裁）
        note = c.note()
        assert '保住了' in note
        assert len(note) <= 40, f'Toast 一句话放不下：{len(note)} 字'
        # 长句进日志：第几行、什么内容、"界面还是旧的"都在这里
        full = c.full_note()
        assert '$ flag = True' in full and '重新载入' in full
        assert len(full) > len(note)

    def test_same_line_is_refused_and_nothing_written(self, tmp_path):
        """撞在同一行 → 拒写，逐字节不动，并指出撞在第几行。"""
        _root, fp = project(tmp_path, body=THEIR_SAME)
        before = fp.read_bytes()
        c = commit_text(fp, SRC, DRAFT)
        assert c.state == GATE_CONFLICT and not c.ok
        assert fp.read_bytes() == before, '拒写却动了文件'
        assert c.final_text == ''
        err = c.error()
        assert '第 4 行' in err and '没有写' in err, err

    def test_adjacent_insert_is_refused_on_purpose(self, tmp_path):
        """
        他插在我删掉那行的**下一行** → 仍然拒写。

        这是**故意**保守：删一行 + 紧贴着插一行，先后顺序没有唯一解
        （`_touch` 里插在替换区间端点上算"碰到"）。宁可让人二选一，
        也别猜一个顺序。
        """
        _root, fp = project(tmp_path, body=THEIR_ADJACENT)
        before = fp.read_bytes()
        c = commit_text(fp, SRC, DRAFT)
        assert c.state == GATE_CONFLICT
        assert fp.read_bytes() == before

    def test_crlf_and_bom_survive_a_merge(self, tmp_path):
        """CRLF + BOM 的文件合完必须还是 CRLF + BOM（逐字节看，不看文本）。"""
        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True)
        fp = root / 'game' / 'ch01.rpy'
        fp.write_bytes(b'\xef\xbb\xbf'
                       + THEIR_FAR.replace('\n', '\r\n').encode('utf-8'))
        c = commit_text(fp, SRC, DRAFT)
        assert c.state == MERGED
        raw = fp.read_bytes()
        assert raw.startswith(b'\xef\xbb\xbf'), 'BOM 丢了'
        assert b'\r\n' in raw and b'\n' not in raw.replace(b'\r\n', b''), \
            'LF 混进来了（逐行还原失效）'
        assert b'$ flag = True\r\n' in raw
        assert b'jump roof' not in raw

    def test_a_merge_stores_exactly_one_old_version(self, tmp_path):
        """合并写盘只留**一代**旧版本（写之前那份），不是两代。"""
        from storygraph.story.version_store import versions_of
        _root, fp = project(tmp_path, body=THEIR_FAR)
        c = commit_text(fp, SRC, DRAFT)
        assert c.state == MERGED
        versions = versions_of(fp)
        assert len(versions) == 1
        assert versions[0].path.read_text(encoding='utf-8') == THEIR_FAR

    def test_history_can_be_switched_off(self, tmp_path):
        """
        `history=False`（非 `.rpy` 的调用方）→ 不建版本目录。

        两条写路都要试：**照写**（盘上还是基线）与**合并**（别人改过）——
        它们是两次独立的 `write_text_atomic` 调用，只测一条会漏掉另一条。
        """
        root, fp = project(tmp_path)
        commit_text(fp, SRC, DRAFT, history=False)
        assert not (root / '.storygraph' / 'versions').exists()

        root2, fp2 = project(tmp_path / 'b', body=THEIR_FAR)
        assert commit_text(fp2, SRC, DRAFT, history=False).state == MERGED
        assert not (root2 / '.storygraph' / 'versions').exists()

    def test_non_utf8_is_refused_before_any_write(self, tmp_path):
        """
        盘上那份不是合法 UTF-8 → **拒写**（`SourceNotUtf8`），一个字节不动。

        姿势：坏字节在**别处**（别人加的一行注释里），我要删的行跟他没冲突，
        于是走到"真要写"那一步 —— 严格解码在 `style.apply` 里发生，
        整次写盘作废（第五轮审计 P0-B 的那道闸，在合并路上同样有效）。
        """
        from storygraph.story.source_style import SourceNotUtf8
        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True)
        fp = root / 'game' / 'ch01.rpy'
        bad = SRC.encode('utf-8') + b'# \xe9\xff\n'
        fp.write_bytes(bad)
        with pytest.raises(SourceNotUtf8):
            commit_text(fp, SRC, DRAFT)
        assert fp.read_bytes() == bad

    def test_as_dict_is_plain_data(self, tmp_path):
        """`as_dict()` 全是可序列化的普通类型（回执/日志要能直接 dump）。"""
        import json
        _root, fp = project(tmp_path, body=THEIR_FAR)
        c = commit_text(fp, SRC, DRAFT)
        data = c.as_dict()
        assert json.loads(json.dumps(data))['state'] == MERGED
        assert data['kept'] and 'label' in data['kept'][0]

    def test_sentence_says_which_state(self, tmp_path):
        _root, fp = project(tmp_path, body=THEIR_FAR)
        assert '两边都落上' in commit_text(fp, SRC, DRAFT).sentence()


# ===========================================================================
#  二、`Kept` / `edits_between` 说人话的那一层
# ===========================================================================

class TestEditsAndKept:
    def test_edits_between_is_the_public_single_source(self):
        base = ['a', 'b', 'c']
        other = ['a', 'X', 'c', 'd']
        assert edits_between(base, other) == [(1, 2, ['X']), (3, 3, ['d'])]

    def test_insert_at_start_says_the_file_start(self):
        assert Kept(start=0, end=0, lines=('# hi',)).label == '文件开头加了 1 行「# hi」'

    def test_insert_in_middle_says_after_which_line(self):
        assert Kept(start=3, end=3, lines=('$ x = 1',)).label == \
            '第 3 行后面加了 1 行「$ x = 1」'

    def test_replace_says_the_range(self):
        label = Kept(start=3, end=5, lines=('jump b',)).label
        assert label == '第 3–5 行改成了「jump b」', label

    def test_long_line_is_shortened(self):
        kept = Kept(start=1, end=1, lines=('x' * 120,))
        assert len(kept.head) <= 40 and kept.head.endswith('…')

    def test_merge_text_is_still_the_only_judgement(self):
        """`commit_text` 的"能不能合"必须来自 `merge_text`，不许多一套判据。"""
        assert merge_text(SRC, THEIR_FAR, DRAFT)
        assert merge_text(SRC, THEIR_SAME, DRAFT) == ''


# ===========================================================================
#  三、接在家族上：删源行（章节脚本那条路）
# ===========================================================================

def _line_id_of(fp: Path, raw: str) -> str:
    from storygraph.app.editor import EditorSession
    session = EditorSession(project_root=fp.parent.parent)
    session.load(fp)
    beat = next(b for b in session.beats
                if str(getattr(b, 'raw', '') or '').strip() == raw)
    return beat.line_id


class TestSourceLineDelete:
    def _plan(self, tmp_path, body=SRC):
        from storygraph.app.source_line_delete import plan_source_line_delete
        root, fp = project(tmp_path, body=body)
        line_id = _line_id_of(fp, 'jump roof')
        plan = plan_source_line_delete(root, fp, line_id)
        assert plan.ok, plan.reason
        return fp, plan

    def test_change_elsewhere_is_merged(self, tmp_path):
        from storygraph.app.source_line_delete import (
            apply_source_line_delete)
        fp, plan = self._plan(tmp_path)
        fp.write_text(THEIR_FAR, encoding='utf-8')
        res = apply_source_line_delete(plan)
        assert res['ok'] is True, res
        now = fp.read_text(encoding='utf-8')
        assert '$ flag = True' in now and 'jump roof' not in now
        assert res.get('note'), '合并了却不吭声'
        assert res['commit']['state'] == MERGED

    def test_same_line_is_refused_without_touching_the_file(self, tmp_path):
        from storygraph.app.source_line_delete import (
            apply_source_line_delete)
        fp, plan = self._plan(tmp_path)
        fp.write_text(THEIR_SAME, encoding='utf-8')
        before = fp.read_bytes()
        res = apply_source_line_delete(plan)
        assert res['ok'] is False
        assert '又被改过' in res['error']
        assert fp.read_bytes() == before

    def test_undo_is_still_refused_after_a_merge(self, tmp_path):
        """
        合并之后 Ctrl+Z → **必须拒绝**（走真命令的 `undo()`）。

        撤销那道闸比的是"盘上还是我写的那份吗"（`self.plan.new_text`）。
        合并结果 ≠ 我写的那份，所以闸会拦住 —— 这正是要的：不然一按 Ctrl+Z
        就把别人那几行抹了。**回执里的 `new_text` 也必须是草稿**，不是合并
        结果（否则将来哪个调用方拿它去比，闸就形同虚设）。
        """
        from storygraph.app.source_line_delete import (
            apply_source_line_delete)
        from storygraph.graph.commands import DeleteSourceLineCommand
        from storygraph.graph.models import Graph

        fp, plan = self._plan(tmp_path)
        fp.write_text(THEIR_FAR, encoding='utf-8')
        res = apply_source_line_delete(plan)
        assert res['ok'] and res['new_text'] == plan.new_text, \
            '回执把合并结果当成了"我写的那份"——撤销那道闸会因此放行'
        merged_bytes = fp.read_bytes()

        cmd = DeleteSourceLineCommand(_Host(fp.parent.parent, Graph()), plan, 0)
        cmd.plan = plan
        with pytest.raises(RuntimeError):
            cmd.undo()
        assert fp.read_bytes() == merged_bytes, '撤销被拦住了却还是写了盘'
        assert '$ flag = True' in fp.read_text(encoding='utf-8')


# ===========================================================================
#  四、接在家族上：编排层（补包装 / 分支 / 接线）
# ===========================================================================

CONTROL = ('label ch01_control:\n    call ch01\n    return\n\n'
           'label ch02_control:\n    call ch02\n    return\n')


def control_project(tmp_path, body=CONTROL):
    root = tmp_path / 'proj'
    (root / 'game' / 'script').mkdir(parents=True)
    (root / 'game' / 'script' / 'ch01.rpy').write_text(
        'label ch01:\n    e "一"\n    return\n', encoding='utf-8')
    (root / 'game' / 'script' / 'ch02.rpy').write_text(
        'label ch02:\n    e "二"\n    return\n', encoding='utf-8')
    path = root / 'game' / 'controller.rpy'
    path.write_text(body, encoding='utf-8')
    return root, path


class TestControlLayer:
    def test_init_change_elsewhere_is_merged(self, tmp_path):
        """补 `_control` 包装 + 别人在**中间**加一行 → 两边都在。"""
        from storygraph.story.control_route import (apply_control_init,
                                                    plan_control_init)
        body = ('label ch01_control:\n    call ch01\n    return\n\n'
                'label ch03_control:\n    call ch03\n    return\n')
        root, path = control_project(tmp_path, body)
        plan = plan_control_init(root, 'ch01')
        assert plan.ok, plan.reason
        # 别人在 ch03 里加一行（离"文件末尾追加"很远）
        path.write_text(body.replace('label ch03_control:\n',
                                     'label ch03_control:\n    $ y = 2\n'),
                        encoding='utf-8')
        res = apply_control_init(plan)
        assert res['ok'] is True, res
        text = path.read_text(encoding='utf-8')
        assert '$ y = 2' in text, '别人的改动被吞了'
        assert text.rstrip().endswith('return'), '包装没补在末尾'

    def test_branch_change_elsewhere_is_merged(self, tmp_path):
        from storygraph.story.control_branch import (apply_control_branch,
                                                     branch_marker,
                                                     plan_control_branch)
        root, path = control_project(tmp_path)
        plan = plan_control_branch(root, 'ch01', 'ch02', 'jump',
                                   condition='flag', edge_id=21)
        assert plan.ok, plan.reason
        path.write_text(CONTROL + '\n# 别人加的注释\n', encoding='utf-8')
        res = apply_control_branch(plan)
        assert res['ok'] is True, res
        text = path.read_text(encoding='utf-8')
        assert '# 别人加的注释' in text and f'{branch_marker(21)}' in text
        assert res.get('note')
        # 回执里的 new_text 必须是**草稿**（撤销那道闸拿它比，见
        # `TestSourceLineDelete.test_undo_is_still_refused_after_a_merge`）
        assert res['new_text'] == plan.new_text

    def test_route_anchor_moved_is_found_again(self, tmp_path):
        """
        接线：别人在锚点**上面**插一行 → 锚点按内容认回来，他那一行留着。

        `return` 在每个 `_control` 里都有，所以只在**它自己那个 label 块**里找
        （`control_route._label_block`）—— 这一条钉住"别认到别的 label 里"。
        """
        from storygraph.story.control_route import (apply_control_route,
                                                    plan_control_route)
        root, path = control_project(tmp_path)
        plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
        path.write_text(CONTROL.replace('    call ch01\n',
                                        '    call ch01\n    $ x = 1\n'),
                        encoding='utf-8')
        res = apply_control_route(plan)
        assert res['ok'] is True, res
        text = path.read_text(encoding='utf-8')
        assert ('label ch01_control:\n    call ch01\n    $ x = 1\n'
                '    jump ch02_control\n') in text
        assert text.count('    call ch01\n') == 1

    def test_route_anchor_changed_is_refused(self, tmp_path):
        from storygraph.story.control_route import (apply_control_route,
                                                    plan_control_route)
        root, path = control_project(tmp_path)
        plan = plan_control_route(root, 'ch01', 'ch02', 'jump')
        path.write_text(CONTROL.replace('    return\n',
                                        '    jump ch09_control\n', 1),
                        encoding='utf-8')
        before = path.read_bytes()
        res = apply_control_route(plan)
        assert res['ok'] is False and '不在了' in res['error']
        assert path.read_bytes() == before

    def test_unroute_keeps_someone_elses_line(self, tmp_path):
        """断线：别人在同一文件**别处**加一行 → 他的留着，我要删的那行删掉。"""
        from storygraph.story.control_route import (apply_control_unroute,
                                                    plan_control_unroute)
        body = CONTROL.replace('    call ch01\n    return\n',
                               '    call ch01\n    jump ch02_control\n')
        root, path = control_project(tmp_path, body)
        plan = plan_control_unroute(root, 'ch01', 'ch02', 'jump')
        assert plan.ok, plan.reason
        path.write_text(body.replace('label ch02_control:\n',
                                     'label ch02_control:\n    $ z = 3\n'),
                        encoding='utf-8')
        res = apply_control_unroute(plan)
        assert res['ok'] is True, res
        text = path.read_text(encoding='utf-8')
        assert '$ z = 3' in text and 'jump ch02_control' not in text


# ===========================================================================
#  五、"合并了要吭声" —— 真命令 + notify
# ===========================================================================

class _Host:
    """够 `DeleteSourceLineCommand` 用的最小宿主。"""

    def __init__(self, root, graph, project_root=None):
        self._project_root = project_root if project_root is not None else root
        self.graph = graph
        self.status: list = []
        self.logs: list = []

    def _status(self, msg):
        self.status.append(str(msg))

    def _log(self, msg):
        self.logs.append(str(msg))

    def _set_graph(self, graph, keep_view=None):
        self.graph = graph

    def on_graph_structure_changed(self):
        pass


def test_the_command_tells_the_user_when_it_merged(tmp_path, notify_calls):
    """
    真 `DeleteSourceLineCommand.execute()`：合并了 → Toast 一句短的、
    **日志**一句长的（这是两处不同的显示，缺一个用户就少一条线索）。
    """
    from storygraph.app.source_line_delete import plan_source_line_delete
    from storygraph.graph.commands import DeleteSourceLineCommand
    from storygraph.graph.models import Graph

    root, fp = project(tmp_path)
    line_id = _line_id_of(fp, 'jump roof')
    plan = plan_source_line_delete(root, fp, line_id)
    assert plan.ok, plan.reason
    fp.write_text(THEIR_FAR, encoding='utf-8')

    host = _Host(root, Graph())
    cmd = DeleteSourceLineCommand(host, plan, 0)
    cmd.execute()

    infos = notify_calls.messages('info')
    assert infos, '合并了却一句话都没有 = 静默改别人的文件'
    assert any('保住了' in (m or '') for m in infos), infos
    assert all(len(m or '') <= 40 for m in infos), f'Toast 放不下：{infos}'
    assert any('$ flag = True' in (m or '') and '重新载入' in (m or '')
               for m in host.logs), host.logs
    assert '$ flag = True' in fp.read_text(encoding='utf-8')


def test_the_command_says_nothing_when_nothing_merged(tmp_path, notify_calls):
    """没合并的正常路不能刷屏。"""
    from storygraph.app.source_line_delete import plan_source_line_delete
    from storygraph.graph.commands import DeleteSourceLineCommand
    from storygraph.graph.models import Graph

    root, fp = project(tmp_path)
    line_id = _line_id_of(fp, 'jump roof')
    plan = plan_source_line_delete(root, fp, line_id)
    assert plan.ok, plan.reason
    cmd = DeleteSourceLineCommand(_Host(root, Graph()), plan, 0)
    cmd.execute()
    assert not [m for m in notify_calls.messages('info') if '保住' in m]


# ===========================================================================
#  六、顺手钉住：`Commit` 的字段语义（这一轮最容易踩的坑）
# ===========================================================================

def test_final_text_is_not_the_undo_expectation(tmp_path):
    """
    `Commit.final_text` 是**盘上**的内容，**不许**拿去当撤销的"期望内容"。

    撤销比的是"盘上还是我写的那份吗"。拿合并结果去比就会放行，然后把别人
    那几行一起抹掉 —— 所以本文件第三条判据（`test_undo_is_still_refused...`）
    必须还在。这里把两个值并排摆一次，谁改错了看得见。
    """
    _root, fp = project(tmp_path, body=THEIR_FAR)
    c = commit_text(fp, SRC, DRAFT)
    assert c.final_text != DRAFT
    assert '$ flag = True' in c.final_text
    assert 'jump roof' not in DRAFT
