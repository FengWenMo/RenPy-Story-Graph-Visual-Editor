"""
增量路径的两条 P0 与三条 P1 的回归测试（2026-10-19）
====================================================

对应报告 `docs/P0保真测试_增量路径_2026-10-19.md`：

  · **P0-1** 表格改「说话人」静默不生效（产物仍是原说话人、报告"改动 0 行"）；
  · **P0-2** 门禁拿不到项目上下文，把好产物判成"引擎读不了"；
  · **P1-1** 零改动往返被插 2 行生成标记，"逐字节还原"永远差 2 行；
  · **P1-2** `类型` 列填中文（「旁白」）无提示，那一行静默不落地；
  · **P1-3** 删掉 `if` / `else` 体里唯一那一拍 → 增量写出空块（`expected statement`）。

另有一条本轮顺带修掉的**真缺陷**：多 label 章节里"插入点"用的是组内下标，
新卡片会被当成"替换"，把原文那一行顶掉（`pomodorro.rpy` 插入一拍退回全量）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.patcher import _speaker_swap_ok, patch_export


def _beats(write_rpy, src, name='proj/game/ch01.rpy'):
    fp = write_rpy(src, name)
    beats = beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(fp)))
    return Path(fp), beats


def _as_from_table(beats) -> list:
    """模拟"从表里读回来"：行号是运行期元数据，不入 Excel。"""
    for b in beats:
        b.src_first = b.src_last = 0
    return beats


SPEAKER_ONLY = '''label ch01:
    scene bg room
    jc "这一句只有说话人要改"
    return
'''


class TestSpeakerOnlyEdit:
    """P0-1：只改说话人 → 只换那一行，别的字节不动。"""

    def test_speaker_change_lands_without_trust_ranges(self, write_rpy):
        fp, beats = _beats(write_rpy, SPEAKER_ONLY)
        _as_from_table(beats)
        for b in beats:
            if b.text == '这一句只有说话人要改':
                b.speaker = 'yhj'
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        assert res.ok, res.reasons
        assert 'yhj "这一句只有说话人要改"' in res.text, res.text
        assert 'jc "这一句只有说话人要改"' not in res.text
        assert res.changed_lines >= 1
        old = fp.read_text(encoding='utf-8').splitlines()
        new = res.text.splitlines()
        diff = [i for i, (x, y) in enumerate(zip(old, new)) if x != y]
        assert len(diff) == 1, [(old[i], new[i]) for i in diff]

    def test_speaker_swap_requires_identical_body(self):
        assert _speaker_swap_ok('jc "同一句"', 'yhj "同一句"')
        assert not _speaker_swap_ok('jc "同一句"', 'yhj "另一句"')
        # 菜单选项行的"正文"是选项文案，换说话人会写坏菜单
        assert not _speaker_swap_ok('"选项":', 'yhj "选项"')
        # 关键字行（`extend` / `menu`）的说话人位不是角色名
        assert not _speaker_swap_ok('extend "续"', 'yhj "续"')


IF_BODY = '''label ch01:
    $ love = 0
    if love >= 5:
        jc "只有这一句"
    return
'''


class TestBranchDeleteIsRefused:
    """P1-3：删掉块体唯一那一行 → 增量不许动，退全量（并说明原因）。"""

    def test_deleting_the_only_body_line_is_refused(self, write_rpy):
        fp, beats = _beats(write_rpy, IF_BODY)
        beats = [b for b in beats if b.text != '只有这一句']
        _as_from_table(beats)
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        assert res.dropped_beats >= 1, '删掉的拍没被检测出来'
        assert res.removed_lines == 0, '不许真的删（if 体会空）'
        assert not res.ok, '拒绝时必须 ok=False，让调用方退回全量'
        assert any('删除不安全' in r for r in res.reasons), res.reasons
        assert res.text == fp.read_text(encoding='utf-8'), '拒绝时一个字都不许动'


TWO_LABELS = '''label alpha:
    "甲一"
    "甲二"

label beta:
    "乙一"
    "乙二"
'''


class TestInsertPointsAreGlobalIndices:
    """
    多 label 章节的插入点必须用**全局下标**。

    分组对齐（`_recover_ranges_for_groups`）拿的是组内下标，直接当全局用
    就会指到另一组的拍上：新卡片走"替换"通道、把原文那一行顶掉，
    整份补丁再被 `_verify_patch` 否掉 → 退回全量（pomodorro 实测 60 段）。
    """

    def test_insert_in_second_label_does_not_eat_the_first_label(self, write_rpy):
        fp, beats = _beats(write_rpy, TWO_LABELS)
        new = type(beats[0])(label='beta', kind='narration',
                             text='乙新', seq=99)
        # 放在 beta 的第一拍之后
        idx = next(i for i, b in enumerate(beats) if b.text == '乙一')
        beats.insert(idx + 1, new)
        _as_from_table(beats)
        res = patch_export(beats, fp, render_beats_to_rpy(beats))
        assert res.ok, res.reasons
        assert res.inserted_lines >= 1, '新拍没落进产物'
        for text in ('甲一', '甲二', '乙一', '乙二', '乙新'):
            assert f'"{text}"' in res.text, f'{text} 不见了：{res.text}'


class TestInvalidKindIsReported:
    """P1-2：`类型` 填了工具不认的值 → 必须点名（行号 + 原值）。"""

    def test_unknown_kind_is_reported_with_line_id(self):
        from storygraph.pipeline.compile import compile_beats
        from storygraph.story.beat_model import Beat
        good = Beat(kind='narration', text='正常一句', label='ch',
                    seq=1, line_id='ch.0001')
        bad = Beat(kind='say', kind_raw='旁白', text='填了中文', label='ch',
                   seq=2, line_id='ch.0002')
        res = compile_beats([good, bad])
        assert res.invalid_kinds == [('ch.0002', '旁白')], res.invalid_kinds
        assert '类型列有 1 行' in res.summary()
        assert '旁白' in res.summary()

    def test_legit_kinds_are_not_reported(self):
        from storygraph.story.beat_model import Beat, unknown_kinds
        for kind in ('say', 'narration', 'menu', 'menu_option', 'extend'):
            b = Beat(kind=kind, kind_raw=kind, text='x', label='ch')
            assert unknown_kinds([b]) == [], kind


class TestDeclarationContext:
    """P0-2：给引擎 lint 补项目 `define` 骨架，且骨架自己永不背锅。"""

    def _project(self, tmp_path):
        game = tmp_path / 'proj' / 'game'
        game.mkdir(parents=True)
        (game / 'gui.rpy').write_text(
            'define gui.button_tile = False\n'
            'define gui.name = _p("""多\n行""")  # 三引号跨行\n'
            'transform bounce:\n    xpos 0.2\n', encoding='utf-8')
        return game

    def test_skeleton_takes_defines_and_skips_blocks(self, tmp_path):
        from storygraph.pipeline.engine_lint import declaration_context
        game = self._project(tmp_path)
        ctx = declaration_context(tmp_path / 'proj')
        assert 'gui.button_tile = False' in ctx
        assert 'except Exception:' in ctx, '每条都要 try/except：骨架不许拖崩 lint'
        assert 'transform bounce' not in ctx, 'transform 需要块体，不能单独抽出来'
        # 骨架不替被 lint 的那份文件说话（否则会掩掉"它自己丢了一个 define"）
        assert declaration_context(tmp_path / 'proj',
                                   exclude=game / 'gui.rpy') == ''

    def test_errors_in_the_skeleton_are_not_the_products_fault(self):
        from storygraph.pipeline.engine_lint import classify
        raw = ('File "game/00_sg_context.rpy", line 3: invalid syntax\n'
               '    define __broken__ = (\n')
        parse, semantic = classify(raw)
        assert parse, '不豁免时应当算语法错（对照组）'
        parse2, semantic2 = classify(raw, ignore_files=('00_sg_context.rpy',))
        assert parse2 == [], parse2
        assert semantic2 == [], semantic2


ZERO_CHANGE = '''label ch01:
    jc "什么都没改"
    return
'''


class TestZeroChangeDoesNotWrite:
    """P1-1：零改动往返 → 文件逐字节不变、也不插生成标记。"""

    def test_roundtrip_to_the_same_file_is_untouched(self, tmp_path):
        from storygraph.pipeline.compile import compile_table
        from storygraph.story.script_table import open_rpy_as_table

        game = tmp_path / 'proj' / 'game'
        game.mkdir(parents=True)
        src = game / 'ch01.rpy'
        src.write_text(ZERO_CHANGE, encoding='utf-8')
        before = src.read_bytes()

        table = tmp_path / 'ch01.xlsx'
        open_rpy_as_table(src, table)
        _path, _sheet, result = compile_table(
            table, rpy_path=src, source_path=src, write_sheet=False,
            engine_lint=False)
        assert result.untouched, '零改动应当标成 untouched'
        assert src.read_bytes() == before, '文件被改写了（或多插了生成标记）'

    def test_roundtrip_to_a_new_path_is_byte_identical(self, tmp_path):
        from storygraph.pipeline.compile import compile_table
        from storygraph.story.script_table import open_rpy_as_table

        game = tmp_path / 'proj' / 'game'
        game.mkdir(parents=True)
        src = game / 'ch01.rpy'
        src.write_text(ZERO_CHANGE, encoding='utf-8')
        out = tmp_path / 'copy.rpy'

        table = tmp_path / 'ch01.xlsx'
        open_rpy_as_table(src, table)
        compile_table(table, rpy_path=out, source_path=src, write_sheet=False,
                      engine_lint=False)
        assert out.read_text(encoding='utf-8') == ZERO_CHANGE, \
            '副本必须逐字节等于源文（不许插生成标记）'
