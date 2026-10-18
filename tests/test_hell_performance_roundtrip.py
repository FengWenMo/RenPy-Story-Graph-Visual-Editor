"""
tests/test_hell_performance_roundtrip.py
========================================
**"演出里真实会写的花样"** —— 解析 → 拍 → 导出，逐条钉住"不丢 / 不乱序 / 不改字节"。

用户 2026-10-18（原话）：

    > 你试试一些 pause，fade in fade out……at 关键字等在上面硬写一些别的
    > 逻辑继续测试……我严重怀疑你仍旧没有清理完所有的 bug

这一份收的是**这一轮攻击打出来的洞**（每一条都先在真机上复现过）：

  ① `scene … with Fade(1,0,1)` 后面再跟 `show … with dissolve`：
     拍级只有一个 `with`，scene 的那条被覆盖 → **换景从淡入变成硬切**；
  ② `play music "a.ogg"`（文件名不带目录）→ 导出成裸的 `a.ogg`：
     Ren'Py 把它当表达式求值，**NameError，游戏崩**；
  ③ `play sound "s.ogg" volume 0.5` → `volume 0.5` **静默消失**；
  ④ 自定义语句后面跟 `$ renpy.pause(0.5)` → 停顿被排到语句**前面**
     （音效与停顿的时间关系反过来）；
  ⑤ `$ y = (\n 1 + 2\n )`（括号续行）→ 只剩 `$ y = (`，**语法错误**；
  ⑥ `$ x = 1  # 备注` → 备注消失；
  ⑦ `menu:` 里的 `set seen` → 整行消失（玩法记账变了）；
  ⑧ `label start(flag=True):` → 参数表消失（`call start(...)` 报参数过多）；
  ⑨ 台词里的 `{color=…}` 内联标签必须逐字节不动（用户点名的"部分词语变色"）。

跑法：

    python -m pytest tests/test_hell_performance_roundtrip.py -q
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.statement_registry import clear_cache


@pytest.fixture(autouse=True)
def _clean_registry_cache():
    clear_cache()
    yield
    clear_cache()


def _project(tmp_path: Path, rules: dict, script: str) -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    (root / 'storygraph.rules.json').write_text(
        json.dumps(rules, ensure_ascii=False, indent=2), encoding='utf-8')
    (root / 'game' / 'ch.rpy').write_text(script, encoding='utf-8')
    return root


def _render(root: Path) -> str:
    src = root / 'game' / 'ch.rpy'
    res = ParseResultBuilder().build(RenPyParser().parse(src))
    return render_beats_to_rpy(beats_from_parse_result(res), label_name=None)


def _body(text: str) -> list:
    """产物/源码的"内容行"（去生成标记、去空行；`return` 是收尾补的，不算）。"""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith('# 由剧本表自动生成'):
            continue
        out.append(s)
    return out


def _assert_in_order(src: str, out: str) -> None:
    """
    源码里的每一行都要在产物里**按原顺序**出现（允许产物多出 `return`）。

    比"逐字节相等"宽、比"包含"严：**丢一行**与**换个位置**都会红 ——
    这两类才是"演出悄悄变了"的真身。
    """
    out_lines = _body(out)
    i = 0
    for want in _body(src):
        while i < len(out_lines) and out_lines[i] != want:
            i += 1
        assert i < len(out_lines), (
            f'这一行在产物里丢了或顺序被改：{want!r}\n'
            f'--- 产物 ---\n{out}')
        i += 1


# ---------------------------------------------------------------------------
#  ① 两条转场各归各位
# ---------------------------------------------------------------------------

class TestTwoTransitions:
    """
    一拍只有一个拍级 `with`。`scene … with Fade` + `show … with dissolve`
    是**最常见的换景写法**，从前后一条覆盖前一条 → 换景硬切。
    """

    def test_scene_transition_survives_a_second_transition(self, tmp_path):
        script = ('label ch:\n'
                  '    scene bg room with Fade(1, 0, 1)\n'
                  '    show e happy with dissolve\n'
                  '    e "hi"\n')
        out = _render(_project(tmp_path, {}, script))
        assert re.search(r'scene bg room with Fade\(1, 0, 1\)', out), out
        assert re.search(r'show e happy with dissolve', out), out

    def test_a_single_transition_is_untouched(self, tmp_path):
        script = 'label ch:\n    scene bg room with fade\n    e "hi"\n'
        out = _render(_project(tmp_path, {}, script))
        assert 'scene bg room with fade' in out, out


# ---------------------------------------------------------------------------
#  ②③ 音频：文件名必须加引号、没建模的参数不许丢
# ---------------------------------------------------------------------------

class TestAudioRefs:
    """
    不带目录的文件名（`a.ogg` / `v.ogg`）从前导出成裸标识符 ——
    Ren'Py 里那是表达式，运行到就 `NameError`。体积最小、后果最重的一条。
    """

    @pytest.mark.parametrize('line', [
        'play music "a.ogg"',
        'queue music "b.ogg"',
        'voice "v.ogg"',
        'play sound "hit.ogg"',
    ])
    def test_a_bare_filename_is_quoted(self, tmp_path, line):
        script = f'label ch:\n    {line}\n    e "hi"\n'
        out = _render(_project(tmp_path, {}, script))
        assert line in out, out

    def test_unmodeled_audio_args_are_preserved(self, tmp_path):
        """`volume 0.5` / `if_changed` 不在工具的词表里 —— 但绝不能丢。"""
        script = ('label ch:\n'
                  '    play sound "s.ogg" volume 0.5\n'
                  '    play music "bgm/a.ogg" fadein 2.0 if_changed\n'
                  '    e "hi"\n')
        out = _render(_project(tmp_path, {}, script))
        assert 'volume 0.5' in out, out
        assert 'if_changed' in out, out
        # 已建模的参数仍在（顺序不参与语义，逐词比对）
        assert 'fadein 2.0' in out, out

    def test_stop_with_fadeout_is_unchanged(self, tmp_path):
        script = 'label ch:\n    stop music fadeout 1.0\n    e "hi"\n'
        out = _render(_project(tmp_path, {}, script))
        assert 'stop music fadeout 1.0' in out, out


# ---------------------------------------------------------------------------
#  ④ 自定义语句后面的代码不许排到它前面
# ---------------------------------------------------------------------------

class TestOrderAfterCustomStatement:
    """
    `sfx_mix …` 后面紧跟 `$ renpy.pause(0.5)`：从前 `$` 被当成这一拍的第一项，
    导出时排到了语句**前面** —— 先停 0.5 秒再放音效，时间关系反了。
    """

    RULES = {'statements': [
        {'name': 'sfx_mix', 'label': '多轨音效', 'pattern': [
            {'kind': 'keyword', 'text': 'sfx_mix', 'column': '动作名'},
            {'kind': 'choice', 'column': '声道', 'choices': ['se', 'se2'],
             'multi': True},
            {'kind': 'variable', 'column': '文件'},
            {'kind': 'optional', 'text': 'gain', 'column': '音量'}]},
    ]}

    def test_code_after_the_statement_stays_after(self, tmp_path):
        script = ('label ch:\n'
                  '    sfx_mix se se2 "se/a.ogg" gain=-6\n'
                  '    $ renpy.pause(0.5)\n'
                  '    window hide\n'
                  '    e "hi"\n')
        out = _render(_project(tmp_path, self.RULES, script))
        _assert_in_order(script, out)

    def test_the_statement_itself_is_byte_identical(self, tmp_path):
        script = ('label ch:\n'
                  '    sfx_mix se se2 "se/a.ogg" gain=-6\n'
                  '    $ renpy.pause(0.5)\n'
                  '    e "hi"\n')
        out = _render(_project(tmp_path, self.RULES, script))
        assert 'sfx_mix se se2 "se/a.ogg" gain=-6' in out, out


# ---------------------------------------------------------------------------
#  ⑤⑥ 代码：括号续行与行尾注释
# ---------------------------------------------------------------------------

class TestCodeFidelity:
    def test_multiline_dollar_code_survives(self, tmp_path):
        """
        `$ y = (\n 1 + 2\n )` 从前只剩 `$ y = (` —— 产物是**语法错误**。
        现在要么原样、要么等价地包成 `python:` 块；判据是**三行都在**。
        """
        script = ('label ch:\n'
                  '    $ y = (\n'
                  '        1 + 2\n'
                  '      )\n'
                  '    e "hi"\n')
        out = _render(_project(tmp_path, {}, script))
        assert '1 + 2' in out, out
        assert 'y = (' in out, out
        # 再解析一轮也得吃得下（语法没坏）
        p = tmp_path / 'again.rpy'
        p.write_text(out, encoding='utf-8')
        res = ParseResultBuilder().build(RenPyParser().parse(p))
        assert not any('语法' in e for e in res.errors), res.errors

    def test_inline_comment_on_dollar_survives(self, tmp_path):
        script = 'label ch:\n    $ x = 1  # 行尾注释\n    e "hi"\n'
        out = _render(_project(tmp_path, {}, script))
        assert '$ x = 1  # 行尾注释' in out, out


# ---------------------------------------------------------------------------
#  ⑦ menu 块里的非选项语句
# ---------------------------------------------------------------------------

class TestMenuExtras:
    def test_menu_set_survives(self, tmp_path):
        """`menu: set seen` 是 Ren'Py 的合法写法（决定这一菜单记不记已读）。"""
        script = ('label ch:\n'
                  '    menu:\n'
                  '        set seen\n'
                  '        "选项" if flag:\n'
                  '            jump other\n')
        out = _render(_project(tmp_path, {}, script))
        assert 'set seen' in out, out
        # 位置：必须在 menu: 之后、选项之前
        assert out.index('menu:') < out.index('set seen') < out.index('"选项"'), out

    def test_menu_dollar_survives(self, tmp_path):
        script = ('label ch:\n'
                  '    menu:\n'
                  '        $ x = 1\n'
                  '        "选项":\n'
                  '            jump other\n')
        out = _render(_project(tmp_path, {}, script))
        assert '$ x = 1' in out, out

    def test_comment_inside_a_menu_block_survives(self, tmp_path):
        """
        真机 `script.rpy` 的第二个菜单里写着 `# … begin` / `# … end`
        （协作时用来分段）。从前 `_parse_menu` 认不出就 `i += 1` —— **整行消失**。
        """
        script = ('label ch:\n'
                  '    menu:\n'
                  '        # 协作：这一段是 liuqin 加的\n'
                  '        "选项":\n'
                  '            jump other\n')
        out = _render(_project(tmp_path, {}, script))
        assert '# 协作：这一段是 liuqin 加的' in out, out
        assert out.index('menu:') < out.index('# 协作') < out.index('"选项"'), out


# ---------------------------------------------------------------------------
#  ⑧ label 参数表
# ---------------------------------------------------------------------------

class TestLabelParams:
    def test_label_params_survive(self, tmp_path):
        script = 'label start(flag=True, who="凌跃捷"):\n    e "hi"\n'
        out = _render(_project(tmp_path, {}, script))
        assert 'label start(flag=True, who="凌跃捷"):' in out, out

    def test_plain_label_stays_plain(self, tmp_path):
        script = 'label start:\n    e "hi"\n'
        out = _render(_project(tmp_path, {}, script))
        assert 'label start:' in out, out
        assert 'label start(' not in out, out


# ---------------------------------------------------------------------------
#  ⑨ 内联文字标签 / pause / with：逐字节不动
# ---------------------------------------------------------------------------

class TestVerbatimLines:
    """
    这几行**一个字都不许改**：内联标签是内容（用户点名的"部分词语变色"），
    `pause` / `with` / `window` 是节奏本身。
    """

    SCRIPT = ('label ch:\n'
              '    e "{color=#ff0000}红{/color}与{i}斜{/i}"\n'
              '    e "引号在标签里{color=#ff0000}\\"你好\\"{/color}结束"\n'
              '    e "插值{size=+8}[player]{/size}" with dissolve\n'
              '    pause 1.0 hard\n'
              '    with fade\n'
              '    window hide\n'
              '    show e at Transform(zoom=1.2, alpha=0.5)\n'
              '    "收尾"\n')

    def test_every_line_is_byte_identical_and_in_order(self, tmp_path):
        out = _render(_project(tmp_path, {}, self.SCRIPT))
        _assert_in_order(self.SCRIPT, out)


# ---------------------------------------------------------------------------
#  ⑫ 行尾注释：贴得上就贴行尾，贴不上就**另起一行** —— 但不许丢
# ---------------------------------------------------------------------------

class TestInlineComments:
    """
    一拍只有一位行尾注释，而且它只贴得上**单行拍**。多行拍里
    `scene … #白色转场` / `play music … #主题曲` 从前**静默消失**。
    """

    def test_single_line_beat_keeps_the_comment_inline(self, tmp_path):
        """能贴行尾就必须贴行尾（一个字节都不动）。"""
        script = ('label ch:\n'
                  '    scene white with Dissolve(1.5)  #白色转场\n'
                  '    return\n')
        out = _render(_project(tmp_path, {}, script))
        assert 'scene white with Dissolve(1.5)  #白色转场' in out, out

    def test_multi_line_beat_moves_the_comment_above(self, tmp_path):
        """贴不上（多行拍）就另起一行 —— 字一个不少，位置换到头顶。"""
        script = ('label ch:\n'
                  '    scene bg room with fade  # 换景\n'
                  '    show e happy at left  # 站左边\n'
                  '    e "hi"\n')
        out = _render(_project(tmp_path, {}, script))
        assert '# 换景' in out, out
        assert '# 站左边' in out, out
        # 两条都在 `scene` 之前（不贴错行）
        assert out.index('# 换景') < out.index('scene bg room'), out

    @pytest.mark.parametrize('line', [
        'play music "a.ogg"  # 主题曲',
        'pause 0.5  # 等一下',
        'voice "v.ogg"  # 旁白',
    ])
    def test_audio_and_raw_comments_survive(self, tmp_path, line):
        script = f'label ch:\n    {line}\n    e "hi"\n'
        out = _render(_project(tmp_path, {}, script))
        comment = line.split('#', 1)[1].strip()
        assert comment in out, out

    def test_a_say_comment_does_not_leak_to_the_next_beat(self, tmp_path):
        """`e "hi" #备注` 之后那句台词**不许**继承这个注释。"""
        script = 'label ch:\n    e "hi"  # 台词注释\n    "收尾"\n'
        out = _render(_project(tmp_path, {}, script))
        assert out.count('# 台词注释') == 1, out
        assert '"收尾"  # 台词注释' not in out, out


# ---------------------------------------------------------------------------
#  ⑩ 表格往返（Excel 那条路）：新字段必须随表走
# ---------------------------------------------------------------------------

class TestExcelRoundTrip:
    """
    这些新字段（label 参数 / 未建模音频参数）都藏在**隐藏列**里。
    隐藏列最容易在"过一趟表"时无声消失 —— 所以单独钉一次。
    """

    def test_label_params_and_audio_args_survive_the_sheet(self, tmp_path):
        from storygraph.story.excel_export import export_beats_to_excel
        from storygraph.story.excel_import import import_beats_from_excel
        from storygraph.story.statement_registry import registry_for_parse_result

        script = ('label start(flag=True):\n'
                  '    play sound "s.ogg" volume 0.5\n'
                  '    e "带{color=#f00}色{/color}" with dissolve\n')
        root = _project(tmp_path, {}, script)
        src = root / 'game' / 'ch.rpy'
        res = ParseResultBuilder().build(RenPyParser().parse(src))
        reg = registry_for_parse_result(res)
        from storygraph.story.statement_registry import annotate_spec_statements
        annotate_spec_statements(res.statements, reg)
        beats = beats_from_parse_result(res)

        xlsx = tmp_path / 'ch.xlsx'
        export_beats_to_excel(beats, xlsx)
        back = import_beats_from_excel(xlsx)
        out = render_beats_to_rpy(back, label_name=None)
        assert 'label start(flag=True):' in out, out
        assert 'volume 0.5' in out, out
        assert '带{color=#f00}色{/color}' in out, out


# ---------------------------------------------------------------------------
#  ⑪ 卡片：一张拍上多条语句时，参数格必须说清"这一格改的是哪一行"
# ---------------------------------------------------------------------------

class TestCardGroups:
    """
    一张拍可以同时带着：声明过的 `mycue …` + 它的块体 + `pause` / `with` /
    `window`。这些语句的参数列会一起长出来，从前**没有归属标记** ——
    卡片上就是"内容 / 强度 / 秒数 / 转场 / 动作"一串，人不知道
    「秒数」改的是哪一行。现在按语句分组、组前写明那一行。
    """

    RULES = {'statements': [
        {'name': 'mycue', 'label': '我的提示', 'pattern': [
            {'kind': 'keyword', 'text': 'mycue', 'column': '动作名'},
            {'kind': 'variable', 'column': '内容'},
            {'kind': 'optional', 'text': 'intensity', 'column': '强度'}]},
    ]}

    SCRIPT = ('label ch:\n'
              '    mycue "闪电" intensity=3:\n'
              '        target = ling\n'
              '    pause 1.0 hard\n'
              '    with fade\n'
              '    window hide\n'
              '    "收尾"\n')

    @pytest.fixture
    def window(self, tmp_path):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        pytest.importorskip('PySide6')
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from storygraph.ui_qt.main_window import MainWindowQt
        root = _project(tmp_path, self.RULES, self.SCRIPT)

        class _Engine:
            project_root = str(root)

            def get_registry(self):
                return None

        win = MainWindowQt(_Engine(), root / 'game' / 'ch.rpy')
        win._set_page(win.PAGE_CARD)
        app.processEvents()
        yield win
        win.close()

    def test_each_statement_gets_its_own_caption(self, window):
        from PySide6.QtWidgets import QLabel
        window._show_index(len(window.beats) - 1)
        panel = window._panel
        caps = [w.text() for w in panel._spec_host.findChildren(QLabel)
                if w.text().startswith('◆')]
        joined = ' | '.join(caps)
        assert 'mycue' in joined, joined
        assert 'pause 1.0 hard' in joined, joined
        assert 'with fade' in joined, joined
        assert 'window hide' in joined, joined

    def test_the_loose_lines_are_all_named(self, window):
        window._show_index(len(window.beats) - 1)
        text = window._panel._loose_label.text()
        for want in ('pause 1.0 hard', 'with fade', 'window hide',
                     'target = ling'):
            assert want in text, f'{want!r} 没出现在原样行里：\n{text}'

    def test_editing_pause_keeps_the_hard_keyword(self, window, tmp_path):
        """`pause 1.0 hard` 只改秒数 —— `hard` 不许被折掉。"""
        from storygraph.app import beats as beats_api
        window._show_index(len(window.beats) - 1)
        panel = window._panel
        beat = window.beats[len(window.beats) - 1]
        header = next(h for h in panel._spec_widgets if 'seconds' in h)
        panel._spec_widgets[header].setText('2.0')
        beats_api.apply_spec_arg_fields(beat, panel._spec_values())
        assert 'pause 2.0 hard' in beat.raw, beat.raw


class TestInlineTagPreview:
    """
    用户 2026-10-18："一句话中只有部分关键词语变色"。
    台词框里是**源码**（对的），但"最终长什么样"只有游戏里才知道 ——
    卡片给一行富文本预览，标签写错时至少看得见"这句带样式"。
    """

    def _window(self, tmp_path, text):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        pytest.importorskip('PySide6')
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from storygraph.ui_qt.main_window import MainWindowQt
        root = _project(tmp_path, {}, f'label ch:\n    e "{text}"\n')

        class _Engine:
            project_root = str(root)

            def get_registry(self):
                return None

        win = MainWindowQt(_Engine(), root / 'game' / 'ch.rpy')
        win._set_page(win.PAGE_CARD)
        win._show_index(0)
        app.processEvents()
        return win

    def test_tagged_line_gets_a_colored_preview(self, tmp_path):
        win = self._window(
            tmp_path, '开场，{color=#ff0000}红色{/color}与{i}斜体{/i}。')
        prev = win._panel._rich_preview
        try:
            assert prev.isVisibleTo(win._panel), '有标签却没有预览'
            html = prev.text()
            assert 'color:#ff0000' in html, html
            assert '<i>' in html, html
            assert '{color' not in html, '预览里还露着源码标签'
        finally:
            win.close()

    def test_plain_line_has_no_preview(self, tmp_path):
        win = self._window(tmp_path, '普通台词，没有标签。')
        prev = win._panel._rich_preview
        try:
            assert not prev.isVisibleTo(win._panel), '普通台词不该占一行预览'
        finally:
            win.close()

