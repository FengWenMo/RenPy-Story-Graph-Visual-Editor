"""
tests/test_incremental_dialogue_pairing.py
=========================================
一拍里"台词槽位"的配对：**改说话人 + 改正文同时发生**时，只换那一行。

为什么单开一个文件（2026-10-21，真机 P0）：

表格那条路（`table xlsx2rpy`）上，把 `jc "…"` 的说话人和正文**同时**改掉，
导出回执是「改动 2 行」、退出码 0，而文件里是：

    yhj "【同改】…"            ← 新行
    jc "好了，我休息好了…"       ← 旧行还在

同一句话出现两遍。根因是配对键 `('say', 说话人)`：两者同时变就配不上，
新行被当"新增内容"插入、旧台词又不许删 —— "插入 + 保留"。

判据用**文件内容**（旧句子消失、新句子恰好一行），不是回执里的行数：
回执从前也会说"改动 2 行"，那正是这条缺陷活下来的原因。
"""

from pathlib import Path

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.patcher import _say_parts, patch_export

SRC = '''# 文件头：逐字节保住
label ch01:
    scene bg classroom
    show ling happy at left
    ling "第一句"
    "旁白一句"
    ling "第三句"
    ling "第四句"
    return
'''


def _beats(fp):
    return beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(fp)))


def _patch(write_rpy, mutate, *, table=True):
    """
    `table=True`：模拟**表格那条路** —— 拍上没有行号（Excel 不存运行期元数据），
    由 `recover_source_ranges` 当场从源文件算。这正是用户报缺陷的那条路。
    """
    fp = Path(write_rpy(SRC, 'proj/game/ch01.rpy'))
    beats = _beats(fp)
    if table:
        for b in beats:
            b.src_first = 0
            b.src_last = 0
            b.insert_anchor = 0
    mutate(beats)
    res = patch_export(beats, fp, render_beats_to_rpy(beats))
    return res


def _count(text: str, needle: str) -> int:
    return sum(1 for ln in text.splitlines() if needle in ln)


class TestSpeakerAndTextTogether:
    def test_replaces_the_line_instead_of_duplicating_it(self, write_rpy):
        """真机 pomodorro 第 28 行：两个字段同时改 → 只许留一行。"""
        def mutate(beats):
            for b in beats:
                if b.text == '第一句':
                    b.speaker = 'yhj'
                    b.text = '【同改】说话人和正文一起换'

        res = _patch(write_rpy, mutate)
        assert res.ok, res.reasons
        assert '第一句' not in res.text, '旧台词还留在文件里'
        assert _count(res.text, '【同改】说话人和正文一起换') == 1
        assert 'ling "第三句"' in res.text
        assert '# 文件头：逐字节保住' in res.text

    def test_still_one_dialogue_line_per_beat(self, write_rpy):
        """别的拍不受牵连：只动改过的那一行。"""
        def mutate(beats):
            for b in beats:
                if b.text == '第一句':
                    b.speaker = 'yhj'
                    b.text = '【同改】换掉'

        res = _patch(write_rpy, mutate)
        assert res.changed_lines == 1, f'动了 {res.changed_lines} 行'

    def test_unknown_speaker_does_not_leave_the_old_line(self, write_rpy):
        """
        说话人写成不存在的角色（表里拼错名字）也不许留旧行。

        渲染器会为未定义角色写一行 `# TODO: 缺少资源 角色 '…'` 再退回旁白；
        旧台词**必须被替换**，否则同一句话在脚本里出现两次。
        """
        def mutate(beats):
            for b in beats:
                if b.text == '第三句':
                    b.speaker = 'nosuchchar'
                    b.text = '拼错角色名的台词'

        res = _patch(write_rpy, mutate)
        assert '第三句' not in res.text
        assert _count(res.text, '拼错角色名的台词') == 1


class TestSlotOnlyPairsDialogue:
    """槽位配对只认"真台词"——选项行 / voice / extend 一概不碰。"""

    def test_option_row_is_not_a_dialogue_slot(self):
        assert _say_parts('"选项":') is None

    def test_voice_is_not_a_dialogue_slot(self):
        assert _say_parts('voice "v_ling_01"') is None

    def test_extend_is_not_a_dialogue_slot(self):
        assert _say_parts('extend "接着上一句"') is None

    def test_a_plain_say_is_a_slot(self):
        assert _say_parts('ling "台词"') == ('ling', '台词')
