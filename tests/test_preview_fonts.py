"""
tests/test_preview_fonts.py
===========================
**演出预览用哪支字体**：与引擎一致，而且说得清"从哪儿来的"。

为什么单开一个文件（2026-10-21）：

  · 预览从前只读 `gui.text_font` / `gui.name_text_font`。Ren'Py 的
    `gui.text_properties("dialogue")` 会**优先**用 `gui.dialogue_text_font`
    —— 项目一覆盖字体或字号，预览画的就是另一支字，看起来像、其实不是；
  · `Character(..., what_font=…, who_font=…)` 让某个角色单独换字体，
    预览完全没读；
  · `FontGroup`（CJK 混排）预览只能画一支 —— 必须**明说**，不许静默。

判据：解析结果（路径 + 来源）对，渲染真的用了那支字（记录 `_load_font`）。
"""

from pathlib import Path

import pytest

pytest.importorskip('PIL')


def _project(tmp_path, gui_body: str, extra_rpy: str = ''):
    game = tmp_path / 'game'
    (game / 'fonts').mkdir(parents=True, exist_ok=True)
    for name in ('a.ttf', 'b.ttf', 'c.ttf', 'd.ttf'):
        (game / 'fonts' / name).write_bytes(b'not a real font')
    (game / 'gui.rpy').write_text('define gui.init(1280, 720)\n' + gui_body,
                                  encoding='utf-8')
    if extra_rpy:
        (game / 'definitions.rpy').write_text(extra_rpy, encoding='utf-8')
    return tmp_path


def _profile(project):
    from storygraph.story.renpy_gui import load_gui_profile
    return load_gui_profile(project, force=True)


GUI_BASIC = '''
define gui.text_font = "fonts/a.ttf"
define gui.name_text_font = "fonts/a.ttf"
define gui.text_size = 33
'''


class TestDialogueFontOverride:
    def test_dialogue_text_font_wins_over_text_font(self, tmp_path):
        """`gui.dialogue_text_font` 优先（Ren'Py 的解析顺序），且来源说得出来。"""
        p = _project(tmp_path, GUI_BASIC + '''
define gui.dialogue_text_font = "fonts/b.ttf"
define gui.dialogue_text_size = 40
''')
        prof = _profile(p)
        assert Path(prof.dialogue_font_path).name == 'b.ttf'
        assert prof.dialogue_font_source == 'gui.dialogue_text_font'
        assert prof.dialogue_text_size == 40
        # 名条仍用 gui.name_text_font
        assert Path(prof.name_font_path).name == 'a.ttf'

    def test_without_override_it_falls_back_and_says_so(self, tmp_path):
        prof = _profile(_project(tmp_path, GUI_BASIC))
        assert Path(prof.dialogue_font_path).name == 'a.ttf'
        assert prof.dialogue_font_source == 'gui.text_font'
        assert prof.dialogue_text_size == 33

    def test_dialogue_text_outlines_win(self, tmp_path):
        prof = _profile(_project(tmp_path, GUI_BASIC + '''
define gui.text_outlines = [(2, "#000000", 0, 0)]
define gui.dialogue_text_outlines = [(4, "#112233", 1, 1)]
'''))
        assert prof.dialogue_text_outlines == [(4, '#112233', 1, 1)]


class TestCharacterFonts:
    def test_character_what_and_who_font_are_read(self, tmp_path):
        p = _project(tmp_path, GUI_BASIC, '''
define jc = Character("JC", what_font="fonts/c.ttf", who_font="fonts/d.ttf")
define other = Character("别的")
''')
        prof = _profile(p)
        what, src = prof.font_for('jc', 'JC', 'what')
        who, src2 = prof.font_for('jc', 'JC', 'who')
        assert Path(what).name == 'c.ttf'
        assert Path(who).name == 'd.ttf'
        assert 'jc' in src and 'what_font' in src
        assert 'jc' in src2 and 'who_font' in src2
        # 没写字体的角色 → 用全局
        w2, s2 = prof.font_for('other', '别的', 'what')
        assert Path(w2).name == 'a.ttf'

    def test_missing_character_font_falls_back_with_a_warning(self, tmp_path):
        p = _project(tmp_path, GUI_BASIC, '''
define jc = Character("JC", what_font="fonts/nope.ttf")
''')
        prof = _profile(p)
        assert any('nope.ttf' in w for w in prof.warnings), prof.warnings
        what, _src = prof.font_for('jc', 'JC', 'what')
        assert Path(what).name == 'a.ttf'


class TestFontGroup:
    def test_fontgroup_is_not_silently_mocked(self, tmp_path):
        """
        `FontGroup` 是"按字符区间混排"，预览做不了 —— 取第一支并**明说**。
        """
        p = _project(tmp_path, '''
define gui.text_font = FontGroup().add("fonts/a.ttf", "0x20-0x7f").add("fonts/b.ttf", "0x4e00-0x9fff")
''')
        prof = _profile(p)
        assert any('FontGroup' in w for w in prof.warnings), prof.warnings
        assert Path(prof.text_font_path).name == 'a.ttf'


class TestRenderUsesTheResolvedFont:
    def test_say_drawing_loads_the_dialogue_font(self, tmp_path, monkeypatch):
        """渲染真的用了 `dialogue_text_font`（不是只解析对了）。"""
        p = _project(tmp_path, GUI_BASIC + '''
define gui.dialogue_text_font = "fonts/b.ttf"
''')
        prof = _profile(p)
        from storygraph.app import stage_render as R

        seen = []
        real = R._load_font

        def spy(path, size, bold=False, italic=False):
            seen.append((str(path), int(size)))
            return real(path, size, bold, italic)

        monkeypatch.setattr(R, '_load_font', spy)

        frame = type('F', (), {
            'shows_window': True, 'speaker': 'jc', 'speaker_name': 'JC',
            'display_speaker': 'JC', 'text': '一句台词'})()
        ctx = R._Ctx(profile=prof, registry=None, project_root=p,
                     screen=(1280, 720), transform_index={}, image_index={},
                     layer_model=None)
        canvas = R.Image.new('RGBA', (1280, 720), (0, 0, 0, 255))
        result = R.RenderResult(width=1280, height=720)
        R._draw_say(canvas, frame, ctx, result)

        used = {Path(path).name for path, _size in seen if path}
        assert 'b.ttf' in used, f'台词没用 dialogue_text_font：{used}'
        assert any(size == prof.dialogue_text_size for _p_, size in seen
                   if Path(_p_).name == 'b.ttf')


class TestThePanelSaysItOutLoud:
    def test_font_note_names_the_file_and_the_source(self, tmp_path):
        """
        横条上要写"这一句用哪支字、从哪儿来"。

        预览画得像引擎，但字体悄悄不是同一支时，用户没有任何线索 ——
        这正是 2026-10-21 那条问题的形态：不是画错了，是**没说**。
        """
        pytest.importorskip('PySide6')
        p = _project(tmp_path, GUI_BASIC + '''
define gui.dialogue_text_font = "fonts/b.ttf"
''')
        prof = _profile(p)
        from storygraph.ui_qt.stage_preview import StagePreviewWindow

        panel = type('P', (), {})()
        panel._engine = True
        panel._profile = prof
        frame = type('F', (), {'speaker': 'jc', 'display_speaker': 'JC'})()
        note = StagePreviewWindow._font_note(panel, frame)
        assert 'b.ttf' in note, note
        assert 'dialogue_text_font' in note, note

    def test_schematic_mode_does_not_claim_engine_fonts(self, tmp_path):
        """示意图模式不谈"引擎字体"（那边本来就不是引擎排版）。"""
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.stage_preview import StagePreviewWindow

        panel = type('P', (), {})()
        panel._engine = False
        panel._profile = None
        frame = type('F', (), {'speaker': '', 'display_speaker': ''})()
        assert StagePreviewWindow._font_note(panel, frame) == ''

