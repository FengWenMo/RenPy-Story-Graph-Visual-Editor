"""
tests/test_show_attr_warnings.py
================================
`show lan base_swimsuit happy` 这种**自定义立绘属性**，导出时会不会提醒？

用户 2026-10-21 的原话：

    show lan base_swimsuit happy 实际上是自定义的写法……
    当我没有规定 rules.json 时，它是否能识别出它是未定义的？？？
    感觉这里会有点问题

先分清两件事（用户混在一起了）：

  · `rules.json`（`storygraph.rules.json`）声明的是**自定义语句**（`dust`、
    `camera` 这类引擎不认识的动词）。立绘属性**不归它管**。
  · 立绘 tag / 属性的"认识不认识"，靠的是 `images.xlsx` 里的
    `Tag` 与 `Attributes` 两列。

实测（见 `tools/probe_custom_show_attrs.py`）：**文字保真没问题** ——
  · 表里「立绘」列存的是完整串 `lan base_swimsuit happy`；
  · 零改动往返逐字节相同；
  · 改一句台词后 show 行原样；
  · 改立绘列（happy→sad）只换那个词，`base_swimsuit` 保住。

真正的缺口在**报告**：体检（`cli validate`）会报 `W111`「属性组合未在资源表
声明」，但**改完直接导出**的人看不到 —— 导出回执里只有门禁那几条。
这个文件守的就是"导出时也必须说"。

判据（四条，都只看 `export_table(...)['warnings']`）：
  1. 组合没声明 → 有一句"属性组合未在资源表声明"；
  2. 组合已声明 → 一声不响（**不许误报**）；
  3. tag 都不在 → 有一句"tag 不在资源表里"（措辞要能把两种情况分开）；
  4. 老表没填 Tag → 一声不响（没有可比信息时报出来全是噪音）。
"""

from pathlib import Path

import pytest


def _project(tmp_path, images_rows, body=None):
    """造一个最小项目：一章 + 一张资源表。返回 `(root, registry)`。"""
    import openpyxl
    from storygraph.pipeline.build import load_asset_registry

    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch01.rpy').write_text(
        body or 'label ch01:\n'
                '    show lan base_swimsuit happy\n'
                '    lan "第一句"\n'
                '    return\n',
        encoding='utf-8')

    d = root / 'game' / 'asset_tables'
    d.mkdir(parents=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes'])
    for row in images_rows:
        ws.append(list(row))
    wb.save(str(d / 'images.xlsx'))
    return root, load_asset_registry(root)


def _warnings(root, registry):
    from storygraph.app import tables
    table = tables.generate_table(root / 'game' / 'ch01.rpy',
                                  project_root=root)['table_path']
    res = tables.export_table(table, project_root=root,
                              source_rpy=root / 'game' / 'ch01.rpy',
                              registry=registry)
    return [str(w) for w in res['warnings']]


def _ours(warnings) -> list:
    """去掉门禁那几条（OUTSIDE_BUILD_DIR 之类），只看"属性提示"。"""
    return [w for w in warnings
            if '属性组合' in w or 'tag 不在资源表' in w]


class TestUndeclaredAttributesAreNamed:
    def test_undeclared_combo_is_reported(self, tmp_path):
        root, reg = _project(tmp_path, [
            ('lan_happy', 'game/i/happy.png', 'lan', 'happy', 'character', ''),
            ('lan_sad', 'game/i/sad.png', 'lan', 'sad', 'character', ''),
        ])
        ours = _ours(_warnings(root, reg))
        assert ours, '属性组合不在资源表里，导出却一句话都没说'
        assert '属性组合未在资源表声明' in ours[0]
        assert 'lan base_swimsuit happy' in ours[0]

    def test_declared_combo_is_silent(self, tmp_path):
        root, reg = _project(tmp_path, [
            ('lan_bs', 'game/i/bs.png', 'lan', 'base_swimsuit happy',
             'character', ''),
        ])
        assert _ours(_warnings(root, reg)) == [], '组合已经登记了，不许误报'

    def test_unknown_tag_says_so_differently(self, tmp_path):
        """tag 都不在表里 —— 措辞必须和"属性组合没声明"分开。"""
        root, reg = _project(tmp_path, [
            ('x_bs', 'game/i/x.png', 'xing', 'base_swimsuit happy',
             'character', ''),
        ])
        ours = _ours(_warnings(root, reg))
        assert ours and 'tag 不在资源表里' in ours[0]
        assert '属性组合未在资源表声明' not in ours[0]

    def test_old_table_without_tag_is_silent(self, tmp_path):
        """老表只填了 Attributes、没填 Tag —— 没有可比信息，不许吵。"""
        root, reg = _project(tmp_path, [
            ('old', 'game/i/old.png', '', '', 'character', ''),
        ])
        assert _ours(_warnings(root, reg)) == []

    def test_no_registry_is_silent(self, tmp_path):
        root, _ = _project(tmp_path, [
            ('lan_bs', 'game/i/bs.png', 'lan', 'base_swimsuit happy',
             'character', ''),
        ])
        assert _ours(_warnings(root, None)) == []
