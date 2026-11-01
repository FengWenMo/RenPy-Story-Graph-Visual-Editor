"""
tests/test_mapping_excel.py
===========================
**资源映射的 Excel 定义途径**（`storygraph.mapping.xlsx`，用户 2026-10-30）。

用户原话：

> 映射表除了 json 文件外，添加 excel 表格的定义途径（至于如何做，先想想……）

所以这里钉的不是"能读 xlsx"这么一句，而是**先想清楚的那三件事**：

  · **谁说了算**：同一目录 `xlsx` 优先于 `json`，且两处都说得出来
    （`mapping_sources` 的顺序、`mapping_cached` 的"还有另一份"警告、
    `status_text` 里的 `（Excel）`）；
  · **表长什么样**：一张表 = 一个顶层键、行序 = 数组顺序、空单元格 = 不写这个键、
    `备注` 列刻意不看、表头可以不在第 1 行、中英文列名都认；
  · **写不写回**：xlsx **只读**（工具从不回写人做的表）；`write_workbook`
    只用于新建（导出初稿 / 模板）。

判据清单：

  读：`我们写的能原样读回` / 人写的表（标题行、英文表名、备注列）/ 空单元格语义 /
      数字 id / 定位警告（表名 + 行号）/ 缺 key 跳过 / 重复 key 覆盖 /
      `能否 show` 认不出 / 规则行序 / 一张认得表都没有 / `.xls` 与坏文件。
  写：表头 / kind 下拉 / 冻结 / 旧文件 `.bak` / 被 Excel 占用时说人话。
  优先级：同目录 Excel 优先 / 项目根 JSON 胜过 `game/` Excel / 两份都在要提醒。
  集成：`draft_mapping` → 写 xlsx → 读回 → 判定结论与 JSON 完全一致。
"""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest

from storygraph.assets.asset_mapping import (
    MAPPING_FILENAME,
    MAPPING_SCHEMA,
    MAPPING_XLSX_FILENAME,
    MappingError,
    file_format,
    find_mapping_path,
    is_workbook,
    load_mapping,
    mapping_cached,
    mapping_sources,
    resolve_with_mapping,
    save_mapping,
    validate_mapping,
)
from storygraph.assets.mapping_excel import (
    SHEET_TITLES,
    blank_template,
    read_workbook,
    write_workbook,
)

GOOD = {
    'schema': MAPPING_SCHEMA,
    'name': '测试 · 资源映射',
    'characters': {'ling': {'display': '凌跃捷', 'aliases': ['凌', 'LTJ'],
                            'avatar': 'game/art/faces/ling.png'}},
    'rules': [
        {'glob': 'game/art/full/<tag>/*.png', 'kind': 'sprite',
         'char': '<tag>', 'attr': '<stem>'},
        {'glob': 'game/images/bg/*.png', 'kind': 'bg', 'char': 'bg',
         'attr': '<stem>'},
    ],
    'images': {'13.2': {'kind': 'cg'},
               'refusing': {'kind': 'preview', 'char': 'ling',
                            'attr': 'refusing', 'showable': False}},
    # 代号两段（2026-10-31）：写表时两张表**一定**建出来（模板要它有表头），
    # 所以读回来这两个键也一定在 —— 往返判据要求 `back == GOOD`，这里得写上。
    'outfit_codes': [{'char': 'ling', 'code': 'B', 'attr': 'base_swimsuit',
                      'label': '泳装'}],
    'expression_codes': [{'char': 'ling', 'no': '7', 'attr': 'indifferent',
                          'label': '不在意'}],
    # 中文叫法（2026-10-31）：同样是"写表时一定建出来的"，读回来键一定在
    'vocabulary': [{'kind': 'transform', 'id': 'i11', 'label': '1人，近处'}],
    'attributes': {'expressions': {'label': '表情'}},
}


def _book(path, sheets):
    """手搓一份工作簿：`{表名: [行, …]}`（`None` 格子就是空单元格）。"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(list(row))
    wb.save(path)
    return path


# ==================================================================
#  写 → 读（往返）
# ==================================================================

class TestRoundTrip:
    def test_我们写的表能原样读回(self, tmp_path):
        """写出去 → 读回来 == 原来那份 dict（**逐键相等**，不是"大致对"）。"""
        p = tmp_path / MAPPING_XLSX_FILENAME
        counts = save_mapping(str(p), GOOD)
        assert counts == {'characters': 1, 'rules': 2, 'images': 2,
                          'outfit_codes': 1, 'expression_codes': 1,
                          'vocabulary': 1, 'attributes': 1}
        back, warnings = load_mapping(str(p))
        assert warnings == [], warnings
        assert back == GOOD, json.dumps(back, ensure_ascii=False, indent=1)

    def test_规则的行序就是数组顺序(self, tmp_path):
        """`rules` 是先中先得 —— Excel 没有"下标"列，顺序只能靠行的上下位置。"""
        p = tmp_path / MAPPING_XLSX_FILENAME
        save_mapping(str(p), GOOD)
        back, _warn = load_mapping(str(p))
        assert [r['glob'] for r in back['rules']] == \
               [r['glob'] for r in GOOD['rules']]

    def test_tag与attrs的别名写法不会丢(self, tmp_path):
        """
        JSON 里 `tag` / `char`、`attrs` / `attr` 是同一个东西
        （`_identity_from_entry` 先看前者、再退回后者）。Excel 只有一列，
        所以转换时必须认别名 —— 否则"导出成功、角色变没了"。
        """
        data = {'schema': MAPPING_SCHEMA,
                'rules': [{'glob': 'game/a/*.png', 'kind': 'sprite',
                           'tag': '<stem>'}],
                'images': {'x': {'kind': 'sprite', 'tag': 'ling',
                                 'attrs': 'happy'}}}
        p = tmp_path / MAPPING_XLSX_FILENAME
        save_mapping(str(p), data)
        back, _warn = load_mapping(str(p))
        assert back['rules'][0]['char'] == '<stem>'
        assert back['images']['x']['char'] == 'ling'
        assert back['images']['x']['attr'] == 'happy'
        # 判定结论必须和原 JSON 一致（换的是写法，不是语义）
        assert resolve_with_mapping(back, relpath='game/a/ling.png').tag == 'ling'

    def test_空白模板七张表都在(self, tmp_path):
        """没有资源表也能导出模板：表头都在，人打开就照着填。"""
        p = tmp_path / 'template.xlsx'
        counts = write_workbook(str(p), blank_template())
        assert counts == {'characters': 0, 'rules': 0, 'images': 0,
                          'outfit_codes': 0, 'expression_codes': 0,
                          'vocabulary': 0, 'attributes': 0}
        wb = openpyxl.load_workbook(str(p), read_only=True)
        try:
            assert SHEET_TITLES['characters'] in wb.sheetnames
            assert SHEET_TITLES['rules'] in wb.sheetnames
            assert SHEET_TITLES['images'] in wb.sheetnames
            # 代号两段：没有它们，模板就填不出 `B7`（用户 2026-10-31）
            assert SHEET_TITLES['outfit_codes'] in wb.sheetnames
            assert SHEET_TITLES['expression_codes'] in wb.sheetnames
            # 词汇表（2026-10-31）：位置 / 音乐 / 背景 / 角色 / 过场的中文名
            assert SHEET_TITLES['vocabulary'] in wb.sheetnames
            assert SHEET_TITLES['attributes'] in wb.sheetnames
        finally:
            wb.close()
        data, warnings = load_mapping(str(p))
        assert data['schema'] == MAPPING_SCHEMA
        assert warnings == [], warnings


# ==================================================================
#  读：人写的表长什么样都得认
# ==================================================================

class TestReadHandMade:
    def test_标题行_英文表名_备注列(self, tmp_path):
        """
        真实场景：人会在上面写一行标题、表名写成 `characters`、加一列备注。
        这三件事**都不许**变成"这张表没用"。
        """
        p = _book(tmp_path / 'hand.xlsx', {
            'characters': [
                ['我的项目角色表'],                       # 标题行（表头不在第 1 行）
                ['角色', '显示名', '别名', '备注'],
                ['ling', '凌跃捷', '凌、LTJ', '主角，随便写'],
            ],
        })
        data, warnings = load_mapping(str(p))
        assert data['characters'] == {'ling': {'display': '凌跃捷',
                                               'aliases': ['凌', 'LTJ']}}
        assert warnings == [], warnings

    def test_空单元格等于不写这个键(self, tmp_path):
        """
        `{"kind": ""}` 和「没写 kind」在判定里是两件事：
        前者是"我明确说没类型"，后者是"让工具自己推断"。空格子必须是后者。
        """
        p = _book(tmp_path / 'hand.xlsx', {
            'rules': [['glob', 'kind', 'char', 'attr', 'showable'],
                      ['game/art/full/<tag>/*.png', 'sprite', '<tag>', None, None]],
        })
        data, warnings = load_mapping(str(p))
        assert warnings == [], warnings
        rule = data['rules'][0]
        assert rule == {'glob': 'game/art/full/<tag>/*.png', 'kind': 'sprite',
                        'char': '<tag>'}

    def test_数字id读成文本(self, tmp_path):
        """Excel 把 `13.2` 存成数字——读回来必须是 `'13.2'`，不是 `13.199999`。"""
        p = _book(tmp_path / 'hand.xlsx', {
            'images': [['资产 ID', 'kind'], [13.2, 'cg'], [7.0, 'ui']],
        })
        data, _warn = load_mapping(str(p))
        assert set(data['images']) == {'13.2', '7'}
        assert data['images']['13.2'] == {'kind': 'cg'}

    def test_我们自己写的_007_不会被吃成_7(self, tmp_path):
        """我们写的时候是把 id 当**文本**写的，所以前导零得活着回来。"""
        data = {'schema': MAPPING_SCHEMA, 'images': {'007': {'kind': 'ui'}}}
        p = tmp_path / MAPPING_XLSX_FILENAME
        save_mapping(str(p), data)
        back, _warn = load_mapping(str(p))
        assert '007' in back['images']

    def test_属性表(self, tmp_path):
        p = _book(tmp_path / 'hand.xlsx', {
            '属性': [['维度', 'label'], ['expressions', '表情']],
        })
        data, warnings = load_mapping(str(p))
        assert data['attributes'] == {'expressions': {'label': '表情'}}
        assert warnings == [], warnings


# ==================================================================
#  读：警告必须能按表名 + 行号直接找到那一行
# ==================================================================

class TestLocatedWarnings:
    def test_错kind的警告带表名和行号(self, tmp_path):
        p = _book(tmp_path / 'hand.xlsx', {
            'rules': [['glob', 'kind'],
                      ['game/a/*.png', 'sprite'],
                      ['game/b/*.png', 'sprit']],       # 第 3 行拼错
        })
        _data, warnings = load_mapping(str(p))
        assert any('「规则」表第 3 行' in w and 'sprit' in w for w in warnings), \
            warnings

    def test_缺key缺glob的行跳过并警告(self, tmp_path):
        p = _book(tmp_path / 'hand.xlsx', {
            'rules': [['glob', 'kind', 'char'],
                      [None, 'sprite', 'ling'],          # 第 2 行没 glob
                      ['game/a/*.png', 'sprite', 'ling']],
            'images': [['key', 'kind'],
                       [None, 'cg'],                     # 第 2 行没 key
                       ['x', 'cg']],
        })
        data, warnings = load_mapping(str(p))
        assert len(data['rules']) == 1
        assert set(data['images']) == {'x'}
        assert any('「规则」表第 2 行' in w for w in warnings), warnings
        assert any('「图片」表第 2 行' in w for w in warnings), warnings

    def test_重复key后者覆盖并警告(self, tmp_path):
        p = _book(tmp_path / 'hand.xlsx', {
            'images': [['key', 'kind', 'char'],
                       ['x', 'preview', 'ling'],
                       ['x', 'sprite', 'qing']],
        })
        data, warnings = load_mapping(str(p))
        assert data['images']['x']['kind'] == 'sprite'
        assert any('重复' in w and '第 3 行' in w for w in warnings), warnings

    def test_能否show认不出当没写(self, tmp_path):
        p = _book(tmp_path / 'hand.xlsx', {
            'images': [['key', 'kind', 'showable'],
                       ['x', 'bg', '？']],
        })
        data, warnings = load_mapping(str(p))
        assert 'showable' not in data['images']['x']
        assert any('能否 show' in w for w in warnings), warnings

    def test_能否show认得出(self, tmp_path):
        p = _book(tmp_path / 'hand.xlsx', {
            'images': [['key', 'kind', '能否 show'],
                       ['a', 'bg', '不能'],
                       ['b', 'bg', '能']],
        })
        data, _warn = load_mapping(str(p))
        assert data['images']['a']['showable'] is False
        assert data['images']['b']['showable'] is True

    def test_认不出的列要报出来(self, tmp_path):
        """列名写错字会落到"认不出的列"里 —— 不报的话就是静默丢信息。"""
        p = _book(tmp_path / 'hand.xlsx', {
            'images': [['key', 'kind', '表情符'], ['x', 'cg', '笑']],
        })
        _data, warnings = load_mapping(str(p))
        assert any('表情符' in w and '不认识' in w for w in warnings), warnings

    def test_备注列不算认不出的列(self, tmp_path):
        """`备注` 是刻意忽略的（不是"认不出的列"），否则每写一句注释都多一条警告。"""
        p = _book(tmp_path / 'hand.xlsx', {
            'images': [['key', 'kind', '备注'], ['x', 'cg', '这张先放着']],
        })
        _data, warnings = load_mapping(str(p))
        assert warnings == [], warnings

    def test_表头认不出就整张跳过并说清楚(self, tmp_path):
        p = _book(tmp_path / 'hand.xlsx', {
            'rules': [['随便', '什么'], ['a', 'b']],       # 没有 glob 列
            'images': [['key', 'kind'], ['x', 'cg']],
        })
        data, warnings = load_mapping(str(p))
        assert data['rules'] == []                    # 表在、但一行都没读进来
        assert any('「规则」表' in w and '找不到表头' in w for w in warnings), \
            warnings


# ==================================================================
#  读：坏文件 / 不是映射文件
# ==================================================================

class TestBadFiles:
    def test_一张认得的表都没有(self, tmp_path):
        p = _book(tmp_path / 'other.xlsx', {'Sheet1': [['a', 'b']]})
        with pytest.raises(MappingError) as exc:
            load_mapping(str(p))
        assert '角色' in str(exc.value)

    def test_老式xls要说人话(self, tmp_path):
        p = tmp_path / 'old.xls'
        p.write_bytes(b'\xd0\xcf\x11\xe0')          # OLE 头，反正读不了
        with pytest.raises(MappingError) as exc:
            load_mapping(str(p))
        assert '.xls' in str(exc.value) and '另存为' in str(exc.value)

    def test_不是工作簿(self, tmp_path):
        p = tmp_path / 'mapping.txt'
        p.write_text('hello', encoding='utf-8')
        with pytest.raises(MappingError):
            load_mapping(str(p))

    def test_坏文件不崩而是抛MappingError(self, tmp_path):
        p = tmp_path / 'broken.xlsx'
        p.write_bytes(b'not a zip at all')
        with pytest.raises(MappingError) as exc:
            load_mapping(str(p))
        # 2026-11-01：读 Excel 统一过 `excel/safety.py`，说法比从前那句
        # "读不出来"具体 —— 要点名"它不是 zip / 不像 xlsx"，人才知道去改什么。
        text = str(exc.value)
        assert '拒绝打开' in text and ('zip' in text or '不像' in text), text

    def test_mapping_cached_把坏文件降级成一句话(self, tmp_path):
        """界面/选择器不该因为一份配置坏掉就整个打不开（与 JSON 那条同一个口径）。"""
        (tmp_path / MAPPING_XLSX_FILENAME).write_bytes(b'broken')
        data, warnings, path = mapping_cached(str(tmp_path))
        assert data == {} and warnings and path


# ==================================================================
#  写：格式 + 安全
# ==================================================================

class TestWrite:
    def test_表头写人话_冻结_下拉(self, tmp_path):
        """
        表头是**给人照着填的**（用户 2026-10-31 要"模板"）：写 `glob/kind/char`
        等于逼人先学会我们的键名。读的一侧两种写法都认（`test_mapping_excel`
        里手搓的表用的就是英文表头）。
        """
        from storygraph.assets.mapping_excel import HEADERS
        p = tmp_path / MAPPING_XLSX_FILENAME
        save_mapping(str(p), GOOD)
        wb = openpyxl.load_workbook(str(p))
        try:
            ws = wb[SHEET_TITLES['rules']]
            assert [ws.cell(row=1, column=i).value for i in range(1, 7)] == \
                   list(HEADERS['rules'])
            assert HEADERS['rules'][0] == '路径'      # 人话，不是 `glob`
            assert ws.freeze_panes == 'A2'
            dvs = list(ws.data_validations.dataValidation)
            assert dvs and 'sprite' in str(dvs[0].formula1)
        finally:
            wb.close()

    def test_旧文件先备份成bak(self, tmp_path):
        p = tmp_path / MAPPING_XLSX_FILENAME
        save_mapping(str(p), GOOD)
        first = p.read_bytes()
        save_mapping(str(p), {'schema': MAPPING_SCHEMA, 'images': {}})
        bak = p.with_name(p.name + '.bak')
        assert bak.is_file() and bak.read_bytes() == first

    def test_文件被Excel占着时说人话(self, tmp_path, monkeypatch):
        p = tmp_path / MAPPING_XLSX_FILENAME
        save_mapping(str(p), GOOD)
        import storygraph.assets.mapping_excel as MX

        def boom(*_a, **_k):
            raise PermissionError(13, 'Permission denied')

        monkeypatch.setattr(MX.os, 'replace', boom)
        with pytest.raises(MappingError) as exc:
            save_mapping(str(p), GOOD)
        assert 'Excel' in str(exc.value) or '打开' in str(exc.value)

    def test_不是对象就不写(self, tmp_path):
        with pytest.raises(MappingError):
            save_mapping(str(tmp_path / MAPPING_XLSX_FILENAME), [1, 2])

    def test_后缀不对就说清楚(self, tmp_path):
        with pytest.raises(MappingError) as exc:
            write_workbook(str(tmp_path / 'x.json'), GOOD)
        assert '.xlsx' in str(exc.value)


# ==================================================================
#  优先级：同一目录 Excel 优先，且每处都说出来
# ==================================================================

class TestPriority:
    def test_同目录Excel优先(self, tmp_path):
        _book(tmp_path / MAPPING_XLSX_FILENAME, {
            'images': [['key', 'kind'], ['from-excel', 'bg']]})
        (tmp_path / MAPPING_FILENAME).write_text(json.dumps(
            {'schema': MAPPING_SCHEMA, 'images': {'from-json': {'kind': 'bg'}}}),
            encoding='utf-8')
        assert find_mapping_path(str(tmp_path)).endswith(MAPPING_XLSX_FILENAME)
        data, _warn, path = mapping_cached(str(tmp_path))
        assert path.endswith(MAPPING_XLSX_FILENAME)
        assert 'from-excel' in data['images']

    def test_项目根的JSON胜过game里的Excel(self, tmp_path):
        """目录的远近先于格式：项目根是人认定的"主目录"。"""
        game = tmp_path / 'game'
        game.mkdir()
        _book(game / MAPPING_XLSX_FILENAME, {
            'images': [['key', 'kind'], ['x', 'cg']]})
        (tmp_path / MAPPING_FILENAME).write_text('{"schema": "%s"}' % MAPPING_SCHEMA,
                                                 encoding='utf-8')
        found = find_mapping_path(str(tmp_path))
        assert found.endswith(MAPPING_FILENAME)

    def test_两份都在要提醒改哪份(self, tmp_path):
        _book(tmp_path / MAPPING_XLSX_FILENAME, {'images': [['key', 'kind']]})
        (tmp_path / MAPPING_FILENAME).write_text('{}', encoding='utf-8')
        _data, warnings, _path = mapping_cached(str(tmp_path))
        assert any('为准' in w for w in warnings), warnings

    def test_只有JSON时行为不变(self, tmp_path):
        (tmp_path / MAPPING_FILENAME).write_text(json.dumps(GOOD),
                                                 encoding='utf-8')
        assert find_mapping_path(str(tmp_path)).endswith(MAPPING_FILENAME)
        data, warnings, _path = mapping_cached(str(tmp_path))
        assert data == GOOD
        assert warnings == [], warnings

    def test_按优先级列出全部候选(self, tmp_path):
        game = tmp_path / 'game'
        game.mkdir()
        _book(game / MAPPING_XLSX_FILENAME, {'images': [['key', 'kind']]})
        (tmp_path / MAPPING_FILENAME).write_text('{}', encoding='utf-8')
        got = mapping_sources(str(tmp_path))
        assert len(got) == 2
        assert got[0].endswith(MAPPING_FILENAME)          # 项目根那份先
        assert got[1].endswith(MAPPING_XLSX_FILENAME)

    def test_没有映射时两种格式都返回None(self, tmp_path):
        assert find_mapping_path(str(tmp_path)) is None
        assert mapping_sources(str(tmp_path)) == []


# ==================================================================
#  集成：初稿 → Excel → 读回 → 判定结论必须一致
# ==================================================================

class TestIntegratedWithJudge:
    def test_转成Excel后判定结论不变(self, tmp_path):
        """
        这是**真正重要的那条**：格式换了，`resolve_with_mapping` 的结论
        必须一模一样（kind / tag / 属性 / 能不能 show）。
        """
        rel = 'game/art/full/ling/happy.png'
        before = resolve_with_mapping(GOOD, relpath=rel, asset_id='happy_ling')
        p = tmp_path / MAPPING_XLSX_FILENAME
        save_mapping(str(p), GOOD)
        back, _warn = load_mapping(str(p))
        after = resolve_with_mapping(back, relpath=rel, asset_id='happy_ling')
        assert before == after

    def test_初稿写成Excel再读回(self, tmp_path):
        """`draft_mapping` 的产物走 Excel 一圈，结论不许变。"""
        from storygraph.assets.asset_mapping import draft_mapping

        rows = {
            'a': {'ID': 'a', 'Path': 'game/art/full/ling/happy.png',
                  'Tag': 'ling', 'Attributes': 'happy'},
            'b': {'ID': 'b', 'Path': 'game/images/bg/room.png',
                  'Tag': 'bg', 'Attributes': 'room'},
        }
        project = tmp_path / 'proj'
        (project / 'game' / 'art' / 'full' / 'ling').mkdir(parents=True)
        (project / 'game' / 'images' / 'bg').mkdir(parents=True)
        draft = draft_mapping(str(project), rows, {'ling': '凌跃捷'},
                              project_name='proj')
        p = project / MAPPING_XLSX_FILENAME
        save_mapping(str(p), draft)
        back, warnings = load_mapping(str(p))
        assert warnings == [], warnings
        assert back['characters'] == draft['characters']
        assert back['rules'] == draft['rules']
        assert back['images'] == draft['images']


# ==================================================================
#  小工具 + 界面文案（纯函数，不弹窗）
# ==================================================================

class TestHelpers:
    def test_file_format(self):
        assert file_format('a/b/storygraph.mapping.xlsx') == 'excel'
        assert file_format('a/b/storygraph.mapping.XLSM') == 'excel'
        assert file_format('a/b/storygraph.mapping.json') == 'json'
        assert file_format('a/b/x.txt') == ''

    def test_is_workbook(self):
        assert is_workbook('x.xlsx') and is_workbook('x.xlsm')
        assert not is_workbook('x.xls') and not is_workbook('x.json')

    def test_validate_mapping的定位器(self):
        """同一个结构错误，JSON 报 `images['x']`、Excel 报「图片」表第 3 行。"""
        data = {'schema': MAPPING_SCHEMA, 'images': {'x': {'kind': 'sprit'}}}
        plain = validate_mapping(data)
        assert plain and "images['x']" in plain[0]
        located = validate_mapping(
            data, where=lambda *p: '「图片」表第 3 行' if p[0] == 'images' else '')
        assert located and located[0].startswith('「图片」表第 3 行：')

    def test_状态栏写明来自Excel(self, tmp_path):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.mapping_dialog import (format_label,
                                                     state_from_project,
                                                     status_text)

        _book(tmp_path / MAPPING_XLSX_FILENAME, {
            'images': [['key', 'kind'], ['x', 'cg']]})
        state = state_from_project(str(tmp_path))
        assert state.has_data()
        assert format_label(state.path) == 'Excel'
        assert '（Excel）' in status_text(state)

    def test_保存目标是同目录的json(self, tmp_path):
        """生效源是 Excel 时，「保存到项目根」写同目录 JSON（**绝不回写 Excel**）。"""
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.mapping_dialog import write_target

        _book(tmp_path / MAPPING_XLSX_FILENAME, {'images': [['key', 'kind']]})
        assert Path(write_target(str(tmp_path))).name == MAPPING_FILENAME
        assert not is_workbook(write_target(str(tmp_path)))
