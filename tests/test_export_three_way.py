# -*- coding: utf-8 -*-
"""
导出那条大路：**别人也改过这个脚本**（第十五轮，三路合并）
=========================================================

第十轮～第十四轮把"原地小改"（接线、断线、留多久、别名…）一个个接到了
`write_gate.commit_text` 上：别人改了别处 → 能合就合。可**改一句台词 → 导出**
这条每天都在走的大路一直没接 —— 它走的是 `session_export._patch_note` →
`patch_export(trust_ranges=<文件没被外人改过>)`。

这一轮先量了它（`.pip-tmp/probe_r15_export_drift.py`），量出来的**不是**"拒写"：

| 别人改在哪 | 从前 | 病 |
|---|---|---|
| 文件开头加行 / 末尾加句 / 中间插一句 | 拒写 | 能合却拒 |
| **改了我没动的那一拍** | ok、写出去了 | **他那行被静默改回原样** |
| **改的正是我在改的那一拍** | ok、写出去了 | **他那行被静默改回原样**（真冲突没说） |

根子在：文件一变，`trust_ranges` 就关了 → 只能靠内容证据认行 → 而用户改的
正是那句台词（内容证据全没了）→ 退成"说话人一样就认这一拍" → 把别人的行
当成自己的改回去。

修法见 `docs/设计_导出这条大路_三路合并_2026-11-08.md`：**补丁打在底稿上**
（`base_text`），产物 = 底稿 + 我的改动，再与盘上那份做三路合并
（`write_gate.merge_three_way`，与原地小改那条路**同一个函数**）。

这个文件按"三件事"分组：

  1. 端到端：走真正的 `EditorSession.load` + `export`，看**盘上到底落了什么**；
  2. patcher 那一层：`base_text` 的等价性（给了它不能改变"文件没变"时的结果）；
  3. 守卫：任何"把补丁打在底稿上"的调用点，**必须**接着合并 —— 否则就是拿
     旧底稿覆盖别人的文件。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / 'storygraph'

SRC = (
    'default score = 0\n'
    '\n'
    'label a:\n'
    '    e "第一句"\n'
    '    e "第二句"\n'
    '    e "第三句"\n'
)

#: 别人写的那几行（与原文不共享子串 —— 共享的话"内容证据"会误判，
#: 探针第一版就踩了这个：`他改的第二句` 里含 `第二句`，会让人以为旧判据能认出）。
HIS = '换成别的说法甲'


def _loaded(tmp_path, text=SRC):
    """走**真正的** `load`：记指纹，也记底稿（第十五轮新增的那一项）。"""
    from storygraph.app.editor import EditorSession

    src = tmp_path / 'ch.rpy'
    src.write_text(text, encoding='utf-8')
    s = EditorSession(project_root=tmp_path)
    s.load(src)
    return s, src


def _beat(s, text):
    for b in s.beats:
        if str(getattr(b, 'text', '') or '') == text:
            return b
    raise AssertionError(f'没有哪一拍写着「{text}」')


def _edit_second(s, text='我改过的第二句'):
    b = _beat(s, '第二句')
    b.text = text
    return b


class TestBothSidesLand:
    """他改的、我改的，都要在盘上 —— 而且**他那行逐字不许变**。"""

    def test_his_line_survives_when_i_changed_another_line(self, tmp_path):
        """
        **本轮的主要修复**：他改第 1 句、我改第 2 句 → 两句都得在。

        从前这一刀是 `ok=True` + 文件写出去 + 他把第 1 句改回「第一句」。
        判据是"他那行逐字还在"，不是"导出成功了"—— 从前也报成功。
        """
        s, src = _loaded(tmp_path)
        _edit_second(s)
        src.write_text(SRC.replace('第一句', HIS), encoding='utf-8')

        res = s.export(src)
        assert res['ok'] is True, res
        body = src.read_text(encoding='utf-8')
        assert HIS in body, '他把第 1 句改了，导出后它被改回原样了（静默覆盖）'
        assert '我改过的第二句' in body, '我改的那一拍没落地'
        assert '第一句' not in body, '旧的那句还在（没被替换）'
        assert 'default score = 0' in body, '章节级内容被抹掉了'

    def test_both_sides_land_when_he_appended_a_line(self, tmp_path):
        """他在末尾加一句台词（从前：整份拒写）。"""
        s, src = _loaded(tmp_path)
        _edit_second(s)
        src.write_text(SRC + '    e "他新加的一句话"\n', encoding='utf-8')

        res = s.export(src)
        assert res['ok'] is True, res
        body = src.read_text(encoding='utf-8')
        assert '他新加的一句话' in body, '他加的那一句被吞了'
        assert '我改过的第二句' in body

    def test_both_sides_land_when_he_inserted_after_the_label(self, tmp_path):
        """他在 `label a:` 后面插一句（行号整体下移一行）。"""
        s, src = _loaded(tmp_path)
        _edit_second(s)
        src.write_text(SRC.replace('label a:\n', 'label a:\n    e "插队的一句话"\n'),
                       encoding='utf-8')

        res = s.export(src)
        assert res['ok'] is True, res
        body = src.read_text(encoding='utf-8')
        assert '插队的一句话' in body
        assert '我改过的第二句' in body
        # 顺序也要对：他插的那句在 label 之后、第一句之前
        assert body.index('插队的一句话') < body.index('第一句'), body

    def test_my_deletion_and_his_edit_both_land(self, tmp_path):
        """我删一张卡、他改别处的一句 → 删的删掉、他的留着。"""
        s, src = _loaded(tmp_path)
        gone = s.beats.index(_beat(s, '第三句'))
        assert s.delete_beat(gone).get('ok'), '前置条件：删卡要成功'
        src.write_text(SRC.replace('第一句', HIS), encoding='utf-8')

        res = s.export(src)
        assert res['ok'] is True, res
        body = src.read_text(encoding='utf-8')
        assert '第三句' not in body, '我删掉的那一拍还在'
        assert HIS in body, '他改的那一句被吞了'
        assert '第二句' in body

    def test_my_insert_and_his_edit_both_land(self, tmp_path):
        """我插一张卡、他改别处一句 → 两边都在。"""
        s, src = _loaded(tmp_path)
        at = s.beats.index(_beat(s, '第二句'))
        assert s.insert_beat(at).get('ok'), '前置条件：插卡要成功'
        s.beats[at + 1].text = '我插的一句'
        src.write_text(SRC.replace('第一句', HIS), encoding='utf-8')

        res = s.export(src)
        assert res['ok'] is True, res
        body = src.read_text(encoding='utf-8')
        assert '我插的一句' in body, '我插的那一句没落地'
        assert HIS in body, '他改的那一句被吞了'

    def test_i_changed_nothing_so_his_version_is_left_alone(self, tmp_path):
        """
        我一个字都没改、只他改了 → 文件**逐字节不动**（连 mtime 都不烧）。

        这一条防的是"合并"被用成"顺手重写一遍"：产物应当与盘上那份完全相同，
        于是 `export` 的零改动那条路会认出它（`untouched=True`）。
        """
        s, src = _loaded(tmp_path)
        theirs = SRC.replace('第一句', HIS)
        src.write_text(theirs, encoding='utf-8')

        res = s.export(src)
        assert res['ok'] is True, res
        assert res.get('untouched') is True, res
        assert src.read_text(encoding='utf-8') == theirs, '文件不该被动'
        # 也不该说"两边都落上了"（我这边什么都没落）
        assert not res['note'].startswith('别人也改过'), res['note']

    def test_a_speaker_swap_lands_when_the_file_drifted(self, tmp_path):
        """
        **内容证据一条都不剩时也要能落地**（这才是 `trust_ranges` 存在的理由）。

        这一条是变异测试逼出来的。先写的是"旁白 → 台词"，可那个变异
        （"底稿补丁不开 trust"）**活下来了** —— 追下去才发现旁白被整句换掉时有
        另一条分支兜着（`_narration_slot_replaced`：邻居自洽，只做一对一替换）。
        真正**只能**靠 trust 的是这一种：**连说话人都换了**（`e` → `jc`），
        正文也换了 —— 对位守卫的三样证据（正文 / 说话人 / 图像名）一样都不在，
        形状相似度那条兜底又只对"没有说话人的拍"生效。

        实测（`.pip-tmp/dbg_r15_narration.py` 的 H / I 两个场景）：
        trust 开 → 落地；trust 关 → `对位率不足（1/2 = 50%）`整份拒绝。
        """
        s, src = _loaded(tmp_path)
        beat = _beat(s, '第一句')
        beat.speaker = 'jc'                # 这句其实是另一个人说的
        beat.text = '另一句'
        src.write_text('# 外部加的一行\n' + SRC, encoding='utf-8')

        res = s.export(src)
        assert res['ok'] is True, res
        body = src.read_text(encoding='utf-8')
        assert 'jc "另一句"' in body, '换说话人 + 换词这一刀没落地'
        assert 'e "第一句"' not in body, '旧的那一行还在'
        assert '# 外部加的一行' in body, '他那行被吞了'


class TestConflict:
    """撞在同一处 → 一个字都不写（判定唯一的地方是 `merge_text`）。"""

    def test_same_line_both_changed_refuses_and_names_the_card(self, tmp_path):
        s, src = _loaded(tmp_path)
        _edit_second(s)
        theirs = SRC.replace('第二句', '他也改了这一句')
        src.write_text(theirs, encoding='utf-8')

        res = s.export(src)
        assert res['ok'] is False, res
        assert 'PATCH_FAILED' in (res.get('codes') or []), res.get('codes')
        joined = ' '.join(res.get('blocking') or [])
        assert '第 2 张卡' in joined, f'拒绝理由要点到哪一张卡：{joined}'
        assert src.read_text(encoding='utf-8') == theirs, '拒绝时文件一个字节都不许动'

    def test_export_to_another_path_is_not_blocked_by_the_conflict(self, tmp_path):
        """
        导出到**别处**（`build/`）不受"他那份文件"的约束：产物是我这一版。

        为什么：目标不是他的文件，写它不会毁掉他的任何东西 —— 而且
        "导出到别的路径比对一下"正是拒绝时给用户的退路之一（不能自己也走不通）。
        """
        s, src = _loaded(tmp_path)
        _edit_second(s)
        theirs = SRC.replace('第二句', '他也改了这一句')
        src.write_text(theirs, encoding='utf-8')
        out = tmp_path / 'build' / 'ch.rpy'
        out.parent.mkdir()

        res = s.export(out)
        assert res['ok'] is True, res
        assert '我改过的第二句' in out.read_text(encoding='utf-8')
        assert src.read_text(encoding='utf-8') == theirs, '源文件不许被动'


class TestNoBaseline:
    """没有底稿 = 不知道"我改了什么"，那就还是**不猜**（不回归）。"""

    def test_a_session_without_a_baseline_still_refuses(self, tmp_path):
        from storygraph.app.editor import EditorSession
        from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                     RenPyParser)
        from storygraph.story.beat_build import beats_from_parse_result

        src = tmp_path / 'ch.rpy'
        src.write_text(SRC, encoding='utf-8')
        s = EditorSession(project_root=tmp_path)
        s.script_path = src
        s.beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(str(src))))
        # 手工塞拍：没有 `_source_baseline_text`（表格那条路就是这样）
        assert s._source_baseline_text == ''
        _edit_second(s)
        src.write_text('# 外部加的一行\n' + SRC, encoding='utf-8')

        res = s.export(src)
        assert res['ok'] is False, f'没有底稿还敢按旧行号写：{res}'
        assert src.read_text(encoding='utf-8') == '# 外部加的一行\n' + SRC


class TestPatcherLevel:
    """patcher 那一层：`base_text` 只许**换源**，不许改别的。"""

    def test_base_text_equal_to_the_file_is_byte_identical(self, tmp_path):
        """
        等价性：`base_text == 盘上内容` 时必须与"不传"**逐字节相同**。

        没有这一条，`base_text` 就会变成一条"悄悄改变了导出结果"的新路 ——
        而它本该只是"把第 ② 步读到的字节从盘上换成调用方手里那份"。
        """
        from storygraph.app.editor import EditorSession
        from storygraph.story.patcher import patch_export

        src = tmp_path / 'ch.rpy'
        src.write_text(SRC, encoding='utf-8')
        s = EditorSession(project_root=tmp_path)
        s.load(src)
        _edit_second(s)
        gen = s.compile().rpy_text

        a = patch_export(s.beats, src, gen, registry=s.registry(),
                         trust_ranges=True)
        b = patch_export(s.beats, src, gen, registry=s.registry(),
                         trust_ranges=True, base_text=SRC)
        assert a.ok and b.ok, (a.reasons, b.reasons)
        assert a.text == b.text, '给了底稿之后产物变了（那不是等价性）'
        assert (a.matched_beats, a.changed_lines) == (b.matched_beats,
                                                      b.changed_lines)

    def test_base_text_makes_the_dropped_count_follow_the_baseline(self,
                                                                   tmp_path):
        """
        "数被删的拍"必须数**底稿**，不是数现在这份盘。

        构造：我删掉一拍，他同时在别处加了一行。拿盘去数时，源里多出来的那行
        会让对齐把"我删的那一拍"认错，`dropped` 就成了别人的行号 ——
        删除通道照着它删，删的就是别人的行（当年"整体下移 1 行、259 行被改写"
        的形态）。

        这一条同时把**分工**钉住：patch 只管"底稿 + 我"（他那一行当然不在里面），
        把两边合起来是 `merge_three_way` 的事。
        """
        from storygraph.app.editor import EditorSession
        from storygraph.story.patcher import patch_export
        from storygraph.story.write_gate import MERGED, merge_three_way

        src = tmp_path / 'ch.rpy'
        src.write_text(SRC, encoding='utf-8')
        s = EditorSession(project_root=tmp_path)
        s.load(src)
        s.delete_beat(s.beats.index(_beat(s, '第三句')))
        gen = s.compile().rpy_text
        drift = SRC.replace('label a:\n', 'label a:\n    e "插队的一句话"\n')
        src.write_text(drift, encoding='utf-8')

        base = patch_export(s.beats, src, gen, registry=s.registry(),
                            trust_ranges=True, base_text=SRC)
        assert base.ok, base.reasons
        assert base.dropped_beats == 1, '我删的那一拍没被数出来'
        assert base.removed_preview == ('e "第三句"',), base.removed_preview
        assert '第三句' not in base.text, '我删掉的那句还在'
        assert '第一句' in base.text and '第二句' in base.text
        assert '插队的一句话' not in base.text, \
            '底稿补丁里不该有他那行（那正是必须再合并一次的理由）'

        merged = merge_three_way(SRC, drift, base.text)
        assert merged.state == MERGED, merged.sentence()
        assert '插队的一句话' in merged.final_text, '他那行没回来'
        assert '第三句' not in merged.final_text, '我删的那句回来了'


class TestMergeThreeWay:
    """`write_gate.merge_three_way` —— 导出与原地小改共用的**那一份**判定。"""

    def test_four_states(self):
        from storygraph.story.write_gate import (GATE_CONFLICT, MERGED, SAME,
                                                 WROTE, merge_three_way)

        base = 'a\nb\nc\n'
        same = merge_three_way(base, 'a\nB\nc\n', 'a\nB\nc\n')
        assert same.state == SAME and not same.wrote_bytes
        wrote = merge_three_way(base, base, 'a\nB\nc\n')
        assert wrote.state == WROTE and wrote.final_text == 'a\nB\nc\n'
        merged = merge_three_way(base, 'A\nb\nc\n', 'a\nb\nC\n')
        assert merged.state == MERGED
        assert merged.final_text == 'A\nb\nC\n'
        assert len(merged.kept) == 1 and 'A' in merged.kept[0].head
        clash = merge_three_way(base, 'a\nB\nc\n', 'a\nX\nc\n')
        assert clash.state == GATE_CONFLICT and clash.final_text == ''
        assert clash.clash == ((2, 2),), clash.clash

    def test_the_same_verdict_is_used_by_commit_text(self, tmp_path):
        """原地小改那条路（`commit_text`）走的是同一个判定，不许有两套答案。"""
        import inspect

        from storygraph.story import write_gate as W

        src = inspect.getsource(W.commit_text)
        assert 'merge_three_way(' in src, \
            'commit_text 自己判了一遍 —— 两处会各说一套'


class TestGuard:
    """结构性守卫：**把补丁打在底稿上**的调用点必须接着合并。"""

    def test_every_base_text_call_site_merges(self):
        """
        扫全包：`patch_export(..., base_text=<非空>)` 的那个文件里，
        必须出现 `merge_three_way`（或它的封装 `_settle_with_disk`）。

        为什么值得一条静态守卫：`base_text` 的语义是"这一刀不是打在盘上"，
        拿到的产物里**没有**别人那几行 —— 忘了合并就是拿旧底稿覆盖他的文件，
        而且回执照样说成功（这一轮修的就是这个）。
        """
        sites = []
        for fp in sorted(PKG.rglob('*.py')):
            try:
                tree = ast.parse(fp.read_text(encoding='utf-8'))
            except (SyntaxError, UnicodeDecodeError):   # pragma: no cover
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = getattr(fn, 'id', None) or getattr(fn, 'attr', '')
                if name != 'patch_export':
                    continue
                for kw in node.keywords:
                    if kw.arg != 'base_text':
                        continue
                    if isinstance(kw.value, ast.Constant) and not kw.value.value:
                        continue          # 空串 = 照旧读盘，不算
                    sites.append((fp, node.lineno))
        assert sites, '一个"底稿补丁"的调用点都没扫到 —— 守卫本身失效了'
        for fp, line in sites:
            text = fp.read_text(encoding='utf-8')
            assert ('merge_three_way' in text
                    or '_settle_with_disk' in text), (
                f'{fp.name}:{line} 把补丁打在底稿上，却没有接着合并 —— '
                f'那会拿旧底稿覆盖别人改过的文件')

    def test_the_settle_step_runs_before_the_write(self):
        """
        `export()` 必须真的调它（而且是在 `_write_export` 之前）。

        防的是"把 `_settle_with_disk` 写好了，但没人调"—— 第十四轮的
        `merge_text` 就那样躺了半年（写好、零调用方）。
        """
        src = (PKG / 'app' / 'session_export.py').read_text(encoding='utf-8')
        assert src.index('self._settle_with_disk(') < src.index(
            'self._write_export('), '合并必须在落盘之前'
