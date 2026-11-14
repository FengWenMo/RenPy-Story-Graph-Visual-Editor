# -*- coding: utf-8 -*-
"""
导出回执里的"缺资源 N 处"到底在数什么（2026-11-14）
====================================================

用户报："这个导出明明啥都没事……怎么还有文字会报缺资源见注释？"

两个叠在一起的毛病：

**① 回执数的是"编译期想写几条注释"，不是产物里有几条。**
增量补丁按约定**不写**缺资源说明注释 —— 那句引用原文里已经有对应行了
（`patcher._is_new_content` / `_may_drop_line`），于是"编译说 9 处、产物 0 条"
是**常态**而不是异常。用户打开文件一条都没有，回执却写着"见注释"。
真机复现（`sr_general` 42 章）：15 章回执非零、产物 0 条。

**② 判"角色缺失"只查资源表，不查项目脚本。**
`nvl_narrator` / `cat` / `duck` / `ls` … 九类说话人全在 `definitions.rpy`
里 `define` 着（`define cat = DynamicCharacter('cat_name', …)`），角色表里
没有 → 编译期判"缺少资源" → 渲染器按 D7 丢掉说话人、降级成旁白 →
**一次"什么都没改"的导出把 9~18 行说话人静默改成了旁白**
（真机 chapter1_1 9 行、chapter1_4 18 行）。用户看到的"误报"底下压着的
是一处**真的改错了**。

真源层序（`core/authority.py`）本就写着 **declared(2) > registry(4)**：
脚本里 `define` 出来的角色，比表里有没有那一行权威。

本文件钉三件事：
  · `Symbols.actor` 认得项目脚本声明过的角色（表里没有也算认得）；
  · `res['todos']` **等于产物里真有的缺资源注释条数**；
  · `res['missing_refs']` 是编译期判定的处数（前端靠它说"另一半去哪了"），
    两者**分开**报。
"""
from __future__ import annotations

from pathlib import Path

import pytest

TABLES = 'game/asset_tables'

#: 只在脚本里定义、角色表里没有的角色（真机 `definitions.rpy` 的形态）。
DEFINITIONS = '''\
define nvl_narrator = Character(None, kind=nvl, what_prefix="", what_suffix="")
define cat = DynamicCharacter('cat_name', what_prefix='"', what_suffix='"')
default cat_name = "「猫」"
'''

#: 一句已登记角色、两句"只在脚本里定义过"的角色（从前那两句会丢说话人）。
CHAPTER = '''\
label ch01:
    yhj "已登记的角色。"
    nvl_narrator "只有脚本里定义过的说话人①。"
    nvl_narrator "只有脚本里定义过的说话人②。"
    cat "动态名字的角色。"
    return
'''


def _todo_lines(text: str) -> int:
    """产物里真有几条缺资源注释（行内判定，与 `generation_guard.todo_lines` 同口径）。"""
    return sum(1 for line in text.splitlines() if '# TODO:' in line)


def _project(tmp_path, chapter=CHAPTER, definitions=DEFINITIONS,
             characters=None, music=None):
    """
    最小工程：

        proj/game/asset_tables/characters.xlsx   只有 yhj 一行
        proj/game/asset_tables/music.xlsx        只有 bgm_有.mp3 一行
        proj/game/definitions.rpy                脚本里额外 define 两个角色
        proj/game/chapter1.rpy                   正文
    """
    from storygraph.assets.asset_registry import (_TABLE_FIELDS,
                                                  write_dict_to_excel)
    root = tmp_path / 'proj'
    tables = root / TABLES
    tables.mkdir(parents=True, exist_ok=True)
    write_dict_to_excel(
        tables / 'characters.xlsx',
        characters if characters is not None else {
            'yhj': {'ID': 'yhj', 'Name': '远', 'ImageTag': 'ling'},
        },
        _TABLE_FIELDS['character'])
    write_dict_to_excel(
        tables / 'music.xlsx',
        music if music is not None else {
            'bgm_有': {'ID': 'bgm_有', 'Path': 'game/bgm/有.mp3'},
        },
        ['ID', 'Path', 'Loop', 'Volume', 'Notes'])
    if definitions:
        (root / 'game' / 'definitions.rpy').write_text(definitions,
                                                       encoding='utf-8')
    (root / 'game' / 'chapter1.rpy').write_text(chapter, encoding='utf-8')
    return root


def _session(root):
    from storygraph.app.editor import EditorSession
    from storygraph.assets.registry_load import load_asset_registry

    registry = load_asset_registry(root)
    s = EditorSession(project_root=root, registry_provider=lambda: registry)
    s.load(root / 'game' / 'chapter1.rpy')
    return s


def _export(s, tmp_path, name='out.rpy'):
    """导出到工程**外面**（不碰 game/，免得触到 label 重名那条门禁）。"""
    out = tmp_path / 'exported' / name
    out.parent.mkdir(parents=True, exist_ok=True)
    res = s.export(out)
    assert res.get('ok'), res.get('blocking')
    return res, out.read_text(encoding='utf-8')


# ---------------------------------------------------------------------------
#  ① 项目脚本声明过的角色不算缺资源
# ---------------------------------------------------------------------------

class TestCharactersDeclaredInScripts:

    def test_符号表认得脚本里_define_的角色(self, tmp_path):
        from storygraph.story.dsl import Symbols
        from storygraph.assets.registry_load import load_asset_registry

        symbols = Symbols(load_asset_registry(_project(tmp_path)))
        for name in ('nvl_narrator', 'cat'):
            assert symbols.actor(name), f'脚本里 define 过的 {name} 被判成没有'
        # 表里没有、脚本里也没有 → 仍然不许凭空认下来（铁律 4：不猜）
        assert symbols.actor('没人定义过的说话人') is None

    def test_编译期不再判缺资源(self, tmp_path):
        res = _session(_project(tmp_path)).compile()
        todo = [r['code'] for r in res.rows if r.get('status') == 'todo']
        assert res.todo_count == 0, todo

    def test_什么都没改的导出逐字节不变_说话人不被丢掉(self, tmp_path):
        root = _project(tmp_path)
        source = (root / 'game' / 'chapter1.rpy').read_text(encoding='utf-8')
        res, text = _export(_session(root), tmp_path)

        assert 'nvl_narrator "只有脚本里定义过的说话人①。"' in text, \
            '说话人被静默降级成旁白了'
        assert 'cat "动态名字的角色。"' in text
        assert text == source, '一个字都没改，产物却动了'
        assert res['todos'] == 0
        assert res['missing_refs'] == 0

    def test_真没登记的说话人照旧判缺资源(self, tmp_path):
        """反方向：确实没人 define 的角色**不许**被放过。"""
        chapter = (
            'label ch01:\n'
            '    lj "这个说话人哪都没有。"\n'
            '    return\n')
        res = _session(_project(tmp_path, chapter=chapter)).compile()
        assert res.todo_count == 1, [r['code'] for r in res.rows]


# ---------------------------------------------------------------------------
#  ② 回执只报产物里真有的；编译期的另一半分开说
# ---------------------------------------------------------------------------

class TestReceiptReportsWhatIsInTheFile:

    def test_作者原有的行没写成注释时_回执不报处数(self, tmp_path):
        """
        原文 `play music "bgm/没有这首.mp3"` 指向表里没有的曲子。

        增量补丁按约定**保留作者那一行**（不拿注释顶掉它）→ 产物里没有
        缺资源注释。回执从前报"缺资源 1 处（见注释）"，打开文件却没有。
        """
        chapter = (
            'label ch01:\n'
            '    play music "bgm/没有这首.mp3" fadein 1.0\n'
            '    yhj "一句话。"\n'
            '    return\n')
        root = _project(tmp_path, chapter=chapter)
        source = (root / 'game' / 'chapter1.rpy').read_text(encoding='utf-8')
        res, text = _export(_session(root), tmp_path)

        assert text == source, '作者原有的 play music 行被改掉了'
        assert _todo_lines(text) == 0
        assert res['todos'] == 0, '回执在报产物里没有的东西'
        assert res['missing_refs'] == 1, '编译期确实判了 1 处，这个事实不该丢'

    def test_新内容里的缺资源会写进产物_并被数到(self, tmp_path):
        """
        给一拍填一个没人 define 的立绘：那条注释是**新内容**（原文里没有
        提到这个名字），补丁会把它写进产物 → 回执必须有这一条。
        """
        root = _project(tmp_path)
        s = _session(root)
        fields = s.fields(1)
        fields['Stage'] = '[显示] 谁都不是 开心'
        s.apply(1, fields, stage_loaded=s.fields(1)['Stage'])
        res, text = _export(s, tmp_path)

        assert _todo_lines(text) >= 1, text
        assert res['todos'] == _todo_lines(text), \
            '回执条数与产物里的注释条数必须一致'
        assert res['todos'] >= 1

    def test_回执条数永远等于产物里的注释条数(self, tmp_path):
        """三种工程一起过一遍：`todos` 不许凭空冒出，也不许漏报。"""
        cases = {
            '干净': _project(tmp_path / 'a'),
            '作者行保留': _project(
                tmp_path / 'b', chapter=(
                    'label ch01:\n'
                    '    play music "bgm/没有这首.mp3"\n'
                    '    yhj "一句话。"\n'
                    '    return\n')),
        }
        for name, root in cases.items():
            res, text = _export(_session(root), tmp_path / f'out_{name}')
            assert res['todos'] == _todo_lines(text), name


# ---------------------------------------------------------------------------
#  ③ 声明读取的缓存：改了脚本就该重新读（不许给用户看旧结论）
# ---------------------------------------------------------------------------

class TestDeclaredCharactersCache:

    def test_新增一个_define_之后立刻认得(self, tmp_path):
        from storygraph.story.dsl import Symbols
        from storygraph.assets.registry_load import load_asset_registry

        root = _project(tmp_path, definitions='')
        symbols = Symbols(load_asset_registry(root))
        assert symbols.actor('momo') is None

        (root / 'game' / 'definitions.rpy').write_text(
            'define momo = Character("默默")\n', encoding='utf-8')
        fresh = Symbols(load_asset_registry(root))
        assert fresh.actor('momo'), '缓存没按文件指纹失效'

    def test_没有项目根时是空集_不扫当前目录(self):
        from storygraph.story.script_characters import declared_characters

        assert declared_characters('') == frozenset()
        assert declared_characters(None) == frozenset()
