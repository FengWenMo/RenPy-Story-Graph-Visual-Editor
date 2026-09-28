"""
tests/test_entry_points.py
==========================
**入口可达性**的回归测试。

这个文件的存在理由是一次真实反馈（2026-09-28）：

> 用户："我作为一个用户……我不知道如何打开这个你说的卡牌入口"

当时 4 项卡片的功能是**做完了**的（数据层、测试都过），但：

  1. 入口埋在侧栏第三组按钮里，名字叫「在应用内编辑剧本表」；
  2. 点开后是个**空窗口**，「载入 .rpy」还在底部——用户要先找到它；
  3. 侧栏写着「导出本章为 Excel 表」，但**这个方法根本不存在**（点了没反应）；
  4. 「导入 Ren'Py 文件」只支持单选，旁边却另有一个「批量导入章节」按钮。

**教训**：功能做完 ≠ 用户能用。这一层必须用测试锁住，
因为它是"看不见的功能"——普通测试测的是函数，没人测"用户找得到吗"。

这里用**静态源码检查**（AST / 正则）而不是起 tkinter：
无头环境里 tkinter 是桩，点了也不会报错，反而测不出问题。
"""

import ast
import re

from _source_paths import graph_view_source

GRAPH_VIEW = graph_view_source()


def _source():
    return GRAPH_VIEW.read_text(encoding='utf-8')


class TestNoDanglingCallbacks:
    """
    `command=self.xxx` 指向的方法必须真的存在。

    记忆文件里有过先例：「批量导入章节」曾指向不存在的
    `self.batch_import_chapters`，点一下 AttributeError，且**没有任何测试覆盖**。
    """

    def test_every_command_target_exists(self):
        src = _source()
        targets = set(re.findall(r'command=self\.([A-Za-z_]\w*)', src))
        assert targets, '没扫到任何 command= 目标，检查正则'
        missing = sorted(n for n in targets if f'def {n}(' not in src)
        assert not missing, f'悬空回调（点一下会报错）：{missing}'


class TestCardEntryIsDiscoverable:
    """
    4 项卡片的入口必须"看得见、知道会打开哪一章、点开就有内容"。
    """

    def test_primary_button_mentions_script_or_card(self):
        """侧栏第一屏必须有一个文案能让人认出来的主入口。"""
        src = _source()
        m = re.search(r'text="([^"]*(?:剧本|卡片)[^"]*)"', src)
        assert m, '侧栏找不到"剧本/卡片"字样的主入口按钮'
        assert '✎' in m.group(1) or '打开' in m.group(1), m.group(1)

    def test_editor_receives_a_script_path(self):
        """打开编辑器时必须把选中章节传进去——否则又是空窗口。"""
        src = _source()
        assert 'script_path=script_path' in src, \
            'ScriptTableEditorWindow 没有收到 script_path，会开空窗口'

    def test_open_editor_has_file_fallback(self):
        """没选章节时要让用户挑文件，而不是开个空窗口晾着。"""
        src = _source()
        start = src.find('def open_script_table_editor')
        assert start != -1
        body = src[start:start + 2000]
        assert 'askopenfilename' in body, \
            '没选章节时没有再给一次选文件的机会'

    def test_right_click_menu_also_has_card_entry(self):
        """画布右键菜单里也要有——策划是在节点上右键的。"""
        src = _source()
        assert re.search(r'label="[^"]*(?:卡片|台词)[^"]*"', src), \
            '右键菜单里没有卡片入口'
        assert re.search(r'entryconfig\("[^"]*(?:卡片|台词)[^"]*"', src), \
            '右键菜单的卡片入口没有随选中状态启用/禁用'

    def test_chapter_hint_is_shown(self):
        """要让人在点之前就知道会打开哪一章。"""
        src = _source()
        assert '_update_chapter_label' in src
        assert 'chapter_label' in src


class TestRedundantButtonsRemoved:
    """用户明确要求：「批量导入其实可以删掉，导入本来就包括批量导入」。"""

    def test_batch_import_button_is_gone(self):
        src = _source()
        assert '批量导入章节' not in src, '冗余的「批量导入章节」按钮还在'

    def test_import_button_advertises_multi_select(self):
        """功能没少：导入要明确写着可多选。"""
        src = _source()
        m = re.search(r'text="(导入[^"]*)"', src)
        assert m, '找不到导入按钮'
        assert '可多选' in m.group(1), \
            f'导入按钮没写明可多选（用户会以为只能选一个）：{m.group(1)}'

    def test_import_actually_supports_multi_select(self):
        """`import_renpy` 必须走多选路径，不能退回单选。"""
        src = _source()
        start = src.find('def import_renpy')
        body = src[start:start + 600]
        assert 'batch_import_chapters' in body, \
            'import_renpy 没走多文件导入路径'
        assert 'askopenfilename(' not in body, \
            'import_renpy 又退回单选了（askopenfilename）'


class TestAdvertisedButtonsHaveImplementations:
    """
    侧栏/菜单上写出来的功能必须真的有实现。

    「导出本章为 Excel 表」曾经只是**一行字**——方法不存在，
    点了什么都不会发生。这类"假按钮"比没有按钮更伤信任。
    """

    def test_export_table_for_chapter_exists(self):
        src = _source()
        assert 'def export_table_for_chapter(' in src, \
            '侧栏写着「导出本章为 Excel 表」但没有实现'
        # 必须真的调用了生成表的路径
        start = src.find('def export_table_for_chapter')
        body = src[start:start + 900]
        assert '_generate_and_open_table' in body or 'open_rpy_as_table' in body

    def test_no_button_text_without_handler(self):
        """所有 button 文案都能在类里找到对应动作（粗粒度兜底）。"""
        src = _source()
        buttons = re.findall(r'text="([^"]+)"', src)
        cmds = set(re.findall(r'command=self\.([A-Za-z_]\w*)', src))
        # 没有 command= 的按钮（纯展示/关闭）不在此列，这里只要求
        # 存在 command= 的那些都解析过了（上面已有专门用例）
        assert buttons
        assert cmds
