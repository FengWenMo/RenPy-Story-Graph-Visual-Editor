"""
tests/test_asset_report.py
==========================
**资源体检报告**（用户 2026-10-05）：

> 在「变量分析」右边加一个检查资源完整性的功能，后出示检查报告

`format_asset_health` 是纯函数（dict → 文本），这里把它的判据钉住；
真正的数据由 `EditorSession.asset_health_report()` 出（另有端到端用例）。
"""

from __future__ import annotations

from storygraph.app.asset_report import format_asset_health


def _report(**kw):
    base = {'ok': True, 'counts': {'images': 10, 'music': 2, 'sounds': 1,
                                   'voices': 0, 'transforms': 3,
                                   'characters': 2},
            'total_rows': 13, 'missing': {}, 'unregistered': {},
            'outside_game': {'count': 0, 'by_top': {}},
            'duplicate_ids': [], 'duplicate_paths': [], 'no_path': [],
            'placeholders': [], 'problems': 0,
            # `asset_health_report()` 一定会带这两个键（M2）：
            # `cds=None` 表示"没扫过"，与"扫过、没有"是两回事 —— 报告
            # 只在**扫过**时才出第 ⑧ 段（不装作知道）。
            'cds': [], 'cds_error': ''}
    base.update(kw)
    return base


class TestTheVerdictComesFirst:
    def test_a_clean_table_says_one_to_one(self):
        text = format_asset_health(_report())
        assert text.splitlines()[0].startswith('✅')
        assert '一一对应' in text.splitlines()[0]

    def test_problems_are_counted_in_the_first_line(self):
        text = format_asset_health(_report(
            problems=3, missing={'images': ['game/images/a.png'] * 3}))
        assert text.splitlines()[0].startswith('⚠')
        assert '3 处' in text.splitlines()[0]

    def test_a_failed_check_explains_itself(self):
        text = format_asset_health({'ok': False, 'error': '还没有打开项目'})
        assert '没做成' in text and '还没有打开项目' in text


class TestEverySectionIsPresent:
    def test_all_sections_show_up_even_when_empty(self):
        text = format_asset_health(_report())
        for mark in ('【表里的条数】', '【① 表里有、磁盘上没有】',
                     '【② 磁盘上有、表里没登记】',
                     '【③ 表里的行指向本项目 `game/` 之外】',
                     '【④ 同一个 ID 出现多次】',
                     '【⑤ 同一个文件登记了多行（一对一被破坏）】',
                     '【⑥ 行里没写 Path】', '【⑦ 人工占位',
                     '【⑧ 项目自定义语句'):
            assert mark in text, f'报告里没有 {mark}'

    def test_cds_section_shows_what_the_project_declared(self):
        """
        「项目声明了哪些自定义语句」必须**看得见**（M2）。

        这一段存在的唯一理由：用户说过「若是程序自定义了一个结构，而编剧
        用这个编辑器编辑时发现：你没有适应到它的自定义结构怎么办？他会立即
        退出，因为工具没有用！」—— 所以"工具愿意被教"这件事必须说出来，
        而不是藏在某个没人调用的模块里。
        """
        text = format_asset_health(_report(cds=[
            {'name': 'mycue', 'aliases': ['mycue'],
             'block': 'yes', 'hooks': ['parse'],
             'file': r'D:\p\game\defs.rpy', 'line': 12},
        ]))
        assert 'mycue' in text
        assert 'defs.rpy:12' in text, '要指到"哪一行声明的"，否则没法去看'
        assert '带块' in text
        # 它**不是**缺陷：不该把 problems 撑大，也不该说成"要处理的地方"
        assert text.splitlines()[0].startswith('✅'), text.splitlines()[0]

    def test_cds_unreadable_is_reported_not_hidden(self):
        text = format_asset_health(_report(cds=[], cds_error='ValueError: 坏'))
        assert '没读成' in text and 'ValueError' in text

    def test_missing_lists_samples_and_truncates(self):
        rows = [f'game/images/gone_{i}.png' for i in range(20)]
        text = format_asset_health(_report(problems=20,
                                           missing={'images': rows}))
        assert 'game/images/gone_0.png' in text
        assert '还有 12 条' in text, '样例条数没有截断'
        assert 'game/images/gone_19.png' not in text

    def test_outside_rows_are_shown_by_top_directory(self):
        text = format_asset_health(_report(problems=5,
                                           outside_game={
                                               'count': 5,
                                               'by_top': {'sr_general': 4,
                                                          'tmp': 1}}))
        assert 'sr_general/ —— 4 行' in text
        assert 'tmp/ —— 1 行' in text
        assert '不会自动删' in text

    def test_placeholders_are_labelled_legal(self):
        text = format_asset_health(_report(
            placeholders=[{'table': 'images', 'ID': 'bg_planned',
                           'Path': 'game/images/bg/planned.png'}]))
        assert '合法' in text and 'bg_planned' in text

    def test_the_notes_tell_how_to_keep_a_placeholder(self):
        text = format_asset_health(_report())
        assert '「占位」' in text
        assert '*.bak.xlsx' in text
