# -*- coding: utf-8 -*-
"""
tests/test_vocabulary.py
========================
**词汇表**：引擎写法 ⇄ 人话（用户 2026-10-31 的六条"通用性"要求）

那六条其实是同一件事：屏幕上要出现的那串字和一个说法之间的对照。

  1. `at i11` → 「1人，近处」
  2. `bgm/嗯？.mp3` → `18`
  3. `bg9_2` → `bg9.2`
  4. `DY` → 「大爷」
  5. `my_wipe` → 「闪白」（过场调用；卡片上能选、搜得到）
  6. `base_swimsuit` → `B`（按角色分，一个人一套）

所以这里钉的是**那层通用机制**：一条 = `{kind, id, label, code, char}`，
查得到就显示人话，查不到就原样显示（词汇表是补充，不是白名单）。
"""

from __future__ import annotations

import textwrap

from storygraph.assets import vocabulary as V


# ---------------------------------------------------------------------------
#  种类：一种"要起中文名"的东西
# ---------------------------------------------------------------------------

class TestKinds:
    def test_六条要求各有自己的种类(self):
        for kind in (V.KIND_TRANSFORM, V.KIND_MUSIC, V.KIND_BG,
                     V.KIND_CHARACTER, V.KIND_CUE, V.KIND_COSTUME):
            assert kind in V.KIND_SPECS, kind
            assert V.KIND_SPECS[kind].label

    def test_读得懂人写的种类名(self):
        """Excel 里人一定写中文（`位置` / `音乐` / `过场`）—— 那一列得认。"""
        assert V.kind_from_text('位置') == V.KIND_TRANSFORM
        assert V.kind_from_text('位置 / 镜头') == V.KIND_TRANSFORM
        assert V.kind_from_text('音乐') == V.KIND_MUSIC
        assert V.kind_from_text('bgm') == V.KIND_MUSIC
        assert V.kind_from_text('过场') == V.KIND_CUE
        assert V.kind_from_text('服装') == V.KIND_COSTUME
        assert V.kind_from_text('乱写的') == ''

    def test_带斜杠的名字不会被当成路径(self):
        """
        `位置 / 镜头` 里那个斜杠差点被 `norm` 当路径切掉 —— 只剩「镜头」，
        于是「位置」永远认不出是哪一类（Excel 里写「位置」全被跳过）。
        """
        assert V.norm('位置 / 镜头') == '位置镜头'
        # 真路径才切：`bgm/嗯？.mp3` → 文件名 `嗯？` → 归一得「嗯」
        assert V.norm('bgm/嗯？.mp3') == '嗯'

    def test_服装和表情是按角色分的(self):
        assert V.KIND_SPECS[V.KIND_COSTUME].scoped
        assert V.KIND_SPECS[V.KIND_EXPRESSION].scoped
        assert not V.KIND_SPECS[V.KIND_TRANSFORM].scoped


# ---------------------------------------------------------------------------
#  一张表：六条要求都能换
# ---------------------------------------------------------------------------

DATA = {
    'vocabulary': [
        {'kind': 'transform', 'id': 'i11', 'label': '1人，近处'},
        {'kind': 'music', 'id': 'bgm/嗯？.mp3', 'code': '18', 'label': '嗯？'},
        {'kind': 'bg', 'id': 'bg9_2', 'code': 'bg9.2', 'label': '储物间'},
        {'kind': 'character', 'id': 'DY', 'label': '大爷'},
        {'kind': 'cue', 'id': 'my_wipe', 'label': '闪白'},
    ],
    'outfit_codes': [
        {'char': 'lan', 'code': 'B', 'attr': 'base_swimsuit', 'label': '泳装'},
        {'char': 'ye', 'code': 'B', 'attr': 'base_handdown', 'label': '手放下'},
    ],
    'expression_codes': [
        {'char': 'lan', 'no': '7', 'attr': 'indifferent', 'label': '不在意'},
    ],
}


class TestSixScenarios:
    def test_1_变换名字(self):
        vocab = V.from_mapping(DATA)
        assert vocab.label('transform', 'i11') == '1人，近处'
        # 反方向也要能走（人话 → 引擎写法）
        assert vocab.ident('transform', '1人，近处') == 'i11'

    def test_2_音乐编号(self):
        vocab = V.from_mapping(DATA)
        assert vocab.label('music', 'bgm/嗯？.mp3') == '18 嗯？'
        assert vocab.ident('music', '18') == 'bgm/嗯？.mp3'
        assert vocab.ident('music', '嗯？') == 'bgm/嗯？.mp3'

    def test_3_背景编号(self):
        vocab = V.from_mapping(DATA)
        assert vocab.label('bg', 'bg9_2') == 'bg9.2 储物间'
        assert vocab.ident('bg', 'bg9.2') == 'bg9_2'

    def test_4_角色中文名(self):
        vocab = V.from_mapping(DATA)
        assert vocab.label('character', 'DY') == '大爷'
        assert vocab.ident('character', '大爷') == 'DY'

    def test_5_过场调用(self):
        vocab = V.from_mapping(DATA)
        assert vocab.label('cue', 'my_wipe') == '闪白'
        assert vocab.ident('cue', '闪白') == 'my_wipe'
        # 搜得到（中文名 / 原名都算命中）
        assert [t.id for t in vocab.search('闪白')] == ['my_wipe']
        assert [t.id for t in vocab.search('my_wipe')] == ['my_wipe']

    def test_6_服装代号按角色分(self):
        vocab = V.from_mapping(DATA)
        assert vocab.label('costume', 'base_swimsuit', 'lan') == 'B 泳装'
        assert vocab.label('costume', 'base_handdown', 'ye') == 'B 手放下'
        # 同一个字母在两个角色身上是两身不同的衣服 —— 不会串
        assert vocab.ident('costume', 'B', 'lan') == 'base_swimsuit'
        assert vocab.ident('costume', 'B', 'ye') == 'base_handdown'

    def test_服装不会跑去别的角色身上(self):
        vocab = V.from_mapping(DATA)
        only = vocab.options('costume', 'lan')
        assert [t.id for t in only] == ['base_swimsuit']
        assert vocab.label('costume', 'base_handdown', 'lan') == 'base_handdown'


# ---------------------------------------------------------------------------
#  规矩：查不到就原样（词汇表是补充，不是白名单）
# ---------------------------------------------------------------------------

class TestRules:
    def test_查不到原样显示(self):
        vocab = V.from_mapping(DATA)
        assert vocab.label('transform', 'i99') == 'i99'
        assert vocab.ident('transform', '手写的自由值') == '手写的自由值'
        assert vocab.label('', 'x') == 'x'

    def test_空值不炸(self):
        vocab = V.from_mapping({})
        assert vocab.label('transform', '') == ''
        assert vocab.ident('music', None) == ''
        assert vocab.search('') == []

    def test_code与label各管一边(self):
        """两个都有 → `18 嗯？`；只有一个 → 就显示那一个；都没有 → 原名。"""
        assert V.Term('music', 'a.mp3', label='嗯？', code='18').display() == '18 嗯？'
        assert V.Term('music', 'a.mp3', code='18').display() == '18'
        assert V.Term('music', 'a.mp3', label='嗯？').display() == '嗯？'
        assert V.Term('music', 'a.mp3').display() == 'a.mp3'

    def test_坏条目跳过不猜(self):
        terms = V.parse_terms([{'kind': 'nope', 'id': 'x'}, {'id': 'y'},
                               {'kind': 'music', 'id': ''}, 'not a dict',
                               {'kind': 'music', 'id': 'ok.mp3'}])
        assert [t.id for t in terms] == ['ok.mp3']

    def test_路径只比文件名(self):
        """`bgm/嗯？.mp3` 与 `嗯？` 是同一首 —— 人不会每次都写全路径。"""
        vocab = V.from_mapping(DATA)
        assert vocab.find('music', '嗯？.mp3').id == 'bgm/嗯？.mp3'

    def test_模糊匹配只在唯一命中时认(self):
        """两个都叫「嗯」时，写一个**第三种**写法（归一后撞上）不许乱指。"""
        two = V.from_mapping({'vocabulary': [
            {'kind': 'music', 'id': 'bgm/a.mp3', 'label': '嗯'},
            {'kind': 'music', 'id': 'bgm/b.mp3', 'label': '嗯！'},
        ]})
        # 原样命中优先：写「嗯」就是 a，写「嗯！」就是 b
        assert two.ident('music', '嗯') == 'bgm/a.mp3'
        assert two.ident('music', '嗯！') == 'bgm/b.mp3'
        # 归一后两条都像 → **不猜**，原样返回，让手写的值留着
        assert two.ident('music', '嗯？') == '嗯？'

    def test_搜索把中文名排前面(self):
        vocab = V.from_mapping(DATA)
        hits = vocab.search('泳装')
        assert hits and hits[0].id == 'base_swimsuit'


# ---------------------------------------------------------------------------
#  从剧本注释里学编号（音乐 / 音效 / 背景）
# ---------------------------------------------------------------------------

SCRIPT = textwrap.dedent('''\
    label start:
        # bgm18
        play music "bgm/嗯？.mp3" fadein 1.0
        # bg9.2
        scene bg bg9_2
        # se9
        play sound "se/hit.ogg"
        # bgm18
        play music "bgm/另一首.mp3"
    ''')


class TestLearn:
    def test_学到音乐音效背景的编号(self, tmp_path):
        (tmp_path / 'game').mkdir()
        (tmp_path / 'game' / 'c.rpy').write_text(SCRIPT, encoding='utf-8')
        items = V.learn(str(tmp_path))
        got = {(i['kind'], i['id']): i['code'] for i in items}
        assert got[('music', 'bgm/嗯？.mp3')] == 'bgm18'
        assert got[('bg', 'bg9_2')] == 'bg9.2'
        assert got[('sound', 'se/hit.ogg')] == 'se9'
        # 同一句注释配两条 play music：**先到先得**（后面那条不覆盖）
        assert 'bgm/另一首.mp3' not in {i['id'] for i in items}

    def test_中间隔了别的语句就不算(self, tmp_path):
        bad = '# bgm18\n$ x = 1\nplay music "bgm/x.mp3"\n'
        (tmp_path / 'game').mkdir()
        (tmp_path / 'game' / 'c.rpy').write_text(bad, encoding='utf-8')
        assert V.learn(str(tmp_path)) == []

    def test_没有项目就是空(self):
        assert V.learn('') == []


# ---------------------------------------------------------------------------
#  存进映射：能编辑、能往返
# ---------------------------------------------------------------------------

class TestInMapping:
    def test_是映射里的独立一段(self):
        from storygraph.assets import mapping_edit as ME
        assert 'vocabulary' in ME.SECTIONS
        spec = ME.SPECS['vocabulary']
        assert [f.key for f in spec.fields] == ['kind', 'id', 'label', 'code',
                                                'char', 'note']
        # 「是什么」是下拉，选项来自 `vocabulary.KIND_SPECS`（只有一个真源）
        kind_field = spec.fields[0]
        assert kind_field.kind == 'choice'
        assert len(kind_field.choices) == len(V.KIND_SPECS)

    def test_新增一行填一格就落盘(self):
        from storygraph.assets import mapping_edit as ME
        edit = ME.MappingEdit({})
        index = edit.add('vocabulary')
        assert edit.set_field('vocabulary', index, 'kind', 'transform') == ''
        edit.set_field('vocabulary', index, 'id', 'i11')
        edit.set_field('vocabulary', index, 'label', '1人，近处')
        assert edit.to_data()['vocabulary'] == [
            {'kind': 'transform', 'id': 'i11', 'label': '1人，近处'}]
        assert edit.problems() == []

    def test_空壳行不落盘(self):
        from storygraph.assets import mapping_edit as ME
        edit = ME.MappingEdit({})
        edit.add('vocabulary')
        edit.add('vocabulary')
        assert 'vocabulary' not in edit.to_data()
        assert edit.blank_rows() == 2

    def test_缺原名_缺种类_重复都会说(self):
        from storygraph.assets import mapping_edit as ME
        edit = ME.MappingEdit({'vocabulary': [
            {'kind': 'transform', 'label': '只有名字'},
            {'id': 'i11'},
            {'kind': 'costume', 'id': 'base_swimsuit'},
            {'kind': 'music', 'id': 'a.mp3', 'code': '1'},
            {'kind': 'music', 'id': 'a.mp3', 'code': '2'},
            {'kind': '乱写', 'id': 'x', 'label': 'y'},
        ]})
        texts = [p.text for p in edit.problems()]
        assert any('没写「原名」' in t for t in texts)
        assert any('没选「是什么」' in t for t in texts)
        assert any('按角色分' in t for t in texts)
        assert any('一模一样' in t for t in texts)
        assert any('不是认识的种类' in t for t in texts)

    def test_Excel往返带上词汇表(self, tmp_path):
        from storygraph.assets import mapping_excel as MX
        data = MX.blank_template()
        data['vocabulary'] = [dict(t) for t in DATA['vocabulary']]
        path = tmp_path / 'storygraph.mapping.xlsx'
        MX.write_workbook(str(path), data)
        back, warnings = MX.read_workbook(str(path))
        assert warnings == [], warnings
        assert back['vocabulary'][0]['kind'] == 'transform'
        assert back['vocabulary'] == data['vocabulary']

    def test_Excel里写中文种类也认(self, tmp_path):
        """人做表一定写「位置」「音乐」—— 那一列要认（认不出才跳过）。"""
        import openpyxl
        from storygraph.assets import mapping_excel as MX
        path = tmp_path / 'mine.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = '中文叫法'
        ws.append(['是什么', '原名', '叫什么'])
        ws.append(['位置', 'i11', '1人，近处'])
        ws.append(['音乐', 'bgm/a.mp3', '18'])
        ws.append(['乱写', 'x', 'y'])
        wb.save(str(path))
        data, warnings = MX.read_workbook(str(path))
        got = {e['id']: e['kind'] for e in data['vocabulary']}
        assert got == {'i11': 'transform', 'bgm/a.mp3': 'music'}
        assert any('不认识「是什么」' in w for w in warnings), warnings
