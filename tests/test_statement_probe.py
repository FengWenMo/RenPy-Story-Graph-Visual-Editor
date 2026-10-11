"""
"把工具不认识的写法**直接问引擎**"的判据（方案 §三 的"扫描喂给探针"）
=====================================================================

用户 2026-10-11："做实现！"

链路：`discover_undeclared` 扫出"工具不认识的写法" → **丢给引擎跑一遍**
→ 引擎回答"认不认识 / 什么错" → 结论进 `engine_facts.derived.undeclared_heads`。

这个文件钉住**纯函数**那一半（真机那一半见 `tools/probe_statement_ask.py`）。
判据几乎每一条都对应一个**真机实测踩到的坑**，注释里都写了原委。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.pipeline import statement_probe as SP                  # noqa: E402


# ==================== ① 形态与源码 ====================

def test_variants_cover_the_three_questions():
    got = SP.ask_variants('weather')
    assert [v['what'] for v in got] == ['bare', 'block', 'params']
    assert [v['file'] for v in got] == ['bare', 'block', 'params'], \
        '每个形态一个文件（引擎对一个文件只报第一处 parse error）'
    assert got[0]['lines'] == ['weather sg_arg1']


def test_custom_lines_override_the_defaults():
    got = SP.ask_variants('weather', lines=['weather rain'])
    assert [v['what'] for v in got] == ['custom']
    assert got[0]['lines'] == ['weather rain']


def test_variant_source_is_a_parseable_label():
    src = SP.variant_source('weather', SP.ask_variants('weather')[0])
    assert SP.ASK_MARK in src
    assert 'label _sg_ask_weather_bare:' in src
    assert '    weather sg_arg1' in src          # 缩进 4 格
    assert src.rstrip().endswith('return'), 'label 必须有终点'


def test_variant_source_keeps_a_block_indented():
    v = SP.ask_variants('weather')[1]
    src = SP.variant_source('weather', v)
    assert '    weather sg_arg1:' in src
    assert '        pass' in src, '块体要再缩进一层，否则 label 结构就错了'


def test_filename_is_derived_from_the_variant():
    v = SP.ask_variants('weather')[0]
    assert SP.ask_filename(v) == 'zz_storygraph_ask_bare.rpy'


def test_weird_heads_do_not_break_the_label_name():
    """语句头里可能有 `-` / 别的符号 → label 名要洗干净，不能生成非法 label。"""
    v = SP.ask_variants('weather-2')[0]
    src = SP.variant_source('weather-2', v)
    assert 'label _sg_ask_weather_2_bare:' in src


# ==================== ② 读结论（真机 lint 的真实形状） ====================

#: 真机实测的报错形状：**报错行 + 紧随的源码行**（语句头在下一行）
_REAL = '''Ren'Py 8.5.3.26051504 lint report

game/chapter/chapter1_2.rpy:282 'lan sideeye' is not an image.

File "game/zz_storygraph_ask_bare.rpy", line 4: expected statement.
    weather sg_arg1

Statistics:
'''


def test_rejected_head_is_read_from_the_report():
    v = SP.ask_variants('weather')
    got = SP.read_verdict('weather', _REAL, variants=v, exit_code=1)
    assert got['ok'] and got['recognized'] is False
    assert 'expected statement' in got['evidence'][0]
    assert '不认识' in SP.summarize(got)


def test_other_files_errors_are_not_mistaken_for_ours():
    """⚠ 报告是**全项目**的（这个工程本来就有语义错）—— 别人的错一概不算。"""
    v = SP.ask_variants('weather')
    only_others = ('game/chapter/chapter1_2.rpy:282 '
                   "'lan sideeye' is not an image.\n")
    got = SP.read_verdict('weather', only_others, variants=v, exit_code=1)
    assert got['ok'] and got['recognized'] is True, got


def test_a_rejection_of_a_different_head_does_not_count():
    """
    ⚠ 只认**指向我们那个探针文件**、且**紧随的源码行是我们写的那句**的报错。
    拿别人（同一文件里别的形态）的报错来定罪，就会冤枉一个合法的头。
    """
    v = [{'what': 'bare', 'file': 'bare', 'lines': ['weather sg_arg1']}]
    text = ('File "game/zz_storygraph_ask_bare.rpy", line 4: expected statement.\n'
            '    totally_other_thing x\n')
    got = SP.read_verdict('weather', text, variants=v, exit_code=1)
    assert got['recognized'] is True, got


def test_blocks_is_none_when_the_head_itself_is_rejected():
    """
    ⚠⚠ 真机实测的关键一条：**带 `:` 的未知头会被引擎接受**
    （`sg_definitely_unknown_xyz sg_arg1:` + 缩进体 = 解析通过）。
    所以 `bare` 被拒时，`block` 的"没报错"**什么也证明不了** ——
    必须留 `None`（没测出来），**绝不拿"没报错"冒充"支持块"**。
    """
    v = SP.ask_variants('weather')
    got = SP.read_verdict('weather', _REAL, variants=v, exit_code=1)
    assert got['recognized'] is False
    assert got['blocks'] is None and got['params'] is None, got
    assert got['by_what']['block'].get('unconfirmed') is True


def test_blocks_and_params_are_read_when_the_head_is_accepted():
    """头被接受时，另外两个形态的"没报错"才算数。"""
    v = SP.ask_variants('weather')
    block_text = ('File "game/zz_storygraph_ask_block.rpy", line 4: '
                  'expected statement.\n    weather sg_arg1:\n')
    got = SP.read_verdict('weather', block_text, variants=v, exit_code=1)
    assert got['recognized'] is True
    assert got['blocks'] is False, '带块那条被拒 → 不支持块'
    assert got['params'] is True, 'params 那条没被提 → 接受'


def test_weird_exit_code_is_not_taken_as_a_conclusion():
    """⚠ 引擎没正常跑完 → 说不准，**不能说"引擎说没问题"**（方案坑 4）。"""
    v = SP.ask_variants('weather')
    got = SP.read_verdict('weather', '', variants=v, exit_code=143)
    assert got['ok'] is False and got['recognized'] is None
    assert '说不准' in SP.summarize(got) or '退出码' in got['reason']


def test_empty_head_is_refused():
    got = SP.read_verdict('', _REAL)
    assert got['ok'] is False and '没有语句头' in got['reason']


# ==================== ③ 轻量副本（8GB 项目不能整份拷） ====================

def test_copy_for_ask_skips_heavy_assets(tmp_path):
    """
    ⚠⚠ 真机事故判据：整份 `copytree` 一个 **8GB** 项目，跑五个待测写法就
    **写满了整个 C 盘**（实测踩到）。所以副本只拷"能解析就行"的东西：
    `.rpy`/`.json`/`.txt`，跳过图像/音频/字体/缓存这些重目录。
    """
    root = tmp_path / 'proj'
    (root / 'game' / 'chapter').mkdir(parents=True)
    (root / 'game' / 'chapter' / 'ch01.rpy').write_text(
        'label ch01:\n    return\n', encoding='utf-8')
    (root / 'game' / 'images' / 'characters').mkdir(parents=True)
    big = root / 'game' / 'images' / 'characters' / 'a.png'
    big.write_bytes(b'\x89PNG' + b'x' * (5 * 1024 * 1024))     # 5MB 的"图"
    (root / 'game' / 'bgm').mkdir(parents=True)
    (root / 'game' / 'bgm' / 'a.ogg').write_bytes(b'OggS' + b'y' * 1000)
    (root / 'storygraph.rules.json').write_text('{}', encoding='utf-8')

    work = SP._copy_for_ask(root, tmp_path / 'out')
    assert (work / 'game' / 'chapter' / 'ch01.rpy').is_file(), '脚本必须拷'
    assert (work / 'game').is_dir()
    assert not (work / 'game' / 'images').exists(), '重目录（图）不该被拷'
    assert not (work / 'game' / 'bgm').exists(), '重目录（音频）不该被拷'
    total = sum(p.stat().st_size for p in work.rglob('*') if p.is_file())
    assert total < 1024 * 1024, f'副本应当很小，实际 {total} 字节'


def test_copy_for_ask_keeps_the_directory_shape(tmp_path):
    """空目录也要建：Ren'Py 扫描 `game/` 依赖目录存在。"""
    root = tmp_path / 'proj'
    (root / 'game' / 'sub' / 'deep').mkdir(parents=True)
    (root / 'game' / 'a.rpy').write_text('label a:\n    return\n', encoding='utf-8')
    work = SP._copy_for_ask(root, tmp_path / 'out')
    assert (work / 'game' / 'sub' / 'deep').is_dir()


def test_ask_engine_refuses_a_non_project(tmp_path):
    """不是工程根 → 如实说，**不去猜**（也不拷任何东西）。"""
    got = SP.ask_engine(tmp_path, 'weather')
    assert got['ok'] is False
    assert '不是工程根' in got['reason']
    assert got['recognized'] is None, '拒绝下结论'


# ==================== ④ 写探针的老规矩 ====================

def test_write_ask_refuses_to_overwrite_someone_elses_file(tmp_path):
    """
    ⚠ 与 E1 的 `_write_probe` 同一规矩：**绝不覆盖别人的文件**。
    我们永远写在副本里，正常走不到这一支 —— 但规矩不能少。
    """
    target = tmp_path / 'zz_storygraph_ask_bare.rpy'
    target.write_text('# 用户自己写的，别动我\n', encoding='utf-8')
    with pytest.raises(FileExistsError):
        SP._write_ask(target, SP.ASK_MARK + '\nlabel x:\n    return\n')
    assert '别动我' in target.read_text(encoding='utf-8')


# ==================== ⑤ 引擎被挡住时**不许编结论** ====================

def test_matching_parse_error_finds_the_blocking_file():
    """
    ⚠⚠ 真机实测（我第一版就是在这里假绿的）：引擎报**第一处** parse error 就停。
    如果那一处在**用户自己的文件**里，我们连探针都没被解析到 ——
    这时"lint 没提到探针"**绝不等于**"探针被接受"。
    """
    text = ('File "game/zz_demo_custom_actions.rpy", line 2: expected statement.\n'
            '    weather rain 强度=3\n')
    got = SP._matching_parse_error(text, SP.ASK_PREFIX)
    assert 'zz_demo_custom_actions.rpy' in got, got


def test_matching_parse_error_ignores_our_own_probe():
    """报错在**我们自己的探针**里 → 那不是"被挡住"，那正是我们问的答案。"""
    text = ('File "game/zz_storygraph_ask_bare.rpy", line 4: expected statement.\n'
            '    weather sg_arg1\n')
    assert SP._matching_parse_error(text, SP.ASK_PREFIX) == ''


def test_matching_parse_error_ignores_semantic_errors():
    """语义错（`is not an image`）不是 parse error，不影响问。"""
    text = "game/chapter/chapter1_2.rpy:282 'lan sideeye' is not an image.\n"
    assert SP._matching_parse_error(text, SP.ASK_PREFIX) == ''


def test_ask_many_says_who_blocked_it(tmp_path, monkeypatch):
    """
    项目自己有 parse error → `ok=False` + **说清被谁挡住**；
    既不返回"认识"，也不返回"不认识"（**不猜**）。
    """
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch01.rpy').write_text(
        'label ch01:\n    weather rain\n    return\n', encoding='utf-8')
    blocked_text = ('File "game/ch01.rpy", line 2: expected statement.\n'
                    '    weather rain\n')

    def fake_lint(work, sdk_path, timeout):
        return 1, blocked_text

    def fake_find_sdk(explicit=None):
        return tmp_path / 'sdk'                # 只要不抛就行（lint 被替换）

    import storygraph.pipeline.engine_collect as EC
    monkeypatch.setattr(EC, 'find_sdk', fake_find_sdk)
    monkeypatch.setattr(SP, '_lint', fake_lint)
    got = SP.ask_many(root, ['weather'], sdk=tmp_path / 'sdk', timeout=5)
    assert got['ok'] is False
    assert 'ch01.rpy' in got['blocked_by'], got
    assert '问不到' in got['reason'] or 'parse error' in got['reason']
    assert got['results'] == {}, '被挡住时一个结论都不许给'
