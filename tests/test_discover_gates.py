"""
发现闸门判据（V2-1）：**不许把内置关键字当成"工具还不认识"**
===============================================================

用户 2026-10-10 截图评审：

> 而且 with，window,pause 一类的不都是默认的关键字吗？emm……你确定你这个工具的
> 通用性足够强？？？

一查发现这已不是体验问题，而是**工具在教错人**：真项目根目录下有一份被这个界面
误导着写出来的声明 ——

    {"statements": [{"name": "pause", "label": "等待", ...}]}

而 `pause` 是 Ren'Py **内置**语句（`pause 1.0`，时长本来就是它的参数），
**根本不需要声明**。所以这个文件钉住三件事：

  ① **内置语句不进"待认识"列表**（连"工具自带参数的叶语句"也不进）；
  ② **已经声明的内置语句要被指出来**（"这个其实不用声明"）+ 能**安全移除**；
  ③ 说话人/台词被误读的行（`l oh "3"`）要**标成"疑似写错"**，不叫人去声明它。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.core.vocabulary import RENPY_KNOWN_HEADS               # noqa: E402
from storygraph.story import statement_registry as SR                 # noqa: E402
from storygraph.story.beat_model import LEAF_ARG_HEADS                # noqa: E402

#: 故意混入一堆**内置**语句 + 一行"说话人写错了"的台词 + 一个真自定义动作
SRC = '''label ch01:
    scene bg room
    show lyj happy
    hide lyj
    with dissolve
    play music "a.ogg"
    queue music "b.ogg"
    stop music
    voice "v.ogg"
    window hide
    pause 1.0
    lyj "台词一"
    l oh "3"
    swim_suit lyj "泳装" mood=happy
    if love > 3:
        swim_suit lyj "校服"
    menu:
        "选项一":
            jump ch02
        "选项二":
            pass
    return
'''


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch.rpy').write_text(SRC, encoding='utf-8')
    SR.clear_cache()
    yield root
    SR.clear_cache()


# ==================== ① 内置语句不进列表 ====================

@pytest.mark.parametrize('head', ['with', 'window', 'pause', 'play', 'queue',
                                  'stop', 'voice', 'scene', 'show', 'hide',
                                  'menu', 'jump', 'if', 'return', 'pass'])
def test_builtin_statements_are_never_suggested(project, head):
    """
    用户原话："with，window,pause 一类的不都是默认的关键字吗？" —— 对，所以
    它们**一个都不该**出现在"要认识哪个动作"里。
    """
    assert head in RENPY_KNOWN_HEADS, f'{head} 应当是词表里的已知语句头'
    heads = {r['head'] for r in SR.discover_undeclared(project)}
    assert head not in heads, f'{head} 是内置语句，不该出现在待声明列表里'


def test_leaf_statements_the_tool_already_declared_are_not_suggested(project):
    """`with/pause/window` 是**工具自己声明过参数**的叶语句 —— 更不该叫用户声明。"""
    heads = {r['head'] for r in SR.discover_undeclared(project)}
    for head in LEAF_ARG_HEADS:
        assert head not in heads, head


def test_only_the_real_custom_action_shows_up(project):
    heads = [r['head'] for r in SR.discover_undeclared(project)]
    assert heads == ['swim_suit'], heads
    rec = SR.discover_undeclared(project)[0]
    assert rec['count'] == 2, '用了两次（一次在 if 里）也要数对'


# ==================== ② 已经声明的内置语句 ====================

def test_declared_builtins_are_pointed_out(project):
    """
    真项目里那条 `pause` 就是被这个界面误导写出来的 —— 现在要能认出来。
    """
    SR.declare_statement(project, 'pause', label='等待')
    SR.declare_statement(project, 'swim_suit', label='泳装切换')
    SR.clear_cache()
    reg = SR.StatementRegistry.for_project(project)
    flagged = SR.declared_builtins(reg)
    assert [f['name'] for f in flagged] == ['pause'], flagged
    assert '不用声明' in flagged[0]['reason'] or '内置' in flagged[0]['reason']
    assert flagged[0]['label'] == '等待'


def test_no_declared_builtins_means_empty(project):
    SR.declare_statement(project, 'swim_suit')
    SR.clear_cache()
    assert SR.declared_builtins(
        SR.StatementRegistry.for_project(project)) == []


# ==================== ③ 疑似写错的行 ====================

def test_a_misread_speaker_line_is_flagged_not_suggested(project):
    """
    `l oh "3"` 是**台词**被误读成动作（真项目 `script.rpy:94` 里就有这一行）。

    ⚠ 我第一版把它写成"说话人写错了"—— **骂错了**：`l` 在真项目角色表里是
    角色「蓝」的 ID，这行就是台词。判据要求：
      · 默认那一栏里**不出现**它（不叫人去声明一个动作）；
      · 单独查时给出的原因是"**这是台词**"，而不是指控人写错。
    """
    found = {r['head']: r for r in SR.discover_undeclared(project,
                                                          include_suspect=True)}
    assert 'swim_suit' in found and not found['swim_suit']['suspect']
    assert found['l']['suspect'], '疑似项要带上原因'
    assert 'l' not in {r['head'] for r in SR.discover_undeclared(project)}, \
        '默认那一栏里不许混进疑似项'
    suspects = SR.discover_undeclared(project, include_suspect=True,
                                      only_suspect=True)
    assert [s['head'] for s in suspects] == ['l'], suspects
    assert '台词' in suspects[0]['reason'], suspects[0]['reason']
    assert 'oh "3"' in (suspects[0]['samples'][0][1] if suspects[0]['samples']
                        else ''), suspects[0]['samples']


def test_a_real_character_line_is_reported_as_dialogue_not_a_typo(tmp_path):
    """
    真项目实测（2026-10-10）：`l oh "3"` 里的 `l` 是角色表里的角色 ID
    → 它必须是"**这是台词**"，**不许**说成"你可能写错了"。
    """
    from storygraph.assets.asset_registry import (_TABLE_FIELDS,          # noqa: E402
                                                 write_dict_to_excel)
    root = tmp_path / 'proj'
    adir = root / 'game' / 'asset_tables'
    adir.mkdir(parents=True)
    write_dict_to_excel(adir / 'characters.xlsx', {
        'l': {'ID': 'l', 'Name': '「蓝」', 'Variable': 'l'},
    }, _TABLE_FIELDS['character'])
    (root / 'game' / 'script.rpy').write_text(
        'label ch01:\n    l oh "3"\n    return\n', encoding='utf-8')
    SR.clear_cache()
    got = SR.discover_undeclared(root, include_suspect=True, only_suspect=True)
    assert [g['head'] for g in got] == ['l'], got
    reason = got[0]['reason']
    assert '台词' in reason, reason
    assert '写错' not in reason and '不对' not in reason, (
        f'角色表里有的角色，行里有台词 → 这是对白，不该指控人写错：{reason}')


def test_suspect_needs_both_shortness_and_a_quoted_sample(project):
    """
    只"名字短"不够（可能是真动作 `x`）；只"样例带引号"也不够（`swim_suit`
    也带引号）。**两个条件同时成立**才叫疑似写错 —— 免得把真动作误判成写错。
    """
    found = {r['head']: r for r in SR.discover_undeclared(project,
                                                          include_suspect=True)}
    assert 'swim_suit' in found and not found['swim_suit']['suspect']


def test_suspect_is_excluded_by_default(project):
    """默认调用（列表页）里**不出现**疑似写错的项 —— 它们走另一栏。"""
    assert 'l' not in {r['head'] for r in SR.discover_undeclared(project)}


# ==================== ④ 来源文件（模板/界面文件降权） ====================

def test_each_candidate_says_where_it_came_from(tmp_path):
    """
    `with x39` 那种高计数大多来自 Ren'Py **模板/界面文件**（`screens.rpy` 等）。
    列表要能显示"来自哪个文件"，人才看得懂为什么会有这个数。
    """
    root = tmp_path / 'proj'
    game = root / 'game'
    game.mkdir(parents=True)
    (game / 'ch01.rpy').write_text(
        'label ch01:\n    mycue 1\n    return\n', encoding='utf-8')
    (game / 'screens.rpy').write_text(
        'screen foo:\n    mycue 2\n', encoding='utf-8')
    SR.clear_cache()
    rec = next(r for r in SR.discover_undeclared(root)
               if r['head'] == 'mycue')
    # ⚠ 样例里的第一项是 `文件名:行号`（不是纯路径）—— 直接 Path(str).name
    #    会得到 `ch01.rpy:2` 这种带行号的串（判据当场抓到过）。
    assert rec['files'], '要带来源文件清单'
    assert 'screens.rpy' in rec['files'] or 'ch01.rpy' in rec['files'], rec['files']
    assert all(':' in where for where, _t in rec['samples']), rec['samples']


# ==================== ⑤ 移除声明（安全，可回退） ====================

def test_remove_statement_takes_the_name_out(project):
    SR.declare_statement(project, 'pause', label='等待')
    SR.declare_statement(project, 'swim_suit')
    SR.clear_cache()
    res = SR.remove_statement(project, 'pause')
    assert res['ok'] and res['removed'], res
    SR.clear_cache()
    data = json.loads((project / 'storygraph.rules.json').read_text(
        encoding='utf-8'))
    assert [s['name'] for s in data['statements']] == ['swim_suit']


def test_remove_statement_keeps_the_file_when_other_keys_remain(project):
    (project / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{'name': 'pause'}],
        'attr_groups': [{'name': 'outfit', 'label': '服装',
                         'options': ['swim_suit']}],
        '我的备注': '别弄丢我',
    }, ensure_ascii=False), encoding='utf-8')
    SR.clear_cache()
    res = SR.remove_statement(project, 'pause')
    assert res['ok'] and res['removed']
    data = json.loads((project / 'storygraph.rules.json').read_text(
        encoding='utf-8'))
    assert data['statements'] == []
    assert data['我的备注'] == '别弄丢我'
    assert data['attr_groups'][0]['name'] == 'outfit'


def test_remove_statement_deletes_an_otherwise_empty_manifest(project):
    """清单里只剩这一条、也没有别的段 → 删掉整个文件（回退到"项目没声明"）。"""
    SR.declare_statement(project, 'pause')
    SR.clear_cache()
    path = project / 'storygraph.rules.json'
    assert path.exists()
    res = SR.remove_statement(project, 'pause')
    assert res['ok'] and res['removed'] and res['deleted_file']
    assert not path.exists()


def test_remove_statement_makes_a_backup_first(project):
    """删任何东西之前**先备份**（可回退）—— 用户手写的内容不许不可逆地消失。"""
    (project / 'storygraph.rules.json').write_text(json.dumps({
        'statements': [{'name': 'pause', 'label': '等待', 'docs': '我手写的'}],
    }, ensure_ascii=False), encoding='utf-8')
    SR.clear_cache()
    res = SR.remove_statement(project, 'pause')
    assert res['ok'] and res['backup'], res
    backup = Path(res['backup'])
    assert backup.is_file()
    saved = json.loads(backup.read_text(encoding='utf-8'))
    assert saved['statements'][0]['docs'] == '我手写的', '备份要是**改之前**的内容'


def test_remove_statement_is_honest_when_the_name_is_not_there(project):
    SR.declare_statement(project, 'swim_suit')
    SR.clear_cache()
    res = SR.remove_statement(project, 'pause')
    assert res['ok'] and not res['removed']
    assert '没有' in res['reason']


def test_remove_statement_refuses_a_broken_manifest(project):
    bad = project / 'storygraph.rules.json'
    bad.write_text('{ 这不是 JSON', encoding='utf-8')
    res = SR.remove_statement(project, 'pause')
    assert not res['ok'] and 'JSON' in res['reason']
    assert bad.read_text(encoding='utf-8') == '{ 这不是 JSON'
