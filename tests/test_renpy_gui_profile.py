"""
tests/test_renpy_gui_profile.py
===============================
项目 `gui.rpy` / `screens.rpy` / 角色定义 → 可绘制排版参数。

这些判据对应"预览要跟真正编译的一样"的第一层：**先读对项目自己的数**。
尤其钉住两个实测踩过的坑：

  · `@gui.variant def small():` 里的 `gui.text_size = 45` 是手机变体，
    桌面预览不能采用（否则 39 号字被画成 45 号）；
  · `style window: variant "small": background "gui/phone/textbox.png"`
    也不能当成桌面文本框底图。
"""

from __future__ import annotations

from pathlib import Path

from storygraph.story.renpy_gui import load_gui_profile


def _project(tmp_path: Path) -> Path:
    game = tmp_path / 'game'
    (game / 'gui').mkdir(parents=True)
    (game / 'gui' / 'textbox.png').write_bytes(b'not-a-real-png')
    (game / 'gui' / 'namebox.png').write_bytes(b'not-a-real-png')
    (game / 'gui' / 'phone').mkdir()
    (game / 'gui' / 'phone' / 'textbox.png').write_bytes(b'phone')
    (game / 'gui.rpy').write_text('''
init python:
    gui.init(1280, 720)

define gui.textbox_height = 200
define gui.textbox_yalign = 1.0
define gui.text_font = "fonts/does-not-exist.ttf"
define gui.text_size = 39
define gui.text_color = '#112233'
define gui.name_text_color = gui.text_color
define gui.text_outlines = [(3, "#000000", 1, 1)]
define gui.namebox_borders = Borders(5, 6, 7, 8)
define gui.name_xpos = 100
define gui.name_ypos = -50
define gui.dialogue_xpos = 120
define gui.dialogue_ypos = 30
define gui.dialogue_width = 900

init python:
    gui.text_size = 24

    @gui.variant
    def touch():
        gui.text_size = 48
        gui.textbox_height = 400

    @gui.variant
    def small():
        gui.text_size = 48
        gui.textbox_height = 400
        gui.name_xpos = 10
''', encoding='utf-8')
    (game / 'screens.rpy').write_text('''
screen say(who, what):
    window:
        id "window"
        text who id "who"
        text what id "what"

style window:
    xalign 0.5
    xfill True
    background Image("gui/textbox.png")

style window:
    variant "small"
    background "gui/phone/textbox.png"

style namebox:
    xpos gui.name_xpos
    background Frame("gui/namebox.png", gui.namebox_borders)
''', encoding='utf-8')
    (game / 'definitions.rpy').write_text('''
default l_name = "「蓝」"

define l = DynamicCharacter('l_name', image='lan',
    who_color='#ff0000', what_prefix='「', what_suffix='」')
define n = Character("旁白", what_color="#00ff00")
''', encoding='utf-8')
    return tmp_path


class TestGuiValues:
    def test_screen_and_desktop_values(self, tmp_path):
        p = load_gui_profile(_project(tmp_path), force=True)
        assert p.screen == (1280, 720)
        assert p.textbox_height == 200
        assert p.text_size == 24, '手机变体的 48 号字不能盖掉桌面 24 号'

    def test_small_variant_is_skipped(self, tmp_path):
        p = load_gui_profile(_project(tmp_path), force=True)
        assert p.values['name_xpos'] == 100, 'small 变体的 10 不能生效'
        assert p.textbox_height == 200

    def test_gui_reference_resolves(self, tmp_path):
        p = load_gui_profile(_project(tmp_path), force=True)
        assert p.name_text_color == '#112233'

    def test_borders_and_outlines(self, tmp_path):
        p = load_gui_profile(_project(tmp_path), force=True)
        assert p.namebox_padding == (5.0, 6.0, 7.0, 8.0)
        assert p.text_outlines == [(3.0, '#000000', 1.0, 1.0)]

    def test_textbox_image_ignores_phone_variant(self, tmp_path):
        p = load_gui_profile(_project(tmp_path), force=True)
        assert p.textbox_image
        assert Path(p.textbox_image).name == 'textbox.png'
        assert 'phone' not in Path(p.textbox_image).parts

    def test_missing_font_falls_back_with_a_warning(self, tmp_path):
        p = load_gui_profile(_project(tmp_path), force=True)
        assert any('没有正文字体' in w for w in p.warnings)
        # **"替换了"和"没得替换"是两句话**（2026-10-22，CI 的 Linux 上抓到）：
        # 系统字体查找只认 `C://Windows//Fonts`，Linux 上一个候选都没有 ——
        # 于是这只走"系统也找不到可用字体"的 warning，`fonts_substituted` 空。
        # 用户真正需要的是"**有一句话**说清这支字体怎么了"，不是某个字段非空。
        assert p.fonts_substituted or any('找不到可用字体' in w
                                          for w in p.warnings), \
            '字体没找到却一句话都没说'


class TestFontSearchOrder:
    r"""
    字体搜索顺序 = **引擎的搜索顺序**（2026-10-20 实测踩坑）：

    `the_question` 只写 `gui.text_font = "DejaVuSans.ttf"`，字体在
    `<SDK>/renpy/common/`。旧实现先翻 `C:\Windows\Fonts`，抓到微软雅黑，
    预览的字宽/字重和真机差一截（"Sylvie" 82px vs 引擎 88px）。
    """

    def _sdk(self, tmp_path: Path, *fonts: str) -> Path:
        sdk = tmp_path / 'sdk'
        common = sdk / 'renpy' / 'common'
        common.mkdir(parents=True)
        (sdk / 'renpy.py').write_text('# fake sdk\n', encoding='utf-8')
        for name in fonts:
            (common / name).write_bytes(b'not-a-real-font')
        return sdk

    def _proj(self, sdk: Path, gui_text: str) -> Path:
        proj = sdk / 'myproj'
        game = proj / 'game'
        (game / 'gui').mkdir(parents=True)
        (game / 'gui.rpy').write_text(
            'define gui.text_font = "%s"\n' % gui_text, encoding='utf-8')
        return proj

    def test_sdk_common_beats_windows_for_the_same_name(self, tmp_path):
        # msyh.ttc 在本机 Windows 字体目录里确实存在 —— 正好用来证明
        # SDK 自带的那份排在系统字体**前面**。
        sdk = self._sdk(tmp_path, 'msyh.ttc')
        proj = self._proj(sdk, 'msyh.ttc')
        p = load_gui_profile(proj, force=True)
        assert Path(p.text_font_path) == sdk / 'renpy' / 'common' / 'msyh.ttc'

    def test_same_name_from_sdk_is_not_called_a_substitution(self, tmp_path):
        sdk = self._sdk(tmp_path, 'DejaVuSans.ttf')
        proj = self._proj(sdk, 'DejaVuSans.ttf')
        p = load_gui_profile(proj, force=True)
        assert Path(p.text_font_path).name == 'DejaVuSans.ttf'
        assert p.fonts_substituted == []

    def test_font_shipped_with_the_project_wins(self, tmp_path):
        sdk = self._sdk(tmp_path, 'MyFont.ttf')
        proj = self._proj(sdk, 'fonts/MyFont.ttf')
        (proj / 'game' / 'fonts').mkdir()
        (proj / 'game' / 'fonts' / 'MyFont.ttf').write_bytes(b'project font')
        p = load_gui_profile(proj, force=True)
        assert Path(p.text_font_path) == (proj / 'game' / 'fonts'
                                          / 'MyFont.ttf')
        # 正文那一支**不许**有替换记录（项目字体赢了）。
        # ⚠ `fonts_substituted` 里可能还有一条 `DejaVuSans.ttf → MyFont.ttf`
        # —— 那是**名条字体**：`gui.name_text_font` 有默认值
        # `DejaVuSans.ttf`（`DEFAULTS` 表），项目没写它 → 回退到正文那支。
        # 在 Windows 上能从 SDK/系统找到 DejaVuSans 所以不记；Linux 没有系统
        # 字体目录就记了。这条记录说的是实话（请求 A、实际 B），只是与
        # "正文用哪支字"无关 —— 所以只针对正文那一条断言。
        assert not any('MyFont.ttf' in x and x.startswith('fonts/MyFont')
                       for x in p.fonts_substituted)


class TestCharacters:
    def test_dynamic_character_uses_default_name(self, tmp_path):
        p = load_gui_profile(_project(tmp_path), force=True)
        style = p.characters['l']
        assert style.name == '「蓝」'
        assert style.image_tag == 'lan'
        assert style.who_color == '#ff0000'
        assert style.what_prefix == '「' and style.what_suffix == '」'

    def test_plain_character(self, tmp_path):
        p = load_gui_profile(_project(tmp_path), force=True)
        style = p.characters['n']
        assert style.name == '旁白'
        assert style.what_color == '#00ff00'

    def test_lookup_by_display_name(self, tmp_path):
        p = load_gui_profile(_project(tmp_path), force=True)
        assert p.character_for('n', '旁白').id == 'n'
        assert p.character_for('unknown', '不存在').id == 'unknown'


class TestFontCacheInvalidation:
    """
    **字体文件**也是缓存的一部分（2026-10-20 修，P3）。

    缓存键只含 `.rpy` 的 mtime。作者按 warning 的提示把缺的字体丢进
    `game/fonts/` 时，没有任何一个 `.rpy` 变化 → 缓存永远命中，
    预览只好一直用替代字体（字宽/字形都和真机不一样），得重启编辑器。
    真机味道：`sr_general` 用的是 `Plix-Regular.otf`，换成雅黑后
    "Sylvie" 从 88px 变成 82px —— 字号一样，字不是那个字。
    """

    def test_a_font_added_later_is_picked_up_without_force(self, tmp_path):
        proj = _project(tmp_path)
        first = load_gui_profile(proj)
        assert any('does-not-exist' in w for w in first.warnings), (
            '基线：这个项目本来就缺字体')

        font = proj / 'game' / 'fonts' / 'does-not-exist.ttf'
        font.parent.mkdir(parents=True, exist_ok=True)
        font.write_bytes(b'\x00' * 16)
        again = load_gui_profile(proj)          # 不 force = 再开一次预览
        assert again.text_font_path == str(font), (
            '刚丢进来的字体没生效 —— 缓存没复核字体文件')
        assert not any('does-not-exist' in w for w in again.warnings)

    def test_a_font_removed_later_falls_back_again(self, tmp_path):
        proj = _project(tmp_path)
        font = proj / 'game' / 'fonts' / 'does-not-exist.ttf'
        font.parent.mkdir(parents=True, exist_ok=True)
        font.write_bytes(b'\x00' * 16)
        assert load_gui_profile(proj).text_font_path == str(font)

        font.unlink()
        back = load_gui_profile(proj)
        assert back.text_font_path != str(font), '字体没了还在用它'
        assert any('does-not-exist' in w for w in back.warnings)

    def test_unchanged_fonts_still_hit_the_cache(self, tmp_path):
        """没变就别重建（预览每开一次都要取 profile）。"""
        proj = _project(tmp_path)
        assert load_gui_profile(proj) is load_gui_profile(proj)


class TestRealProjectIfPresent:
    def test_sr_general_reads_1920_and_plix(self):
        root = Path(r'D:\千连万理\RenPyTest\sr_general')
        if not (root / 'game' / 'gui.rpy').is_file():
            return
        p = load_gui_profile(root, force=True)
        assert p.screen == (1920, 1080)
        assert p.textbox_height == 277
        assert p.text_size == 39
        assert p.text_font_path and 'Plix-Regular.otf' in p.text_font_path
        assert 'phone' not in (p.textbox_image or '')
