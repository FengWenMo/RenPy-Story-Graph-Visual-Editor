"""
tests/test_menu_brief.py
========================
控制流条上**有名 menu 的选项清单**（用户 2026-10-25）：

> 它哪里写的是：menu……可是它没有显示 menu 的内容是什么……
> 又跳去哪儿……（不然要我点进去才知道）

`menu` 名在 Ren'Py 里是真 label（`jump choice_a` 合法），但控制流条从前
只写得出"跳转 choice_a"，人不知道这个分支有几条、各自去哪。
`story.flow.menu_brief` 把这个分支的选项与去向算出来，`menu_brief_text`
排成控制流条上那几行；`window_card._flow_target_brief` 接线把它们摆上去。

判据分两层：
  · 纯函数（`menu_brief` / `menu_brief_text`）—— 数得对、**不串台**、认不出就空；
  · 控制流条（`window_card._flow_target_brief` → `set_flow`）—— 条上真写出来。

拍从**真解析器**折出来（`IncrementalRenPyAnalyzer` → `beats_from_statements`），
不是手搓桩 —— `nest_path` 是这套判据的命根子，手搓极易与解析器约定漂移。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from storygraph.story.flow import menu_brief, menu_brief_text   # noqa: E402


def _beats_from(script: str, tmp_path, name: str = 'menu', crlf: bool = False):
    """把一段源码写成 .rpy → 真解析 → 折成拍（真实 `nest_path` / `kind`）。

    用 `write_bytes` 写**精确字节**（不做换行翻译）—— CRLF 样本才保得住
    `\\r\\n`（`Path.write_text` 默认会把 `\\n` 翻成 `os.linesep`，样本就脏了）。
    """
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_statements

    fp = tmp_path / f'{name}.rpy'
    data = script.replace('\n', '\r\n') if crlf else script
    fp.write_bytes(data.encode('utf-8'))
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
    labs = getattr(pr, 'statement_labels', None)
    labels = ([''] * len(pr.statements) if labs is None
              else [str(x or '') for x in labs])
    return beats_from_statements(list(pr.statements), label='', labels=labels)


def _opts(brief) -> list:
    """`[(text, type, target), ...]` —— 断言里读起来比一串 dict 干净。"""
    return [(o.get('text'), o.get('type'), o.get('target'))
            for o in (brief or {}).get('options') or ()]


class _MockBeat:
    """最小拍替身（只带 menu_brief 读的字段）—— 造真解析器产不出的孤儿行。"""

    def __init__(self, kind='', nest='', menu_name='', text='', raw='',
                 label='main'):
        self.kind = kind
        self.nest_path = nest
        self.menu_name = menu_name
        self.text = text
        self.raw = raw
        self.label = label


# ==================================================================
#  一、纯函数：数得对
# ==================================================================

class TestMenuBrief:
    def test_three_options_with_jump_call_and_stay(self, tmp_path):
        beats = _beats_from('''label main:
    menu m:
        "跳走":
            jump away
        "调用":
            call helper
        "留下":
            e "就地来一句"
    jump done

label done:
    return
''', tmp_path)
        brief = menu_brief(beats, 'm')
        assert brief['name'] == 'm'
        assert brief['count'] == 3
        assert _opts(brief) == [
            ('跳走', 'jump', 'away'),
            ('调用', 'call', 'helper'),
            ('留下', '', ''),          # 选项体只有台词，没有跳转 → 就地继续
        ]

    def test_conditional_option_is_listed_with_its_jump(self, tmp_path):
        beats = _beats_from('''label main:
    menu m:
        "普通":
            jump a
        "有条件" if flag:
            call helper
        "默认":
    jump done

label done:
    return
''', tmp_path)
        brief = menu_brief(beats, 'm')
        assert brief['count'] == 3
        # 条件（`if flag`）不改"这是一个选项"的事实，选项照列、去向照认
        assert _opts(brief) == [
            ('普通', 'jump', 'a'),
            ('有条件', 'call', 'helper'),
            ('默认', '', ''),
        ]

    def test_call_screen_is_not_a_flow_jump(self, tmp_path):
        # `call screen` 是界面调用，不改流程 → 该选项算"就地继续"
        beats = _beats_from('''label main:
    menu m:
        "开界面":
            call screen confirm
        "真调用":
            call helper
        "真跳转":
            jump away
    jump done

label done:
    return
''', tmp_path)
        brief = menu_brief(beats, 'm')
        assert _opts(brief) == [
            ('开界面', '', ''),        # call screen 不算跳转
            ('真调用', 'call', 'helper'),
            ('真跳转', 'jump', 'away'),
        ]

    def test_screen_prefixed_labels_are_not_over_excluded(self, tmp_path):
        """`call screen` 不算跳转，但**名字以 screen 开头的真 label 不许连坐**。

        修前用 `rest.startswith('screen')` 排除 —— 于是 `jump screen_map` /
        `call screenplay` 这些**真 label**也被误杀（审计 §1.2）。
        只有紧跟的关键字 `screen`（`call screen X`）才是界面调用。
        """
        beats = _beats_from('''label main:
    menu m:
        "看日志":
            call screen confirm
        "去地图":
            jump screen_map
        "剧本组":
            call screenplay
        "正经跳":
            jump ch02
    jump done

label done:
    return
''', tmp_path)
        brief = menu_brief(beats, 'm')
        assert _opts(brief) == [
            ('看日志', '', ''),        # call screen → 不算
            ('去地图', 'jump', 'screen_map'),   # 真 label，不许误杀
            ('剧本组', 'call', 'screenplay'),   # 真 label，不许误杀
            ('正经跳', 'jump', 'ch02'),
        ]

    def test_call_target_is_just_the_label_not_args_or_from(self, tmp_path):
        """`call ch02(x, y) from _call_ch02_1` → target 只有 `ch02`。

        修前把 `(x, y) from _call_ch02_1` 整段拼进 target（审计 §1.3）——
        与 `parse_flow_line` 的 target/args/from 三段口径不一致（两套出口
        两种待遇）。这里复用同一套解析，target 就该只有 label 名。
        """
        beats = _beats_from('''label main:
    menu m:
        "求助":
            call ch02(x, y) from _call_ch02_1
        "直跳":
            jump ch03
    jump done

label done:
    return
''', tmp_path)
        brief = menu_brief(beats, 'm')
        assert _opts(brief) == [
            ('求助', 'call', 'ch02'),    # 不是 'ch02(x, y) from _call_ch02_1'
            ('直跳', 'jump', 'ch03'),
        ]

    def test_nested_menu_does_not_leak_into_outer_option(self, tmp_path):
        """嵌套 menu 不串：外层选项不吞内层 menu 的跳转，内层只报自己的。"""
        beats = _beats_from('''label main:
    menu choice_a:
        "选项A":
            menu inner:
                "内1":
                    jump deep1
                "内2":
                    jump deep2
        "选项B":
            jump elsewhere
        "选项C" if True:
            call screen confirm
    jump done

label done:
    return
''', tmp_path)
        # 外层：选项A 的体里只有嵌套 menu，**没有**直接跳转 → 就地继续。
        # （修前这里错报 "跳转 deep1" —— 把内层 menu 的跳转吞成了外层的）
        outer = menu_brief(beats, 'choice_a')
        assert _opts(outer) == [
            ('选项A', '', ''),         # 不是 deep1！
            ('选项B', 'jump', 'elsewhere'),
            ('选项C', '', ''),         # call screen 不算
        ]
        # 内层：只报自己的两个选项（修前把外层的选项B/C 当成了 inner 的）
        inner = menu_brief(beats, 'inner')
        assert _opts(inner) == [
            ('内1', 'jump', 'deep1'),
            ('内2', 'jump', 'deep2'),
        ]

    def test_empty_option_body_pairs_by_position(self, tmp_path):
        # 空选项体：跳转归给**位置对得上**的那个选项，不能错位
        beats = _beats_from('''label main:
    menu m:
        "空的":
        "有内容":
            jump x
        "也空":
    jump done

label done:
    return
''', tmp_path)
        brief = menu_brief(beats, 'm')
        assert _opts(brief) == [
            ('空的', '', ''),
            ('有内容', 'jump', 'x'),
            ('也空', '', ''),
        ]

    def test_dotted_local_menu_name_is_found(self, tmp_path):
        # 点号菜单名（`.branch` = 本 label 内的局部菜单）按全名精确匹配
        beats = _beats_from('''label main:
    menu .branch:
        "去A":
            jump a
        "去B" if ok:
            call helper
    jump done

label done:
    return
''', tmp_path)
        brief = menu_brief(beats, '.branch')
        assert brief['name'] == '.branch'
        assert _opts(brief) == [
            ('去A', 'jump', 'a'),
            ('去B', 'call', 'helper'),
        ]
        # 只差一个点就不是它 —— 精确匹配，不猜
        assert menu_brief(beats, 'branch') == {}

    def test_dotted_menu_name_is_found(self, tmp_path):
        """点号菜单名 `main.route`（M1，2026-10-26）—— 从前解析器不认这种
        名字、整块掉原样块，`menu_brief` 永远查不到它。按全名精确匹配。"""
        beats = _beats_from('''label main:
    menu main.route:
        "去A":
            jump a
    jump done

label done:
    return
''', tmp_path)
        brief = menu_brief(beats, 'main.route')
        assert brief['name'] == 'main.route'
        assert _opts(brief) == [('去A', 'jump', 'a')]
        # 只匹配全名：`main` 是另一个 label，不是这个菜单
        assert menu_brief(beats, 'main') == {}

    def test_unknown_or_empty_name_is_empty(self, tmp_path):
        beats = _beats_from('''label main:
    menu m:
        "去A":
            jump a
    jump done

label done:
    return
''', tmp_path)
        assert menu_brief(beats, 'nowhere') == {}
        assert menu_brief(beats, '') == {}
        assert menu_brief([], 'm') == {}

    def test_crlf_script_parses_the_same(self, tmp_path):
        # CRLF（Windows 回车）不许把选项 / 去向弄丢或弄错位
        beats = _beats_from('''label main:
    menu m:
        "去A":
            jump a
        "去B":
            jump b
    jump done

label done:
    return
''', tmp_path, crlf=True)
        brief = menu_brief(beats, 'm')
        assert brief['count'] == 2
        assert _opts(brief) == [
            ('去A', 'jump', 'a'),
            ('去B', 'jump', 'b'),
        ]

    def test_orphan_option_from_another_menu_is_not_claimed(self):
        """无选项的 menu 头 + 紧跟一个**别家块号**的孤儿选项 → 不许认领。

        S1 删拍 / 表编辑后孤儿行是真实存在（审计 §2.3）：`victim` 自己没选项，
        紧跟的那张 `menu_option` 的块号是 `menu:2`（别的 menu 的），不能被
        `victim`（第 1 个 menu）吞成自己的选项。用 N 反查块号后，块号是
        `menu:1`，`menu:2` 的孤儿就进不来。
        """
        rows = [
            _MockBeat(kind='menu', nest='', menu_name='victim'),
            _MockBeat(kind='menu_option', nest='menu:2', text='孤儿选项'),
            _MockBeat(kind='flow', nest='menu:2>A', raw='jump steal'),
        ]
        assert menu_brief(rows, 'victim') == {
            'name': 'victim', 'count': 0, 'options': []}


# ==================================================================
#  二、纯函数：排成文案
# ==================================================================

class TestMenuBriefText:
    def test_empty_brief_is_empty_string(self):
        assert menu_brief_text({}) == ''
        assert menu_brief_text(None) == ''
        assert menu_brief_text({'name': 'x', 'count': 0, 'options': []}) == ''

    def test_lines_name_each_option_and_where_it_goes(self):
        text = menu_brief_text({
            'name': 'm', 'count': 3, 'options': [
                {'text': '跳走', 'type': 'jump', 'target': 'away'},
                {'text': '调用', 'type': 'call', 'target': 'helper'},
                {'text': '留下', 'type': '', 'target': ''},
            ]})
        assert '3 个选项' in text
        assert '跳转 away' in text
        assert '调用 helper' in text
        assert '就地继续' in text


# ==================================================================
#  三、控制流条上真的写出来（接线）
# ==================================================================

@pytest.fixture(scope='session')
def app():
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


MENU_FLOW = '''label start:
    e "开始"
    jump choice_a

label other:
    menu choice_a:
        "去河边":
            jump river
        "回家":
            call home
        "留下":
            e "哪也不去"
    jump after

label river:
    e "河边"
    return

label home:
    e "家"
    return

label after:
    e "之后"
    return
'''


@pytest.fixture
def project(tmp_path):
    from _ui_project import build_project
    root = build_project(tmp_path / 'proj')
    (root / 'game' / 'script' / 'flow.rpy').write_text(
        MENU_FLOW, encoding='utf-8')
    return root


@pytest.fixture
def window(app, project):
    from storygraph.ui_qt.main_window import MainWindowQt

    class _Engine:
        project_root = project
        _registry = None

        def get_registry(self):
            return None

    win = MainWindowQt(_Engine(), project / 'game' / 'script' / 'flow.rpy')
    win.refresh_chapter_nav()
    yield win
    win.close()


class TestFlowBarMenuBrief:
    def test_menu_target_writes_options_on_the_strip(self, window):
        window._set_page(window.PAGE_CARD)
        idx = next(i for i, b in enumerate(window._session.beats)
                   if getattr(b, 'raw', '') == 'jump choice_a')
        window._show_index(idx)
        text = window._panel._flow_detail.text()
        # 这是分支（menu choice_a），3 个选项 + 每条去哪
        assert '这是分支' in text, text
        assert '去河边' in text and '跳转 river' in text, text
        assert '回家' in text and '调用 home' in text, text
        assert '留下' in text and '就地继续' in text, text

    def test_plain_label_target_shows_no_menu_block(self, window):
        # 目标是普通 label（不是 menu）→ 不该冒出"这是分支"那段
        brief = window._flow_target_brief('river')
        assert 'menu_text' not in brief, brief

    def test_menu_brief_is_attached_for_a_menu_target(self, window):
        brief = window._flow_target_brief('choice_a')
        assert brief.get('menu_text'), brief
        assert '去河边' in brief['menu_text'], brief
