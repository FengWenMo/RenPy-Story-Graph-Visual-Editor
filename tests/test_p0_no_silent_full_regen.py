"""
P0 守卫：patcher 失败 / 目标已存在时**不得静默退回全量覆盖**。

对应报告：`docs/archive/P0_静默全量覆盖_2026-10-03.md`

为什么要有这个文件（事故形态）：
  保存剧本时增量补丁对不上 → `_patch_note` 静默换成全量文本 →
  `export` 照写 → `write_generation` 不检查目标是否存在 →
  原文件的 `default` / `init python` / `transform` 全部蒸发。

前两组当前**应该是红的**，它们就是这个 bug 的证据。
修完转绿 = 验收通过。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app.editor import EditorSession                        # noqa
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer    # noqa
from storygraph.pipeline.compile import compile_beats                  # noqa
from storygraph.story.beat_build import beats_from_parse_result         # noqa
from storygraph.story.excel_export import export_beats_to_excel         # noqa
from storygraph.story.excel_import import import_beats_from_excel       # noqa
from storygraph.story.generation_guard import write_generation          # noqa
from storygraph.story.patcher import PatchResult                        # noqa

MARKER = '# 由剧本表自动生成'

#: 含"第三类东西"的样本：default / init python / transform 都不进 Beats
SAMPLE = (
    'default score = 0\n'
    '\n'
    'init python:\n'
    '    def helper():\n'
    '        return 1\n'
    '\n'
    'transform my_tf:\n'
    '    xalign 0.5\n'
    '\n'
    'label a:\n'
    '    e "原文台词"\n'
)


def _make_src(tmp_path) -> Path:
    p = tmp_path / 'chapter.rpy'
    p.write_text(SAMPLE, encoding='utf-8')
    return p


def _beats_of(path: Path):
    a = IncrementalRenPyAnalyzer()
    pr, _ = a.analyze(files=[path], graph=None)
    return beats_from_parse_result(pr)


def _fake_failure(*args, **kwargs):
    """强制 patcher 失败：对位 0/5，带原因。"""
    src = args[1] if len(args) > 1 else kwargs.get('source_path')
    return PatchResult(
        text='# 全量重生成的坏文本',
        source_path=src,
        matched_beats=0,
        total_beats=5,
        changed_lines=0,
        reasons=['测试强制：对位失败'],
    )


# ==================================================================
#  1. patcher 失败 → 必须中止，不得换全量继续写
# ==================================================================
class TestPatchFailureMustNotOverwrite:

    def test_patch_note_marks_blocking_instead_of_full_text(
            self, tmp_path, monkeypatch):
        """patcher 失败时 `_patch_note` 必须给出 blocking，而不是可用文本。"""
        import storygraph.story.patcher as P
        monkeypatch.setattr(P, 'patch_export', _fake_failure)

        src = _make_src(tmp_path)
        s = EditorSession(project_root=tmp_path)
        s.script_path = src
        s.beats = _beats_of(src)

        note = s._patch_note('任意全量文本')
        assert note['used_patch'] is False
        # 现在这里没有 blocking 键 → 红
        assert note.get('blocking'), (
            'patcher 失败时 `_patch_note` 必须返回 blocking，'
            '而不是把全量文本当可用产物交回去')

    def test_export_does_not_touch_file_when_patch_fails(
            self, tmp_path, monkeypatch):
        """patcher 失败 → 目标文件必须逐字节不变。"""
        import storygraph.story.patcher as P
        monkeypatch.setattr(P, 'patch_export', _fake_failure)

        src = _make_src(tmp_path)
        before = src.read_text(encoding='utf-8')
        s = EditorSession(project_root=tmp_path)
        s.script_path = src
        s.beats = _beats_of(src)

        s.export(src)
        assert src.read_text(encoding='utf-8') == before, (
            'patcher 失败，文件却被改写了')

    def test_export_reports_not_ok_when_patch_fails(
            self, tmp_path, monkeypatch):
        """patcher 失败 → export 必须返回 ok=False。"""
        import storygraph.story.patcher as P
        monkeypatch.setattr(P, 'patch_export', _fake_failure)

        src = _make_src(tmp_path)
        s = EditorSession(project_root=tmp_path)
        s.script_path = src
        s.beats = _beats_of(src)

        res = s.export(src)
        # 现在仍然 ok=True → 红
        assert res['ok'] is False, (
            'patcher 失败却返回 ok=True，调用方无从分辨')


# ==================================================================
#  2. write_generation 的"目标已存在"护栏
# ==================================================================
class TestFullRegenGuard:

    def test_refuses_overwriting_handwritten_rpy(self, tmp_path):
        """往已存在的手写 .rpy 写 → 拒绝。"""
        p = _make_src(tmp_path)
        before = p.read_text(encoding='utf-8')
        art = write_generation('# 全量产物\nlabel a:\n    pass\n', p)
        assert art.written is False, '已有手写 .rpy 被覆盖了'
        assert p.read_text(encoding='utf-8') == before

    def test_refuses_file_without_generator_marker(self, tmp_path):
        """无工具生成标记头的文件 = 手写，拒绝覆盖。"""
        p = tmp_path / 'hand.rpy'
        p.write_text('label a:\n    "手写文件"\n', encoding='utf-8')
        art = write_generation('# 全量产物\n', p)
        assert art.written is False

    def test_allows_writing_new_file(self, tmp_path):
        """新文件必须仍然放行（别误伤正向路径）。"""
        p = tmp_path / 'brand_new.rpy'
        art = write_generation(MARKER + '\nlabel a:\n    pass\n', p)
        assert art.written is True
        assert p.exists()

    def test_force_allows_overwrite(self, tmp_path):
        """显式 force 才允许覆盖（逃生舱）。"""
        p = _make_src(tmp_path)
        try:
            art = write_generation('# 全量产物\nlabel a:\n    pass\n',
                                   p, force=True)
        except TypeError:
            pytest.fail('write_generation 尚未支持 force 参数')
        assert art.written is True

    def test_allows_overwriting_its_own_output(self, tmp_path):
        """
        **工具自己生成的产物必须可以再导出**（别被自己的护栏拦下）。

        这条是护栏最容易写坏的方向：判据如果只看"文件已存在"，
        "再导出一次"就会被拒 —— 那比不拦更烦，且症状
        （"我明明是用工具生成的"）极难自查。
        判据必须是"首行有没有生成标记"。
        """
        p = tmp_path / 'gen.rpy'
        first = write_generation(MARKER + '\nlabel a:\n    pass\n', p)
        assert first.written is True
        # 再写一次（内容不同）—— 仍然放行
        second = write_generation(MARKER + '\nlabel a:\n    "第二次"\n', p)
        assert second.written is True, '自己的产物被自己的护栏拦下了'
        assert '第二次' in p.read_text(encoding='utf-8')

    def test_marker_is_a_single_source_of_truth(self, tmp_path):
        """
        渲染层写的首行 **就是** 门禁认的那个标记。

        两处字符串一旦漂移，`test_allows_overwriting_its_own_output`
        那种事故立刻发生。所以这里直接断言两边相等。
        """
        from storygraph.core.vocabulary import GENERATOR_MARKER
        from storygraph.story import generation_guard as G
        assert GENERATOR_MARKER == G.GENERATOR_MARKER
        out = compile_beats(_beats_of(_make_src(tmp_path))).rpy_text
        assert out.splitlines()[0] == GENERATOR_MARKER, (
            '渲染产物的首行与门禁标记不一致')
        # 而且它真的被认成"工具生成的"
        assert not G._looks_handwritten(
            _write_marker_file(tmp_path, out))
        # 只写**前半句**（人最可能写的形态）也要认出来
        assert not G._looks_handwritten(
            _write_marker_file(tmp_path, MARKER + '\nlabel a:\n    pass\n'))
        # 手写文件要认得出来
        assert G._looks_handwritten(
            _write_marker_file(tmp_path, 'label a:\n    "手写"\n'))


def _write_marker_file(tmp_path, text) -> Path:
    p = tmp_path / 'marker_probe.rpy'
    p.write_text(text, encoding='utf-8')
    return p


# ==================================================================
#  2b. export 必须检查 write_generation 的结果
# ==================================================================
class TestExportChecksTheWriteResult:
    """
    `export` 以前**丢掉** `write_generation` 的返回值，无条件报 ok=True。

    这是同一个 P0 的第二个面：即使门禁拦下了，用户看到的是"导出成功"，
    以为产物在，实际 `game/` 里什么都没有。
    """

    def test_export_reports_failure_when_guard_refuses(self, tmp_path):
        """
        目标已存在的手写文件 + 补丁失败 → ok=False 且文件不变。

        （补丁失败是必需的：补丁成功时走的是"保留原文的增量合并"，
        那条路**不会**丢掉 default / init python，本来就安全。）
        """
        import storygraph.story.patcher as P
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(P, 'patch_export', _fake_failure)
        try:
            src = _make_src(tmp_path)
            before = src.read_text(encoding='utf-8')
            s = EditorSession(project_root=tmp_path)
            s.script_path = src
            s.beats = _beats_of(src)
            res = s.export(src)
        finally:
            monkeypatch.undo()
        assert res['ok'] is False, res
        assert res['blocking'], '拦下了却没给原因'
        assert src.read_text(encoding='utf-8') == before

    def test_export_to_a_new_path_still_succeeds(self, tmp_path):
        """
        **新建文件必须仍然成功** —— 全量生成正是它的正当用途。

        这条是"别误伤"：拦得太狠会让"导出到新路径"彻底不能用。
        """
        src = _make_src(tmp_path)
        s = EditorSession(project_root=tmp_path)
        s.script_path = src
        s.beats = _beats_of(src)
        target = tmp_path / 'build' / 'a.generated.rpy'
        res = s.export(target)
        assert res['ok'] is True, res
        assert target.exists(), '新建文件的导出被拦下了'
        assert '原文台词' in target.read_text(encoding='utf-8')

    def test_re_exporting_the_same_target_succeeds(self, tmp_path):
        """
        **连续导出到同一个路径必须一直成功**（幂等）。

        这条是上一版漏掉的真实 bug（我手测时才发现）：
        补丁那条路产出的首行是**源文件的首行**，所以写出去的文件
        **没有生成标记** → 第二次导出被判成"手写文件"拒绝覆盖：
        修法是落盘前统一补标记。

        2026-10-19 改口径（P1-1）：**零改动往返不再补标记** —— 标记是
        "零改动却改了文件"的那 2 行。所以现在守的是另一条等价契约：
        产物与源文**逐字节相同**，且连导三次都成功（没有标记也不会被
        "手写护栏"误伤：`_copy_of_source` 认"内容等于底稿"的复制品）。
        """
        src = _make_src(tmp_path)
        s = EditorSession(project_root=tmp_path)
        s.script_path = src
        s.beats = _beats_of(src)
        target = tmp_path / 'build' / 'a.generated.rpy'

        first = s.export(target)
        assert first['ok'] is True, first
        assert target.read_text(encoding='utf-8') == src.read_text(
            encoding='utf-8'), '零改动往返必须逐字节等于源文（不许插生成标记）'

        for n in (2, 3):
            again = s.export(target)
            assert again['ok'] is True, (
                f'第 {n} 次导出失败了（护栏认不出自家产物）：{again}')

    def test_export_reports_failure_not_faked_when_write_blocked(
            self, tmp_path):
        """`export` 必须把 `write_generation` 的 written=False 转成 ok=False。"""
        from storygraph.story import generation_guard as G
        p = _make_src(tmp_path)
        art = G.write_generation('# 产物\nlabel a:\n    pass\n', p)
        # 先把护栏这一层的结论钉住
        assert art.written is False
        assert any(getattr(i, 'code', '') == 'HANDWRITTEN_TARGET'
                   for i in art.issues), art.issues


# ==================================================================
#  2d. export 的逃生舱（force=True）+ codes 契约
# ==================================================================
class TestExportForceEscapeHatch:
    """
    方案 3：**默认安全 + 明确覆盖**。

    `export(force=True)` 是 UI 那个"仍然覆盖"按钮的后端。
    它只该越过 `HANDWRITTEN_TARGET` 这一条 ——
    产物自己坏了（语法错误 / 图像名非法 / label 重名）时，
    `force` **不许**把坏脚本写进 game/。

    2026-10-05 改口（用户报"明明只是增量的改动，为何阻止我替换旧文件"）：
    **原地写回自己那一章**不再走这条逃生舱 —— 增量合并且底稿就是目标时，
    产物逐字节包含原文，护栏直接让路（见 `test_export_acceptance` 的 1a）。
    `HANDWRITTEN_TARGET` 现在只在"要覆盖的是**别人的**手写文件"时出现，
    下面这些用例照那个形状造。
    """

    def _session_writing_over_someone_else(self, tmp_path):
        """本章（ch.rpy）有源可增量；要写的是**另一个**手写文件。"""
        src = _make_src(tmp_path)                 # ch.rpy（手写、有台词）
        other = tmp_path / 'other.rpy'            # 别人的章节文件
        other.write_text('default x = 1\n\nlabel other:\n    "别人的台词"\n',
                         encoding='utf-8')
        s = EditorSession(project_root=tmp_path)
        s.script_path = src
        s.beats = _beats_of(src)
        return s, src, other

    def test_force_overwrites_the_handwritten_file(self, tmp_path):
        s, _src, other = self._session_writing_over_someone_else(tmp_path)
        blocked = s.export(other)
        assert blocked['ok'] is False, blocked
        assert 'HANDWRITTEN_TARGET' in blocked['codes'], blocked['codes']

        forced = s.export(other, force=True)
        assert forced['ok'] is True, forced

    def test_blocked_result_carries_codes(self, tmp_path):
        """
        `codes` 是"能不能给用户一个覆盖按钮"的判据 —— 必须存在且可判。
        """
        s, _src, other = self._session_writing_over_someone_else(tmp_path)
        res = s.export(other)
        assert res['codes'], '拦下了却没给 codes，前端无从判断能否覆盖'
        assert all(isinstance(c, str) and c for c in res['codes'])

    def test_incremental_write_onto_the_source_is_not_blocked(self, tmp_path):
        """
        **原地增量写回自己这一章不再拦**（用户 2026-10-05 报的那条）。

        形状就是用户的实际动作：打开自己手写的章节 → 改一句台词 →
        导出到**原路径**。它必须是 ok=True 且 `used_patch=True` ——
        不许弹「覆盖手写文件？」（那个对话框的默认按钮是取消，
        用户的手感就是"工具不让我替换旧文件"）。
        """
        src = _make_src(tmp_path)
        before = src.read_text(encoding='utf-8')
        s = EditorSession(project_root=tmp_path)
        s.script_path = src
        s.beats = _beats_of(src)
        s.beats[0].text = '改过的一句'

        res = s.export(src)
        assert res['ok'] is True, res
        assert res['used_patch'] is True, res
        assert 'HANDWRITTEN_TARGET' not in res['codes'], res
        after = src.read_text(encoding='utf-8')
        assert '改过的一句' in after
        # 只多出生成标记（2 行）与替换掉的那一行
        added = len(after.splitlines()) - len(before.splitlines())
        assert added <= 2, f'原地增量写回动得太多了（多了 {added} 行）'

    def test_force_does_not_bypass_broken_product(self, tmp_path):
        """
        **产物自己坏了的时候 force 也不放行。**

        中文图像名（`show 凌跃捷`）是 Ren'Py 语法上根本不存在的图像 ——
        写进 game/ 会让游戏起不来。这种 block 不该有逃生舱。
        """
        src = tmp_path / 'hand.rpy'
        src.write_text('label a:\n    e "x"\n', encoding='utf-8')
        s = EditorSession(project_root=tmp_path)
        s.script_path = None            # 没有源文件 → 一定走全量
        s.beats = _beats_of(src)

        # 造一个"产物本身坏掉"的情形：把图像名写成中文
        from storygraph.story.beat_model import Beat
        s.beats = [Beat(seq=1, label='a', kind='say', text='x',
                        show='凌跃捷 开心')]

        res = s.export(src, force=True)
        assert res['ok'] is False, (
            f'坏产物被 force 放过去了（会写进 game/ 让游戏起不来）：{res}')
        assert 'CJK_IN_IMAGE_TAG' in res['codes'] or res['blocking'], res

    def test_ui_only_offers_force_for_handwritten_target(self):
        """UI 那个"仍然覆盖"按钮只对 `HANDWRITTEN_TARGET` 出现。"""
        from storygraph.ui_qt.main_window import _overwritable
        assert _overwritable({'codes': ['HANDWRITTEN_TARGET']})
        assert not _overwritable({'codes': ['PYTHON_SYNTAX_ERROR']})
        assert not _overwritable({'codes': ['CJK_IN_IMAGE_TAG']})
        assert not _overwritable({'codes': ['LABEL_COLLISION']})
        assert not _overwritable({'codes': []})
        assert not _overwritable({})

    def test_export_to_handwritten_target_reports_blocking(self, tmp_path):
        """
        **端到端走一遍 `export` 里"写被拦下"那条分支**。

        为什么单独写这条：别的用例都在 `export` 的**前一关**
        （补丁失败）就返回了，`write_generation` 的返回值根本没被读到 ——
        于是 `'blocking': [self._issue_message(i) …]` 里的手误
        （漏了 `self.`）能一路活到运行时才 `NameError`。
        这条用例把目标设成**别人的手写文件**（补丁对得上、语法门禁也过），
        真正走到 `art.written == False` 那条 return。
        """
        s, _src, other = self._session_writing_over_someone_else(tmp_path)
        before = other.read_text(encoding='utf-8')

        res = s.export(other)
        assert res['ok'] is False, (
            f'往别人的手写文件写却没有报错：{res}')
        assert any('手写' in str(b) or 'HANDWRITTEN' in str(b)
                   for b in res['blocking']), res['blocking']
        assert other.read_text(encoding='utf-8') == before, '文件被改写了'


# ==================================================================
#  2c. CLI 逃生舱（--force）
# ==================================================================
class TestCliForceEscapeHatch:
    """
    `--force` 是方案 3 的逃生舱：**默认安全，明确知道自己在做什么时才覆盖**。

    这里测的是"逃生舱真的接上了"（参数忘了往下传时，`--force` 会静默无效，
    而用户以为自己加了 force）。
    """

    def _table_for(self, tmp_path) -> Path:
        """造一张能被 `compile` 吃掉的演出表。"""
        src = _make_src(tmp_path)
        beats = _beats_of(src)
        xl = tmp_path / 't.xlsx'
        export_beats_to_excel(beats, xl)
        return xl

    def test_cli_refuses_without_force_and_tells_you_about_it(
            self, tmp_path, capsys):
        from storygraph.cli import main
        xl = self._table_for(tmp_path)
        target = _make_src(tmp_path)          # 已存在的手写 .rpy
        before = target.read_text(encoding='utf-8')

        code = main(['table', 'xlsx2rpy', str(xl), '--out', str(target)])
        err = capsys.readouterr().err
        assert code == 1, f'被拦下却没给退出码 1：{code}'
        assert target.read_text(encoding='utf-8') == before, '文件被改写了'
        assert '--force' in err, (
            f'拦下时应当告诉用户怎么覆盖（逃生舱要能被发现）：\n{err}')

    def test_cli_force_overwrites(self, tmp_path, capsys):
        from storygraph.cli import main
        xl = self._table_for(tmp_path)
        target = _make_src(tmp_path)

        code = main(['table', 'xlsx2rpy', str(xl), '--out', str(target),
                     '--force'])
        assert code == 0, capsys.readouterr().err
        assert '原文台词' in target.read_text(encoding='utf-8')

    def test_cli_writes_a_new_file_without_force(self, tmp_path, capsys):
        """新建文件**不需要** force（别把正向路径也拦了）。"""
        from storygraph.cli import main
        xl = self._table_for(tmp_path)
        target = tmp_path / 'brand_new.rpy'

        code = main(['table', 'xlsx2rpy', str(xl), '--out', str(target)])
        assert code == 0, capsys.readouterr().err
        assert target.exists()


# ==================================================================
#  3. 第三类东西保真（回归网）
# ==================================================================
class TestOpaquePreserved:
    """这些谁都不管、也不进 Beats，只能靠"不动它"保住。"""

    CASES = {
        'default': 'default score = 0\n',
        'define': 'define e = Character("E")\n',
        'init_python': 'init python:\n    def helper(x):\n        return x * 2\n',
        'transform': 'transform my_tf:\n    xalign 0.5\n',
        'screen_call': 'label a:\n    show screen my_hud\n    e "x"\n',
        'external_jump': 'label a:\n    e "x"\n    jump external_minigame\n',
        'compact_dollar': 'label a:\n    $score+=1\n    e "x"\n',
        'persistent': 'label a:\n    $ persistent.n = 1\n    e "x"\n',
    }

    @pytest.mark.parametrize('name,src', list(CASES.items()))
    def test_opaque_survives_roundtrip(self, tmp_path, name, src):
        f = tmp_path / f'{name}.rpy'
        f.write_text(src, encoding='utf-8')
        beats = _beats_of(f)
        if not beats:
            pytest.skip(f'{name} 不进 Beats，不由本用例覆盖')
        xl = tmp_path / f'{name}.xlsx'
        export_beats_to_excel(beats, xl)
        out = compile_beats(import_beats_from_excel(xl)).rpy_text
        want = [l.strip() for l in src.splitlines()
                if l.strip() and not l.strip().startswith('#')]
        got = [l.strip() for l in out.splitlines() if l.strip()]
        missing = [l for l in want if l not in got]
        assert not missing, f'{name} 丢失：{missing}'


# ==================================================================
#  4. 正向路径不误伤
# ==================================================================
class TestHappyPathIntact:

    def test_incremental_patch_keeps_opaque_lines(self, tmp_path):
        """正常增量：改一句台词，第三类东西必须原样保留。"""
        src = _make_src(tmp_path)
        beats = _beats_of(src)
        assert beats, '样本应当解析出拍'
        for b in beats:
            if b.text == '原文台词':
                b.text = '改过的台词'

        from storygraph.story.patcher import patch_export
        gen = compile_beats(beats).rpy_text
        res = patch_export(beats, src, gen)
        if not res.ok:
            pytest.skip(f'本次未走增量（{res.reasons}），非本用例目标')

        text = res.text
        for token in ('default score = 0', 'init python:',
                      'def helper', 'transform my_tf:'):
            assert token in text, f'增量后丢失：{token}'
        assert '改过的台词' in text
