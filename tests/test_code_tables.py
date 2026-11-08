# -*- coding: utf-8 -*-
"""
「编号表」通用读法（`assets/code_tables.py`）。

用户 2026-11-08：映射表只认角色文件夹里的表情/服装编号，`game/bgm/对应.txt`
（`1——Empt1y` …）与剧本里的 `# bgm13` 一样是编号，却进不了映射 ——
"导入过的资产都能映射"就只剩半句。这里量三件事：

  · 一张表怎么读（`8——` 这种空号也算读出来，否则缺口报不出来）；
  · 名字怎么对到文件（`……` 截断、`（备注）`、重名要报歧义）；
  · 扫一个项目 → 词汇行 + 缺口说明（空号、重号、没号的文件、剧本在用的号）。
"""
from __future__ import annotations

from pathlib import Path

from storygraph.assets import code_tables as CT


# ---------------------------------------------------------------------------
#  一、读行
# ---------------------------------------------------------------------------

class TestOneLine:
    def test_破折号写法(self):
        assert CT.parse_line('1——Empt1y') == ('1', 'Empt1y')

    def test_点号写法(self):
        assert CT.parse_line('2. 名字') == ('2', '名字')

    def test_顿号写法(self):
        assert CT.parse_line('3、名字') == ('3', '名字')

    def test_空格写法(self):
        assert CT.parse_line('7 不在意-indifferent') == ('7', '不在意-indifferent')

    def test_空号也算读出来(self):
        """`8——` 是"预留了号没填名"，不是"这行不是编号" —— 缺口靠它报。"""
        assert CT.parse_line('8——') == ('8', '')

    def test_前导零归一(self):
        assert CT.parse_line('01——x') == ('1', 'x')

    def test_注释和空行不算(self):
        assert CT.parse_line('# 说明') == ('', '')
        assert CT.parse_line('   ') == ('', '')

    def test_整行不是编号(self):
        assert CT.parse_line('这是备注') == ('', '')


class TestOneTable:
    def test_行号从1数(self):
        rows = CT.parse_table('1——a\n\n2——b\n')
        assert rows == [('1', 'a', 1), ('2', 'b', 3)]

    def test_重复号只留第一条(self):
        rows = CT.parse_table('1——a\n1——b\n')
        assert rows == [('1', 'a', 1)]


class TestTheNameInTheTable:
    def test_去掉尾部备注(self):
        assert CT.clean_label('青蓝实验室（剧本未出现7）') == '青蓝实验室'

    def test_去掉截断号(self):
        assert CT.clean_label('冬天的落日后……') == '冬天的落日后'
        assert CT.clean_label('很长...') == '很长'

    def test_什么也不改(self):
        assert CT.clean_label('嗯？') == '嗯？'


class TestMatchToFiles:
    def test_一模一样(self):
        assert CT.match_asset('嗯？', ['嗯？', '别的']) == ('嗯？', 'exact')

    def test_前缀对上_截断的曲名(self):
        assert CT.match_asset('冬天的落日后……', ['冬天的落日后，我在风中思念某人']) \
            == ('冬天的落日后，我在风中思念某人', 'prefix')

    def test_包含也算_但只认唯一(self):
        assert CT.match_asset('落日后', ['冬天的落日后，我在风中思念某人']) \
            == ('冬天的落日后，我在风中思念某人', 'contains')

    def test_两个都对得上就不猜(self):
        assert CT.match_asset('theme', ['theme_a', 'theme_b']) == ('', 'ambiguous')

    def test_对不上(self):
        assert CT.match_asset('不存在', ['嗯？']) == ('', 'missing')

    def test_空名字(self):
        assert CT.match_asset('', ['嗯？']) == ('', 'missing')


# ---------------------------------------------------------------------------
#  二、目录是哪类资产
# ---------------------------------------------------------------------------

class TestKindOfFolder:
    def test_音乐(self):
        assert CT.kind_for_folder('game/bgm') == 'music'

    def test_音效优先于音乐_bgm_SFX_子目录(self):
        assert CT.kind_for_folder('game/bgm/SFX') == 'sound'

    def test_语音(self):
        assert CT.kind_for_folder('game/voices') == 'voice'

    def test_背景(self):
        assert CT.kind_for_folder('game/images/bg') == 'bg'

    def test_角色文件夹归表情那套(self):
        assert CT.kind_for_folder('game/images/characters/lan') == ''

    def test_认不出就不猜(self):
        assert CT.kind_for_folder('game/whatever') == ''


# ---------------------------------------------------------------------------
#  三、扫一个项目
# ---------------------------------------------------------------------------

def _music_project(tmp_path) -> Path:
    """一个最小音乐项目：三个能对上的文件、一个没人要的文件、一张有毛病的表。"""
    proj = tmp_path / 'music'
    game = proj / 'game'
    bgm = game / 'bgm'
    bgm.mkdir(parents=True)
    for stem in ('嗯？', '冬天的落日后，我在风中思念某人', '别吵！我还在在准备中',
                 '没人要的'):
        (bgm / f'{stem}.mp3').write_bytes(b'')
    (bgm / '对应.txt').write_text(
        '1——嗯？\n'
        '2——冬天的落日后……\n'
        '3——别吵！我还在……\n'
        '4——\n'
        '5——曲子不存在\n'
        '6——嗯？\n',
        encoding='utf-8')
    # 剧本注释在用 4 号 —— 表里却是空号（正是 sr_general 的 21/24/25/26）。
    (game / 'chapter1.rpy').write_text('# bgm4\n"……"\n', encoding='utf-8')
    # 角色文件夹的表归 `sprite_codes`，这里必须跳过（不然同一条登记两次）。
    char = game / 'images' / 'characters' / 'lan'
    char.mkdir(parents=True)
    (char / '对应.txt').write_text('7 不在意-indifferent\n', encoding='utf-8')
    return proj


class TestScanAProject:
    def test_读出来的行_含没编号的文件(self, tmp_path):
        scan = CT.scan_project(_music_project(tmp_path))
        got = {(r['code'], r['id'], r['label']) for r in scan.rows}
        assert ('1', 'bgm/嗯？.mp3', '嗯？') in got
        assert ('2', 'bgm/冬天的落日后，我在风中思念某人.mp3',
                '冬天的落日后……') in got
        assert ('3', 'bgm/别吵！我还在在准备中.mp3', '别吵！我还在……') in got
        # 目录里有、表里没号：也要有一行（空号），否则它永远"映射不到"。
        assert ('', 'bgm/没人要的.mp3', '') in got

    def test_对不上的号要点名(self, tmp_path):
        scan = CT.scan_project(_music_project(tmp_path))
        assert any('曲子不存在' in n and '没有这个文件' in n for n in scan.notes), \
            scan.notes

    def test_空号要点名(self, tmp_path):
        scan = CT.scan_project(_music_project(tmp_path))
        assert any('空号' in n and '4' in n for n in scan.notes), scan.notes

    def test_剧本在用的空号要点名(self, tmp_path):
        """用户眼前真正的卡点：`# bgm4` 在用，表里第 4 号却是空的。"""
        scan = CT.scan_project(_music_project(tmp_path))
        assert any('剧本注释在用' in n and '4' in n for n in scan.notes), scan.notes

    def test_同一首挂两个号要点名(self, tmp_path):
        scan = CT.scan_project(_music_project(tmp_path))
        assert any('第 1 号和第 6 号' in n for n in scan.notes), scan.notes

    def test_没号的文件要点名(self, tmp_path):
        scan = CT.scan_project(_music_project(tmp_path))
        assert any('没有编号' in n and '没人要的' in n for n in scan.notes), \
            scan.notes

    def test_角色文件夹的表不掺和(self, tmp_path):
        scan = CT.scan_project(_music_project(tmp_path))
        assert all(r['id'].startswith('bgm/') for r in scan.rows), scan.rows

    def test_没有对应txt就没有行(self, tmp_path):
        proj = tmp_path / 'bare'
        (proj / 'game' / 'bgm').mkdir(parents=True)
        (proj / 'game' / 'bgm' / '嗯？.mp3').write_bytes(b'')
        assert CT.scan_project(proj).rows == []

    def test_项目根不对就空着(self, tmp_path):
        assert CT.scan_project(tmp_path / '不存在').rows == []

    def test_有表的种类不再报缺表(self, tmp_path):
        """音乐有表 → 不能再说"音乐没有对应.txt"（那会把人指向错的地方）。"""
        scan = CT.scan_project(_music_project(tmp_path))
        assert not any('music 号' in n and '没有这类资产' in n
                       for n in scan.notes), scan.notes

    def test_没有表的种类要报缺口(self, tmp_path):
        """剧本写 `# se6`、项目里却没有音效表 —— 这 6 号永远对不上文件。"""
        proj = tmp_path / 'no_table'
        (proj / 'game').mkdir(parents=True)
        (proj / 'game' / 'a.rpy').write_text('# se6\n', encoding='utf-8')
        scan = CT.scan_project(proj)
        assert scan.rows == []
        assert any('sound' in n and '对应.txt' in n for n in scan.notes), scan.notes


class TestFindTables:
    def test_找得到音乐表(self, tmp_path):
        tables = CT.find_tables(_music_project(tmp_path))
        assert [(t.folder, t.kind) for t in tables] == [('game/bgm', 'music')]


# ---------------------------------------------------------------------------
#  四、接进映射表
# ---------------------------------------------------------------------------

class TestTheMappingEditor:
    def test_开窗就把音乐编号填进中文叫法(self, qapp, tmp_path):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        d = MappingDialog(None, project_root=_music_project(tmp_path))
        try:
            rows = d.current_data().get('vocabulary') or []
            music = {r['code']: r['id'] for r in rows
                     if str(r.get('kind')) == 'music' and r.get('code')}
            assert music['1'] == 'bgm/嗯？.mp3', rows
            assert music['2'] == 'bgm/冬天的落日后，我在风中思念某人.mp3', rows
        finally:
            d.close()

    def test_再点一次不会翻倍(self, qapp, tmp_path):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        d = MappingDialog(None, project_root=_music_project(tmp_path))
        try:
            again = d._editor.autofill_codes()
            assert again['vocabulary'] == 0, again
        finally:
            d.close()

    def test_人写过的同一条不许被冲掉(self, qapp, tmp_path):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        proj = _music_project(tmp_path)
        (proj / 'storygraph.mapping.json').write_text(
            '{"schema": "storygraph.mapping/1", "vocabulary": '
            '[{"kind": "music", "id": "bgm/嗯？.mp3", "code": "99", '
            '"label": "我改的"}]}', encoding='utf-8')
        d = MappingDialog(None, project_root=proj)
        try:
            rows = [r for r in (d.current_data().get('vocabulary') or [])
                    if r.get('id') == 'bgm/嗯？.mp3']
            assert len(rows) == 1, rows
            assert rows[0]['code'] == '99', rows
        finally:
            d.close()
