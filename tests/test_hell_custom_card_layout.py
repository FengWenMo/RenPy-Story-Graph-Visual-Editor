"""
tests/test_hell_custom_card_layout.py
=====================================
**"不是每个写法都跟 show 一样与图片相关"** —— 自定义语句在**卡片编辑器**里的排版。

用户 2026-10-18（原话）：

    > 测试新建全新的写法 + 旧有写法的一些别样扩展（show 除外），以及，
    > 尤其要测试这些不同的写法，在卡片编辑器之中如何排版？
    > 因为不是每个写法都跟 show 一样与图片相关……有的可能是声音
    > （多音轨、多声部），有的可能是文字（比如给文字添加有色字体一类的
    > 非常变态的逻辑），此时卡片编辑器逻辑又如何处理？

这一份与前面几份的分工
----------------------
  · `test_hell_custom_module.py` —— 声明那一侧（切词 / 参数反推 / 清单）；
  · `test_custom_statement_entry.py` —— 弹窗（教一次的那张表单）；
  · **本文件** —— 声明**之后**：那条语句在卡片上长什么样、能改什么、
    改完导出成什么。**不测 show**（它是全员最熟的那条路，另有判据）。

判据口径（写死在这里，免得日后越改越松）
---------------------------------------
  ① 全部走**真路径**：真 `MainWindowQt` + 真 `rules.json` + 真 `.rpy`，
     不直接 `Beat(...)` 构造成"好看的数据"（那种用例测不到接线）；
  ② 断言用**用户看得见的东西**（列表那一行的字、卡片上的标签文字、
     控件在不在、导出后的那一行），不查内部状态变量；
  ③ **逐字节**：任何"打开 → 保存 → 导出"的用例都必须证明
     *没改的地方一个字节都没动*；
  ④ 每个用例的 docstring 写清"哪一类写法会踩到它"，不写"测试 XX 函数"。

跑法（offscreen 真 Qt，与 `test_ui_qt.py` 同一套）：

    python -m pytest tests/test_hell_custom_card_layout.py -q
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

pytest.importorskip('PySide6')
from PySide6.QtWidgets import QLabel                          # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.story.beat_render import render_beats_to_rpy   # noqa: E402
from storygraph.story.statement_registry import clear_cache    # noqa: E402


# ---------------------------------------------------------------------------
#  工程：一条"声音"语句 + 一条"文字"语句 + 一条"多声部"语句
# ---------------------------------------------------------------------------

#: `sfx_mix` —— **多音轨**：声道可以连写多个（`se se2`）、文件是位置参数、
#: `gain` 是可选具名参数（脚本里写 `gain=-6`）。
#:
#: ⚠ 只写 `pattern` **不写 `columns`** 是**故意的**：手写的清单 / 早期版本
#: 就长这样，而"声明了却一格都不出现"是最气人的一种沉默（见
#: `statement_registry.spec_columns` 里那段 2026-10-18 的说明）。
SFX_MIX = {
    'name': 'sfx_mix', 'label': '多轨音效',
    'pattern': [
        {'kind': 'keyword', 'text': 'sfx_mix', 'column': '动作名'},
        {'kind': 'choice', 'column': '声道', 'choices': ['se', 'se2', 'amb'],
         'multi': True},
        {'kind': 'variable', 'column': '文件'},
        {'kind': 'optional', 'text': 'gain', 'column': '音量'},
    ],
}

#: `styled` —— **文字**：内容是一个**引号串**（位置参数），后面跟颜色/字号。
#:
#: 这一条踩的是解析层的命门：`styled "红色" "#ff0000" 32` 与
#: `<角色> "台词"` 长得一模一样，`_RE_SAY` 只 match 到引号 → 后面的
#: `"#ff0000" 32` 被**静默吃掉**（2026-10-18 修：现在引号后还有东西就不是台词）。
STYLED = {
    'name': 'styled', 'label': '花字',
    'pattern': [
        {'kind': 'keyword', 'text': 'styled', 'column': '动作名'},
        {'kind': 'variable', 'column': '文字'},
        {'kind': 'variable', 'column': '颜色'},
        {'kind': 'variable', 'column': '字号'},
    ],
}

#: `chorus` —— **多声部**：一行里好几个角色一起说。
CHORUS = {
    'name': 'chorus', 'label': '合唱',
    'pattern': [
        {'kind': 'keyword', 'text': 'chorus', 'column': '动作名'},
        {'kind': 'choice', 'column': '人物', 'choices': ['lan', 'ling', 'ye'],
         'multi': True},
        {'kind': 'variable', 'column': '内容'},
    ],
}

RULES = {'statements': [SFX_MIX, STYLED, CHORUS]}

SCRIPT = '''\
label ch:
    "开场"
    play_music track=bgm file="bgm/theme.ogg" fade=1.5
    sfx_mix se se2 "se/a.ogg" gain=-6
    styled "他说\\"你好\\"" "#ff0000" 32
    chorus lan ling "一起说"
    jc "带样式的台词" (no_skip=True)
    play music "bgm/a.ogg" fadeout 1.0
    play ambience "amb/wind.ogg"
    play sound "se/hit.ogg"
    "收尾"
'''

#: 声明在 `play_music` 上（一条"普通"自定义语句：具名参数、无 pattern）
PLAY_MUSIC = {
    'name': 'play_music', 'label': '播放音乐',
    'columns': [
        {'name': 'track', 'label': '音轨', 'choices': ['bgm', 'bgm2', 'voice']},
        {'name': 'file', 'label': '文件'},
        {'name': 'fade', 'label': '淡入秒数'},
    ],
}

ALL_RULES = {'statements': [PLAY_MUSIC, SFX_MIX, STYLED, CHORUS]}


@pytest.fixture(autouse=True)
def _clean_registry_cache():
    """注册表带全局缓存：每个用例前后都清，免得读到上一条用例装配的那份。"""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture(scope='session')
def app():
    from PySide6.QtWidgets import QApplication
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.rules.json').write_text(
        json.dumps(ALL_RULES, ensure_ascii=False, indent=2), encoding='utf-8')
    (root / 'game' / 'ch.rpy').write_text(SCRIPT, encoding='utf-8')
    return root


@pytest.fixture
def window(app, project):
    from storygraph.ui_qt.main_window import MainWindowQt

    class _Engine:
        project_root = None

        def __init__(self, root):
            self.project_root = str(root)

        def get_registry(self):
            return None

    win = MainWindowQt(_Engine(project), project / 'game' / 'ch.rpy')
    yield win
    win.close()


# ---------------------------------------------------------------------------
#  工具
# ---------------------------------------------------------------------------

def _idx_of(window, needle: str) -> int:
    """找出**头行里含 needle** 的那一拍（真拍，不猜）。"""
    for i, b in enumerate(window.beats):
        head = (getattr(b, 'spec_head', '') or getattr(b, 'camera_header', '')
                or getattr(b, 'loop_header', '') or '')
        if needle in head:
            return i
    raise AssertionError(f'没有头行含 {needle!r} 的拍；'
                         f'现有头行：{[getattr(b, "spec_head", "") for b in window.beats]}')


def _show(window, idx: int):
    """切到第 idx 张卡片（走窗口那条真路：载入字段 + 同步参数组）。"""
    window._set_page(window.PAGE_CARD)
    window._show_index(idx)


def _export(window) -> str:
    return render_beats_to_rpy(window.beats, label_name='ch')


def _labels(widget) -> list:
    """控件树里**真的显示出来**的文字（`isVisibleTo` 才认）。"""
    return [w.text() for w in widget.findChildren(QLabel)
            if w.isVisibleTo(widget) and w.text()]


def _panel(window):
    return window._panel


def _set_cell(panel, header: str, value: str):
    """把参数格里那一格改成 value（下拉格与输入框都能用）。"""
    from PySide6.QtWidgets import QComboBox
    w = panel._spec_widgets[header]
    if isinstance(w, QComboBox):
        w.setCurrentText(value)
    else:
        w.setText(value)


# ---------------------------------------------------------------------------
#  一、语句拍在界面上**有名字**
# ---------------------------------------------------------------------------

class TestStatementIdentity:
    """
    一条 `play_music …` 从前在左栏显示成「（无台词）」，与 `sfx_mix …`、
    `styled …` **长得一模一样** —— 人得一张张点进去才知道哪条是哪条。
    """

    def test_the_list_row_names_the_statement(self, window):
        """声音 / 文字 / 多声部三条语句，左栏那一行必须各说各的名字。"""
        i = _idx_of(window, 'sfx_mix')
        row = window._list.item(i).text()
        assert '◆' in row and 'sfx_mix' in row, row
        # 它不是台词拍，不该再被写成"（无台词）"
        assert '（无台词）' not in row, row

    def test_the_list_row_carries_the_human_label(self, window):
        """清单里给它起过人话名字（`label`）→ 列表上就用那个名字。"""
        i = _idx_of(window, 'styled')
        row = window._list.item(i).text()
        assert '花字' in row, row

    def test_the_card_says_which_statement_this_is(self, window):
        """卡片顶部要有一条"这一拍是什么"：语句名 + 中文标签 + 原文。"""
        _show(window, _idx_of(window, 'sfx_mix'))
        panel = _panel(window)
        assert panel._stmt_bar.isVisibleTo(panel), '语句条没显示'
        text = ' '.join(_labels(panel._stmt_bar))
        assert 'sfx_mix' in text, text
        assert '多轨音效' in text, text
        assert 'sfx_mix se se2' in text, f'原文没显示出来：{text}'

    def test_a_pure_dialogue_beat_has_no_statement_bar(self, window):
        """台词拍不该挂一条语句条（那会让"这一拍"变得含糊）。"""
        _show(window, 0)
        panel = _panel(window)
        assert not panel._stmt_bar.isVisibleTo(panel), '台词拍上出现了语句条'


# ---------------------------------------------------------------------------
#  二、排版：按"这一拍是什么"收放
# ---------------------------------------------------------------------------

class TestCardLayout:
    """
    用户点名的是**排版**：语句拍的正文是那几格参数，台词是可选附加物；
    而一条跟图片无关的语句，不该被一张空立绘网格顶到看不见。
    """

    def test_parameters_sit_above_the_dialogue_box(self, window):
        """语句拍的参数组要排在台词框**前面**（它才是正文）。"""
        _show(window, _idx_of(window, 'play_music'))
        panel = _panel(window)
        layout = panel.layout()
        assert layout.indexOf(panel._spec_host) < layout.indexOf(panel._text), \
            '参数组还在台词框下面 —— 语句拍要滚一整屏才看得到自己能改什么'

    def test_a_beat_without_stage_content_collapses_the_stage_block(self, window):
        """一条 `styled …`（纯文字）没有什么舞台内容 → 立绘那块要收起来。"""
        _show(window, _idx_of(window, 'styled'))
        panel = _panel(window)
        assert not panel._stage_box.isVisibleTo(panel), \
            '空立绘网格还占着一整屏'
        assert panel._stage_empty.isVisibleTo(panel), '收起来了却没说为什么'
        assert '没有改动' in panel._stage_empty.text(), panel._stage_empty.text()

    def test_the_stage_block_can_be_opened_again(self, window):
        """收起来 ≠ 藏死：想给这一拍加张立绘，得点得开。"""
        _show(window, _idx_of(window, 'styled'))
        panel = _panel(window)
        assert panel._stage_toggle.isVisibleTo(panel), '没有"改舞台"的入口'
        panel._stage_toggle.click()
        assert panel._stage_box.isVisibleTo(panel), '点了却打不开'
        assert panel._grid.isVisibleTo(panel), '立绘网格没跟着出来'

    def test_a_beat_with_stage_content_still_shows_the_stage_block(self, window):
        """有舞台内容的拍**一个字都不许收**：那是它本来就要编辑的东西。"""
        _show(window, len(window.beats) - 1)      # 收尾那拍：音乐 + 音效 + 台词
        panel = _panel(window)
        assert panel._stage_box.isVisibleTo(panel), '有舞台内容的拍被收起来了'
        assert not panel._stage_empty.isVisibleTo(panel)

    def test_unmodeled_lines_are_named_on_the_card(self, window):
        """
        `play ambience "…"`（项目自定义声道）工具没建模，它躺在折叠着的
        「代码」格里 —— 卡片上必须**说得出**这一拍还会执行它。

        否则人看到的是"这一拍什么都没有"，而产物里确实有一行。
        """
        _show(window, len(window.beats) - 1)
        panel = _panel(window)
        assert panel._loose_label.isVisibleTo(panel), '没提那些原样行'
        text = panel._loose_label.text()
        assert 'play ambience' in text, text
        assert '高级区' in text, f'没说去哪里改：{text}'


# ---------------------------------------------------------------------------
#  三、声音类：多音轨
# ---------------------------------------------------------------------------

class TestAudioStatements:

    def test_audio_values_land_in_their_own_cells(self, window):
        """`play music` 与 `play sound` 的参数各回各格（不许串到一起）。"""
        _show(window, len(window.beats) - 1)
        panel = _panel(window)
        assert panel._get_value('BGM') == '[音乐] bgm/a.ogg fadeout 1.0', \
            panel._get_value('BGM')
        assert panel._get_value('SFX') == '[音效] se/hit.ogg', \
            panel._get_value('SFX')

    def test_three_channels_keep_their_source_order(self, window):
        """
        **多音轨的顺序**：`play music / play ambience / play sound` 中间夹着
        一条未建模声道 —— 顺序被改就是改剧情（谁先响、谁后停）。
        """
        out = _export(window)
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        i_music = lines.index('play music "bgm/a.ogg" fadeout 1.0')
        i_amb = lines.index('play ambience "amb/wind.ogg"')
        i_sfx = lines.index('play sound "se/hit.ogg"')
        assert i_music < i_amb < i_sfx, lines

    def test_the_parameter_cells_belong_to_the_right_words(self, window):
        """`sfx_mix se se2 "se/a.ogg" gain=-6`：三格各拿各的，一格都不许串。"""
        _show(window, _idx_of(window, 'sfx_mix'))
        panel = _panel(window)
        got = {h: panel._get_value(h) for h in panel._spec_widgets}
        assert got.get('sfx_mix·声道@1') == 'se se2', got
        assert got.get('sfx_mix·文件@2') == 'se/a.ogg', got
        assert got.get('sfx_mix·gain') == '-6', got

    def test_editing_one_cell_rewrites_only_that_token(self, window):
        """改「音量」→ 头行只换 `gain=-6` 那一段，别的一个字节不动。"""
        idx = _idx_of(window, 'sfx_mix')
        _show(window, idx)
        panel = _panel(window)
        _set_cell(panel, 'sfx_mix·gain', '-3')
        window.save_current_beat()
        head = window.beats[idx].spec_head
        assert head == 'sfx_mix se se2 "se/a.ogg" gain=-3', head


# ---------------------------------------------------------------------------
#  四、文字类：花字（引号串 + 颜色 + 字号）
# ---------------------------------------------------------------------------

class TestTextStatements:

    def test_a_quoted_first_argument_is_not_dialogue(self, window):
        """
        `styled "他说…" "#ff0000" 32` 与 `<角色> "台词"` 长得一样，
        但它**不是台词**：认成台词就会把颜色和字号直接删掉（一句话都还在，
        只是样式没了 —— 谁也不会立刻发现）。
        """
        i = _idx_of(window, 'styled')
        beat = window.beats[i]
        assert beat.spec_name == 'styled', beat.spec_name
        assert beat.speaker in ('', 'styled') or not beat.speaker, beat.speaker
        # 它不该被算成"这句台词是谁说的"
        assert '他说' in (beat.spec_head or ''), beat.spec_head

    def test_the_text_statement_survives_export(self, window):
        """整行**逐字**出去（颜色 / 字号 / 引号里的转义一个都不许动）。"""
        out = _export(window)
        want = 'styled "他说\\"你好\\"" "#ff0000" 32'
        assert want in out, out

    def test_the_content_and_style_are_editable_cells(self, window):
        """文字 / 颜色 / 字号 三格都要有值可改。"""
        _show(window, _idx_of(window, 'styled'))
        panel = _panel(window)
        got = {h: panel._get_value(h) for h in panel._spec_widgets}
        assert got.get('styled·文字@1') == '他说"你好"', got
        assert got.get('styled·颜色@2') == '#ff0000', got
        assert got.get('styled·字号@3') == '32', got

    def test_a_color_value_gets_a_swatch(self, window):
        """
        `#ff0000` 对编剧只是一串乱码 —— 颜色那一格旁边要有个**色块**，
        而且跟着输入实时变（改完立刻看得出是什么颜色）。
        """
        _show(window, _idx_of(window, 'styled'))
        panel = _panel(window)
        sw = panel._spec_swatches.get('styled·颜色@2')
        assert sw is not None, '颜色格没有色块'
        assert '#ff0000' in sw.styleSheet(), sw.styleSheet()
        _set_cell(panel, 'styled·颜色@2', '#00ff88')
        assert '#00ff88' in sw.styleSheet(), '改了颜色色块没跟着变'

    def test_editing_the_color_rewrites_only_that_token(self, window):
        idx = _idx_of(window, 'styled')
        _show(window, idx)
        panel = _panel(window)
        _set_cell(panel, 'styled·颜色@2', '#00ff88')
        window.save_current_beat()
        head = window.beats[idx].spec_head
        assert head == 'styled "他说\\"你好\\"" "#00ff88" 32', head


# ---------------------------------------------------------------------------
#  五、多声部：一格能装好几个词
# ---------------------------------------------------------------------------

class TestMultiValueSlots:

    def test_a_multi_slot_shows_all_its_words(self, window):
        """`chorus lan ling "一起说"` → 「人物」那格是 `lan ling`（不是只有 lan）。"""
        _show(window, _idx_of(window, 'chorus'))
        panel = _panel(window)
        assert panel._get_value('chorus·人物@1') == 'lan ling', \
            {h: panel._get_value(h) for h in panel._spec_widgets}

    def test_the_multi_slot_is_labelled_on_the_card(self, window):
        """这一格能连写多个 → 卡片上要**说出来**，否则人只会填一个。"""
        _show(window, _idx_of(window, 'chorus'))
        panel = _panel(window)
        texts = ' '.join(_labels(panel._spec_host))
        assert '多个' in texts, texts

    def test_removing_one_word_rewrites_the_slot(self, window):
        """`lan ling` 改成 `lan` → 行里那一整段被换掉，不许留个 `ling` 在中间。"""
        idx = _idx_of(window, 'chorus')
        _show(window, idx)
        panel = _panel(window)
        _set_cell(panel, 'chorus·人物@1', 'lan')
        window.save_current_beat()
        head = window.beats[idx].spec_head
        assert head == 'chorus lan "一起说"', head


# ---------------------------------------------------------------------------
#  六、台词后缀：给文字上色那一类"变态逻辑"
# ---------------------------------------------------------------------------

class TestSayTail:
    """
    `jc "台词" (no_skip=True)` —— 引号后面的括号参数就是**这句台词的样式/控制**。
    真机 `script_pomo.rpy` 有一处；从前导出时括号**无声消失**。
    """

    def test_the_style_clause_is_visible_on_the_card(self, window):
        """带后缀的台词要在卡片上说得出来（否则它只活在折叠区里）。"""
        i = next(i for i, b in enumerate(window.beats)
                 if b.text == '带样式的台词')
        _show(window, i)
        panel = _panel(window)
        assert panel._say_tail_label.isVisibleTo(panel), '没说这句台词带后缀'
        assert 'no_skip' in panel._say_tail_label.text(), \
            panel._say_tail_label.text()

    def test_a_style_clause_survives_export(self, window):
        out = _export(window)
        assert 'jc "带样式的台词" (no_skip=True)' in out, out


# ---------------------------------------------------------------------------
#  七、保存纪律：不许静默丢、不许顺手改
# ---------------------------------------------------------------------------

class TestSaveDiscipline:

    def test_saving_an_untouched_statement_beat_changes_nothing(self, window):
        """打开每条语句拍、按保存 → 产物**逐字节**不变（一个词都不许动）。"""
        before = _export(window)
        for i in range(len(window.beats)):
            _show(window, i)
            window.save_current_beat()
        assert _export(window) == before

    def test_typing_dialogue_keeps_the_statement(self, window):
        """
        语句拍上写一句台词 → **两样东西都在**（语句 + 台词），顺序也对。
        语句被台词顶掉是最坏的一种"保存"。
        """
        idx = _idx_of(window, 'play_music')
        _show(window, idx)
        panel = _panel(window)
        panel.set_text('顺便说一句')
        window.save_current_beat()
        lines = [ln.strip() for ln in _export(window).splitlines()]
        i_stmt = lines.index('play_music track=bgm file="bgm/theme.ogg" fade=1.5')
        i_say = lines.index('"顺便说一句"')
        assert i_stmt < i_say, lines

    def test_a_speaker_without_a_line_is_reported_not_swallowed(self, window):
        """
        语句拍上填了说话人、却没有台词 → 说话人**不会**写进脚本。
        这一格在卡片上确实存在，所以必须当场说清楚（不许"设了却没生效"）。
        """
        idx = _idx_of(window, 'styled')
        _show(window, idx)
        panel = _panel(window)
        panel.set_speaker('lan')
        window.save_current_beat()
        window.refresh_derived_panels(idx)
        note = panel._note_label.text()
        assert panel._note_label.isVisibleTo(panel), '警告条没出现'
        assert '说话人' in note and '没有台词' in note, note

    def test_a_parameter_edit_reaches_the_product(self, window):
        """改「音轨」→ 导出那一行真的变了（不是只改了界面）。"""
        idx = _idx_of(window, 'play_music')
        _show(window, idx)
        panel = _panel(window)
        _set_cell(panel, 'play_music·track', 'bgm2')
        window.save_current_beat()
        out = _export(window)
        assert 'play_music track=bgm2' in out, out
