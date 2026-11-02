# -*- coding: utf-8 -*-
"""
tests/test_sprite_codes.py
==========================
**立绘代号表**：`B7` 这种美术需求表的写法 ⇄ 剧本里真正要写的属性。

用户 2026-10-31 的原话（这一片的验收标准）：

> 你看看真实项目之中的注释：#B7 这些注释，B对应的是服装，而7对应的是这个
> 角色的第7号表情，那请问我该如何通过你这个映射表把 lan swim_suit angry
> 映射为 lan B 7 呢……？不太方便吧？？？你再想想……？

钉住这几件事：

  · 编号表有**三种真实写法**（`7 不在意-indifferent` / `2 close_happy-闭眼微笑`
    / `表情1-哈哈 — haha`），哪一种都得读对；
  · `layeredimage` 里 `attribute happy default:`（带 `default`）也是表达式
    —— 漏了它就会把表情当成服装（第一版就是这么歪的）；
  · 剧本注释是**证据**：`# 表情B7` 后面那条 `show` 说了 B 是哪身衣服；
  · 一组只有一个属性（`show ye hi` 不撤销服装），跨组才替换；
  · 别的角色的表情（`show ye uh`）是手误，**不许**算成服装；
  · 两套编号表对不上时说清楚，并用"和剧本注释对得更多"的那套；
  · `lan swim_suit angry` ⇄ `lan B7` 两个方向都能换；缺东西时**说缺什么**。
"""

from __future__ import annotations

import textwrap

import pytest

from storygraph.assets import sprite_codes as SC


# ---------------------------------------------------------------------------
#  一、编号表的三种真实写法
# ---------------------------------------------------------------------------

class TestExpressionTable:
    def test_中文在前(self):
        """lan / ye：`7 不在意-indifferent`。"""
        assert SC.parse_expression_line('7 不在意-indifferent') == \
            ('7', 'indifferent', '不在意')

    def test_属性在前(self):
        """ling：`2 close_happy-闭眼微笑`（和前一种**顺序相反**）。"""
        assert SC.parse_expression_line('2 close_happy-闭眼微笑') == \
            ('2', 'close_happy', '闭眼微笑')

    def test_破折号带表情前缀(self):
        """xing：`表情1-哈哈 — haha`（破折号是 —，还带「表情」两个字）。"""
        assert SC.parse_expression_line('表情1-哈哈 — haha') == \
            ('1', 'haha', '哈哈')

    def test_括号里的名字不会被吃掉(self):
        """`哼哼（微笑）` —— 只去空格，不许去括号（去了就少半个括号）。"""
        assert SC.parse_expression_line('表情3-哼哼（微笑） — henhen') == \
            ('3', 'henhen', '哼哼（微笑）')

    def test_行尾带图片路径(self):
        """
        lan 的 `中英对应.txt`：`1 闭眼笑-close_happy images\\characters\\lan\\x.png`。

        第一版没认出来 → lan 的两份编号表只剩一份，"两份对不上"这件**真事**
        就看不到了（于是 lan 的 8/9 号会跟剧本注释错位）。
        """
        assert SC.parse_expression_line(
            r'1 闭眼笑-close_happy images\characters\lan\close_happy.png') == \
            ('1', 'close_happy', '闭眼笑')
        assert SC.parse_expression_line(
            '3 噢-agreeable images/characters/lan/a.PNG') == \
            ('3', 'agreeable', '噢')

    def test_认不出的行就当没有(self):
        for line in ('', '   ', '# 注释', '这一行不是编号', '7 只有中文'):
            assert SC.parse_expression_line(line) == ('', '', '')

    def test_读一整段(self):
        text = textwrap.dedent('''\
            1 闭眼笑-close_happy
            2 哦-oh

            3 噢-agreeable
            ''')
        table = SC.parse_expression_text(text)
        assert table == {'1': ('close_happy', '闭眼笑'),
                         '2': ('oh', '哦'),
                         '3': ('agreeable', '噢')}

    def test_两份表对不上时会说出来(self, tmp_path):
        """
        真实项目：lan 的 `对应.txt` 说 8 是 `angry`、`中英对应.txt` 说 8 是
        `angry2`。这里**不替人裁决**：两份都交回去 + 一句人话，
        谁跟剧本对得上由 `scan_project` 用票数挑。
        """
        (tmp_path / '对应.txt').write_text('8 生气-angry\n', encoding='utf-8')
        (tmp_path / '中英对应.txt').write_text('8 生气2-angry2\n',
                                              encoding='utf-8')
        files, conflicts = SC.read_expression_tables(tmp_path)
        assert set(files) == {'对应.txt', '中英对应.txt'}
        assert files['对应.txt']['8'] == ('angry', '生气')
        assert any('第 8 号' in c for c in conflicts), conflicts

    def test_只有一份表时没有冲突(self, tmp_path):
        (tmp_path / '对应.txt').write_text('7 不在意-indifferent\n',
                                          encoding='utf-8')
        files, conflicts = SC.read_expression_tables(tmp_path)
        assert conflicts == []
        assert files == {'对应.txt': {'7': ('indifferent', '不在意')}}


# ---------------------------------------------------------------------------
#  二、layeredimage：哪些属性是表情、哪些是服装
# ---------------------------------------------------------------------------

LAYERED = textwrap.dedent('''\
    layeredimage ye:
        image_format 'images/characters/ye/{image}.png'

        group expressions when not base_handdown:
            attribute happy default:
                'happy'
            attribute hi:
                'hi'
            attribute care:
                'care'
            attribute gratified:
                'gratified'

        group outfit when base_handdown:
            attribute base_handdown:
                y_clip()
    ''')


class TestLayeredImage:
    def test_带default的attribute也算(self):
        """
        `attribute happy default:` —— 第一版正则要求名字后面直接是冒号，
        于是 `happy` 掉出了表情组，整张服装表把它当成了衣服（全歪）。
        """
        got = SC.parse_layeredimage(LAYERED)['ye']
        assert 'happy' in got['expr'], got
        assert got['outfit'] == ['base_handdown']

    def test_别的image里的attribute不算(self):
        text = LAYERED + textwrap.dedent('''\

            image side ye = LayeredImageProxy("ye")
            define something = 1
            ''')
        assert list(SC.parse_layeredimage(text)) == ['ye']


# ---------------------------------------------------------------------------
#  三、剧本注释 = 证据
# ---------------------------------------------------------------------------

SCRIPT = textwrap.dedent('''\
    label start:
        show ye aa happy at l11
        # 表情B6 拉进
        show ye base_handdown happy at f11
        # 表情B7
        show ye hi at i11
        # 表情B11
        show ye gratified
        # 表情A10
        show ye aa care
        # 表情A3
        show ye happy
    ''')
KNOWN = {'ye': {'expr': ['happy', 'hi', 'care', 'gratified', 'uh'],
                'outfit': ['base_handdown']}}
#: `uh` 是 **lan** 的表情（跨角色集合），用来验证"手误不算服装"。
EVERYTHING = {'ye': KNOWN['ye'], 'lan': {'expr': ['uh'], 'outfit': []}}


class TestEvidence:
    def _hits(self, text=SCRIPT, known=None):
        return SC.scan_script_text(text, name='chapter1.rpy',
                                   known=known or KNOWN)

    def test_注释和后面那条show配成一对(self):
        hits = self._hits()
        assert [(h.letter, h.number, h.outfit, h.expr) for h in hits] == [
            ('B', '6', 'base_handdown', 'happy'),
            ('B', '7', 'base_handdown', 'hi'),
            ('B', '11', 'base_handdown', 'gratified'),
            ('A', '10', 'aa', 'care'),
            ('A', '3', 'aa', 'happy'),
        ]

    def test_一组一个属性_换表情不会丢掉服装(self):
        """`show ye hi` 只换表情 —— 服装还是上一条的 base_handdown。"""
        hits = self._hits()
        assert hits[1].outfit == 'base_handdown'

    def test_注释里最后一个代号才算数(self):
        """`# 表情B6换到表情B7，引擎做不出来就用6` —— 说的是**换到** B7。"""
        text = '# 表情B6换到表情B7，引擎做不出来就用6\nshow ye hi\n'
        hits = self._hits(text)
        assert [(h.letter, h.number) for h in hits] == [('B', '7')]

    def test_别的角色的表情是手误_不算服装(self):
        """
        真实项目里有 `show ye uh`（`uh` 是 lan 的表情）。要是把它当成服装，
        `A` 号就会多出 9 票噪声 —— 所以"谁都没声明过"才算服装标记。
        """
        text = '# 表情A7\nshow ye uh\n'
        hits = SC.scan_script_text(text, known=EVERYTHING)
        assert hits[0].outfit == '', hits[0]

    def test_多打一个词也能救回来(self):
        """
        真实的 `show aa ye hi at i11`（`aa` 多打了）：第一眼该认出 `ye` 才是角色。
        救回来的结论正好对：`# 表情B7` 要的就是 `hi`。
        """
        text = '# 表情B7\nshow aa ye hi\n'
        hits = SC.scan_script_text(text, known=KNOWN)
        assert [(h.char, h.expr) for h in hits] == [('ye', 'hi')]

    def test_hide之后重新开始(self):
        text = '# 表情B6\nshow ye base_handdown happy\nhide ye\n' \
               '# 表情A1\nshow ye happy\n'
        hits = SC.scan_script_text(text, known=KNOWN)
        assert [(h.letter, h.outfit) for h in hits] == [('B', 'base_handdown'),
                                                        ('A', '')]

    def test_说了行号(self):
        assert self._hits()[0].where == 'chapter1.rpy:4'


# ---------------------------------------------------------------------------
#  四、扫整个项目 → 两段的行
# ---------------------------------------------------------------------------

def _project(tmp_path, *, scripts=None, lan_table='7 不在意-indifferent\n8 生气-angry\n',
             extra=None):
    """搭一个最小项目：`game/definitions.rpy` + 角色文件夹 + 一章剧本。"""
    game = tmp_path / 'game'
    game.mkdir()
    (game / 'definitions.rpy').write_text(LAYERED, encoding='utf-8')
    folder = game / 'images' / 'characters' / 'ye'
    folder.mkdir(parents=True)
    (folder / '对应.txt').write_text(lan_table, encoding='utf-8')
    for name, text in (scripts or {'chapter1.rpy': SCRIPT}).items():
        (game / name).write_text(text, encoding='utf-8')
    for name, text in (extra or {}).items():
        (folder / name).write_text(text, encoding='utf-8')
    return str(tmp_path)


class TestScanProject:
    def test_两段都建出来了(self, tmp_path):
        scan = SC.scan_project(_project(tmp_path))
        outfits = {(r['char'], r['code']): r['attr'] for r in scan.outfits}
        assert outfits[('ye', 'A')] == 'aa'
        assert outfits[('ye', 'B')] == 'base_handdown'
        numbers = {r['no']: r['attr'] for r in scan.expressions}
        assert numbers['7'] == 'indifferent'
        assert numbers['8'] == 'angry'

    def test_剧本里没出现过的服装留空等人填(self, tmp_path):
        """`clothes2` 这种剧本里没写过的服装：**留空**，不按顺序硬凑一个字母。"""
        text = LAYERED + ('        attribute clothes2:\n            x\n')
        (tmp_path / 'game').mkdir()
        (tmp_path / 'game' / 'definitions.rpy').write_text(text,
                                                          encoding='utf-8')
        (tmp_path / 'game' / 'c.rpy').write_text('show ye aa happy\n',
                                                encoding='utf-8')
        scan = SC.scan_project(str(tmp_path))
        blank = [r['attr'] for r in scan.outfits if not r['code']]
        assert 'clothes2' in blank, scan.outfits

    def test_证据太弱就留空并说一句(self, tmp_path):
        """只对上一次的字母（真实项目里有 `show lan p happy` 这种手误）。"""
        script = '# 表情A7\nshow ye aa care\n# 表情B1\nshow ye hole\n'
        scan = SC.scan_project(_project(tmp_path, scripts={'c.rpy': script}))
        assert not [r for r in scan.outfits if r['code']]
        assert any('证据太弱' in n for n in scan.notes), scan.notes

    def test_剧本里出现但引擎没有的衣服要标出来(self, tmp_path):
        """
        真实项目 `sr_general` 的 `aa`：写在 33 处 `show` 里（注释也写着
        `# 表情A14`），四个 `layeredimage` 里却一个 `attribute aa` 都没有 ——
        Ren'Py 8.4 的 lint 原话是 `'lan aa happy' is not an image`。

        用户 2026-11-02 的原话："这个 aa 从未被注册过……它哪儿来的 aa 映射？"
        病根：表是**扫剧本注释**扫出来的，不是"注册"出来的。所以行要留着
        （人家就是这么写的），但必须当场说清引擎不认 —— 否则人只会疑惑
        "为什么卡片上的属性格里没有它"。
        """
        scan = SC.scan_project(_project(tmp_path))
        row = next(r for r in scan.outfits if r['attr'] == 'aa')
        assert row['code'] == 'A', row          # 字母照注释保留，不偷偷抹掉
        assert '引擎里没有这身衣服' in row['label'], row
        assert any('not an image' in n and 'aa' in n for n in scan.notes), \
            scan.notes

    def test_项目自己的服装表给出人话名字(self, tmp_path):
        """
        项目在游戏代码里另有一张服装表（真实项目 `sr_general` 的
        `CC_OUTFIT_CYCLE` + `CC_OUTFIT_LABEL`：`aa` → 「普通」）。

        它**不是引擎声明**，所以警告照旧；但说明里必须带上这个名字 —— 否则人
        对不上号："我明明在角色中心管它叫普通，工具表里为什么只有 `aa`？"
        """
        game = tmp_path / 'game'
        game.mkdir()
        (game / 'definitions.rpy').write_text(LAYERED, encoding='utf-8')
        (game / 'center.rpy').write_text(
            'init python:\n'
            '    OUTFIT_CYCLE = {\n'
            '        "ye": ["aa", "base_handdown"],\n'
            '    }\n'
            '    OUTFIT_LABEL = {\n'
            '        "aa": "普通",\n'
            '    }\n', encoding='utf-8')
        (game / 'c.rpy').write_text('# 表情A7\nshow ye aa care\n'
                                    '# 表情A7\nshow ye aa care\n',
                                    encoding='utf-8')
        scan = SC.scan_project(str(tmp_path))
        row = next(r for r in scan.outfits if r['attr'] == 'aa')
        assert '普通' in row['label'], row
        assert '引擎里没有这身衣服' in row['label'], row

    def test_引擎认得的衣服不加警告(self, tmp_path):
        """`layeredimage` 里有 `attribute aa` 的项目：一个字都不许多说。"""
        text = LAYERED + ('        attribute aa:\n            x\n')
        game = tmp_path / 'game'
        game.mkdir()
        (game / 'definitions.rpy').write_text(text, encoding='utf-8')
        (game / 'c.rpy').write_text('# 表情A7\nshow ye aa care\n'
                                   '# 表情A7\nshow ye aa care\n',
                                   encoding='utf-8')
        scan = SC.scan_project(str(tmp_path))
        row = next(r for r in scan.outfits if r['attr'] == 'aa')
        assert row['code'] == 'A', row
        assert '引擎里没有' not in row['label'], row
        assert not [n for n in scan.notes if 'not an image' in n], scan.notes

    def test_表情表里没有的号会补一行并提醒(self, tmp_path):
        script = '# 表情A20\nshow ye aa care\n# 表情A20\nshow ye aa care\n'
        scan = SC.scan_project(_project(tmp_path, scripts={'c.rpy': script}))
        rows = [r for r in scan.expressions if r['no'] == '20']
        assert rows and rows[0]['attr'] == 'care'
        assert any('第 20 号' in n for n in scan.notes), scan.notes

    def test_两份编号表用对得上的那份(self, tmp_path):
        """
        剧本里 `# 表情A7` 后面是 `care`：谁的表说 7=care 就用谁。
        这正是真实项目 lan 的情形（对应.txt 与 中英对应.txt 编号不一致）。
        """
        other = '7 关心-care\n8 生气-angry\n'
        scan = SC.scan_project(_project(
            tmp_path, lan_table='7 不在意-indifferent\n',
            extra={'中英对应.txt': other},
            scripts={'c.rpy': '# 表情A7\nshow ye aa care\n'
                               '# 表情A7\nshow ye aa care\n'}))
        assert {r['no']: r['attr'] for r in scan.expressions}['7'] == 'care'
        assert any('对不上' in n for n in scan.notes), scan.notes

    def test_只剩一份表但它和注释对不上也要说(self, tmp_path):
        """
        真实项目 lan 的正脸：`对应.txt` 重新编过号（`angry2` 从 8 号挪到 19 号），
        剧本注释还按老编号写 —— 26 处证据里只对上 5 处。

        两份表都在时有"谁赢"，**只剩一份**时以前一声不响：整张表静默地错一半。
        这里不替人裁决（一行数据都不改），只把数字说出来。
        """
        script = '# 表情A7\nshow ye aa care\n# 表情A8\nshow ye aa angry\n'
        scan = SC.scan_project(_project(
            tmp_path, lan_table='7 不在意-indifferent\n8 生气-angry\n',
            scripts={'c.rpy': script}))
        assert any('只对上 0/2' in n for n in scan.notes), scan.notes
        # 表本身一个字没改：说的还是表里那两行
        assert {r['no']: r['attr'] for r in scan.expressions}['7'] == 'indifferent'

    def test_对得上就不吵(self, tmp_path):
        script = '# 表情A7\nshow ye aa care\n# 表情A7\nshow ye aa care\n'
        scan = SC.scan_project(_project(
            tmp_path, lan_table='7 关心-care\n', scripts={'c.rpy': script}))
        assert not any('只对上' in n for n in scan.notes), scan.notes

    def test_工具留下的副本不重复投票(self, tmp_path):
        """
        同一章旁边躺着一个 `.generated.rpy`（工具早先导出的副本）时，证据
        **不许被数两遍** —— 用户 2026-10-31 实测的 bug：

            真机 `xing` 的「B」号服装只有**一处**证据（`chapter1_4.rpy`），
            按规矩"证据太弱 → 代号留空"；可副本 `chapter1_4.generated.rpy`
            也在 `game/` 里 → 同一句被数两遍 → 票数 2 → 静默变成「B」。
            两个近乎克隆的项目因此给出**不一样**的代号表，而项目的声明
            一个字都没改。

        改法：派生文件（`*.generated.rpy` / `*.blocked.rpy` / `*.bak.rpy`）
        是**投影**，不是事实，不进任何"项目里有什么"的统计。
        """
        script = '# 表情B1\nshow ye base_handdown happy\n'
        one = tmp_path / 'one'
        one.mkdir()
        clean = SC.scan_project(_project(one, scripts={'c.rpy': script}))
        assert not [r for r in clean.outfits if r['code']], clean.outfits
        assert any('证据太弱' in n for n in clean.notes), clean.notes

        two = tmp_path / 'two'
        two.mkdir()
        with_copy = SC.scan_project(_project(two, scripts={
            'c.rpy': script,
            'c.generated.rpy': script,       # 工具留下的副本：同一句话
        }))
        assert not [r for r in with_copy.outfits if r['code']], (
            '副本把票数从 1 翻成 2，代号被静默钉在 B 上 —— '
            f'两个近乎克隆的项目会给出不同答案：{with_copy.outfits}')
        assert any('证据太弱' in n for n in with_copy.notes), with_copy.notes

    def test_派生文件根本不进证据(self, tmp_path):
        """同一条规矩的底层判据（`_script_texts` 是唯一取文本的地方）。"""
        from pathlib import Path
        root = Path(_project(tmp_path, scripts={'c.rpy': SCRIPT}))
        (root / 'game' / 'c.generated.rpy').write_text(SCRIPT, encoding='utf-8')
        (root / 'game' / 'c.blocked.rpy').write_text(SCRIPT, encoding='utf-8')
        (root / 'game' / 'c.bak.rpy').write_text(SCRIPT, encoding='utf-8')
        texts = SC._script_texts(root)
        assert 'game/c.rpy' in texts
        for name in ('game/c.generated.rpy', 'game/c.blocked.rpy',
                     'game/c.bak.rpy'):
            assert name not in texts, f'{name} 是派生文件，不能进证据'

    def test_预览用的变体不算角色(self, tmp_path):
        """`layeredimage ye_cc` 是预览图的变体，不该在表里凭空多几行。"""
        text = LAYERED + LAYERED.replace('layeredimage ye:', 'layeredimage ye_cc:')
        (tmp_path / 'game').mkdir()
        (tmp_path / 'game' / 'definitions.rpy').write_text(text,
                                                          encoding='utf-8')
        scan = SC.scan_project(str(tmp_path))
        assert {r['char'] for r in scan.expressions} == set()
        assert {r['char'] for r in scan.outfits} == set()

    def test_没有项目也不炸(self):
        scan = SC.scan_project('')
        assert scan.outfits == [] and scan.expressions == []
        assert scan.notes


# ---------------------------------------------------------------------------
#  五、换算：人话属性 ⇄ 代号
# ---------------------------------------------------------------------------

def _data() -> dict:
    return {
        SC.SECTION_OUTFITS: [
            {'char': 'lan', 'code': 'A', 'attr': 'aa', 'label': '常态'},
            {'char': 'lan', 'code': 'B', 'attr': 'base_swimsuit',
             'label': '泳装'},
            {'char': 'ye', 'code': 'B', 'attr': 'base_handdown',
             'label': '手放下'},
        ],
        SC.SECTION_EXPRESSIONS: [
            {'char': 'lan', 'no': '7', 'attr': 'indifferent', 'label': '不在意'},
            {'char': 'lan', 'no': '8', 'attr': 'angry', 'label': '生气'},
            {'char': 'ye', 'no': '7', 'attr': 'hi', 'label': 'Hi嗨'},
        ],
    }


class TestTranslate:
    def test_用户原话那一句(self):
        """`lan swim_suit angry` → 代号 + 剧本里那行。"""
        got = SC.translate('lan swim_suit angry', _data())
        assert got.ok, got.note
        assert got.code == 'B8'
        assert got.show == 'show lan base_swimsuit angry'

    def test_反过来也认(self):
        """`lan B7` → 属性。"""
        got = SC.translate('lan B7', _data())
        assert got.ok, got.note
        assert (got.outfit_attr, got.expr_attr) == ('base_swimsuit',
                                                    'indifferent')
        assert got.show == 'show lan base_swimsuit indifferent'

    def test_中间带空格也行(self):
        assert SC.translate('lan B 7', _data()).code == 'B7'

    def test_中文说明也能当输入(self):
        assert SC.translate('lan 泳装 不在意', _data()).code == 'B7'

    def test_只写编号(self):
        got = SC.translate('lan 7', _data())
        assert got.ok and got.code == '7'
        assert got.show == 'show lan indifferent'

    def test_缺字母时说清楚缺哪个(self):
        got = SC.translate('lan C7', _data())
        assert not got.ok
        assert 'C' in got.note and 'A、B' in got.note

    def test_没说角色时不瞎猜(self):
        got = SC.translate('B7', _data())
        assert not got.ok and '角色' in got.note

    def test_一个角色时可以不写角色(self):
        """表里只有一个角色就没有歧义 —— 但两个角色时**必须**写。"""
        assert SC.translate('B7', {SC.SECTION_OUTFITS: [
            {'char': 'lan', 'code': 'B', 'attr': 'base_swimsuit'}],
            SC.SECTION_EXPRESSIONS: [
            {'char': 'lan', 'no': '7', 'attr': 'indifferent'}]}).ok

    def test_空输入不炸(self):
        assert not SC.translate('', _data()).ok
        assert SC.translate(None, _data()).code == ''


class TestHelpers:
    def test_norm_把写法差异抹平(self):
        assert SC.norm('base_swimsuit') == SC.norm('swim_suit') == 'swimsuit'
        assert SC.norm('泳装') == '泳装'

    def test_chars_in_保序去重(self):
        assert SC.chars_in(_data()) == ['lan', 'ye']

    def test_template_rows_丢掉没有属性的行(self):
        scan = SC.Scan(outfits=[{'char': 'lan', 'code': 'B', 'attr': ''},
                                {'char': 'lan', 'code': 'A', 'attr': 'aa'}],
                       expressions=[])
        assert SC.template_rows(scan) == {SC.SECTION_OUTFITS: [
            {'char': 'lan', 'code': 'A', 'attr': 'aa'}],
            SC.SECTION_EXPRESSIONS: []}


@pytest.mark.parametrize('text', ['', '   ', 'lan', 'lan B', 'lan 8'])
def test_换算永远有话说(text):
    """任何输入都得给一句人话（界面上绝不出现空白结果）。"""
    got = SC.translate(text, _data())
    assert got.note or got.ok
