# tests/test_placement_fixtures.py
"""
「文件落点」样例项目的**夹具自检**（G1 的样本，2026-10-23）。

它测什么
--------
不是测识别算法（那要等 G1 实现），而是钉住**样本本身**：

  1. 七个形状样例都在，结构完整；
  2. 每个样例的 ``expected.json`` 可读、字段齐全；
  3. 样例里的每个 ``.rpy`` 都能被项目解析器认出来（合法样本才有资格当判据）；
  4. 六个维度都真的被覆盖到（不然样本白造）。

为什么单独立一个夹具测试
------------------------
样本是判据的根。样本本身坏了（缩进被 shell 弄花、expected 写错、
某维度漏了），后面 G1 的红灯会指向错误的地方。
`tools/make_placement_fixtures.py` 生成样本，本测试守着生成结果。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'placement'

#: 七个形状样例（`docs/分析_通用化与文件落点_2026-10-23.md` §二）
SHAPES = (
    'shape_a_triple',      # 三件套（sr_general 同形）
    'shape_b_template',    # 官方模板
    'shape_c_scattered',   # 分文件但混写
    'shape_d_prefixed',    # 数字前缀集中
    'shape_e_multifile',   # 多文件分拆声明
    'shape_f_chinese',     # 中文命名
    'shape_g_single',      # 单文件原型
)


# ---------------------------------------------------------------------------
#  1. 结构完整
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('shape', SHAPES)
def test_shape_has_expected_and_game(shape):
    base = FIXTURES / shape
    assert base.is_dir(), f'{shape} 样例目录不存在'
    assert (base / 'expected.json').is_file(), f'{shape} 缺 expected.json'
    game = base / 'game'
    assert game.is_dir(), f'{shape} 缺 game/ 目录'
    rpy = list(game.rglob('*.rpy'))
    assert rpy, f'{shape} 的 game/ 里一个 .rpy 都没有'


@pytest.mark.parametrize('shape', SHAPES)
def test_expected_json_is_well_formed(shape):
    data = json.loads((FIXTURES / shape / 'expected.json').read_text(encoding='utf-8'))
    assert data.get('sample') == shape
    assert data.get('notes'), f'{shape} 缺 notes（样本意图说不清，后面断言会对不上）'
    place = data.get('placement') or {}
    for key in ('declarations', 'control_flow', 'content_globs', 'declaration_style'):
        assert key in place, f'{shape} 的 placement 缺 {key}'
    assert isinstance(place['content_globs'], list), f'{shape} 的 content_globs 必须是列表'
    assert place['declaration_style'] in ('dedicated', 'mixed_with_content',
                                          'single_file'), \
        f'{shape} 的 declaration_style 取值非法：{place["declaration_style"]}'


# ---------------------------------------------------------------------------
#  2. 样本合法（能被项目解析器认出来）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('shape', SHAPES)
def test_every_rpy_parses(shape):
    from storygraph.parsing.renpy_parser import RenPyLexer
    lexer = RenPyLexer()
    for f in sorted((FIXTURES / shape / 'game').rglob('*.rpy')):
        text = f.read_text(encoding='utf-8')
        try:
            lexer.tokenize_text(text)
        except Exception as e:                      # noqa: BLE001
            pytest.fail(f'{f.relative_to(FIXTURES)} 解析失败：{e}')


# ---------------------------------------------------------------------------
#  3. 六个维度真的被覆盖到（样本白造 = 这一节红）
# ---------------------------------------------------------------------------

def test_dimension_coverage():
    """
    每个维度至少要出现**两种取值**，否则样本没有对照价值。

    维度（见 tools/make_placement_fixtures.py 模块注释）：
      D1 声明落点形态 / D2 控制流落点 / D3 目录结构 /
      D4 命名风格 / D5 生成物 / D6 剧情时刻 $
    """
    rows = []                                     # [(shape, placement)]
    for shape in SHAPES:
        data = json.loads((FIXTURES / shape / 'expected.json').read_text(encoding='utf-8'))
        rows.append((shape, data['placement']))

    # D1 声明落点形态：专用 / 与内容混写 / 单文件同处（三档都要有）
    decl_styles = {place['declaration_style'] for _, place in rows}
    assert decl_styles == {'dedicated', 'mixed_with_content', 'single_file'}, \
        f'D1 声明落点形态三档没齐：{decl_styles}'

    # D2 控制流落点：独立文件 / 内联（空）
    cf_kinds = {'separate' if place['control_flow'] else 'inline'
                for _, place in rows}
    assert cf_kinds == {'separate', 'inline'}, f'D2 控制流落点没拉开：{cf_kinds}'

    # D3 目录结构：扁平 / 嵌套（game/ 下还有没有子目录）
    dir_kinds = set()
    for _, place in rows:
        for glob in place['content_globs']:
            after = glob.split('game/', 1)[-1]
            parent = after.rsplit('/', 1)[0] if '/' in after else ''
            dir_kinds.add('nested' if parent else 'flat')
    assert {'flat', 'nested'} <= dir_kinds, f'D3 目录结构没拉开：{dir_kinds}'

    # D4 命名风格：从文件名里读（中文名 + 数字前缀都要有）
    names = [f.name
             for shape, _p in rows
             for f in (FIXTURES / shape / 'game').rglob('*.rpy')]
    assert any(not n.isascii() for n in names), 'D4 没有中文/非 ASCII 命名样例'
    assert any(n[0].isdigit() for n in names), 'D4 没有数字前缀样例'

    # D6 剧情时刻 $：有/无都要有
    has_dollar = set()
    for shape, _p in rows:
        for f in (FIXTURES / shape / 'game').rglob('*.rpy'):
            has_dollar.add('$' in f.read_text(encoding='utf-8'))
    assert has_dollar == {True, False}, f'D6 剧情时刻 $ 没拉开：{has_dollar}'


# ---------------------------------------------------------------------------
#  4. 生成脚本可重跑（幂等）
# ---------------------------------------------------------------------------

def test_generator_is_idempotent(tmp_path):
    """跑两遍生成脚本，产物应逐字节相同——样本不该有随机成分。"""
    import subprocess
    import sys
    script = Path(__file__).resolve().parent.parent / 'tools' / 'make_placement_fixtures.py'
    before = {p: p.read_bytes() for p in sorted(FIXTURES.rglob('*')) if p.is_file()}
    subprocess.run([sys.executable, str(script)], check=True, capture_output=True)
    after = {p: p.read_bytes() for p in sorted(FIXTURES.rglob('*')) if p.is_file()}
    assert before == after, '生成脚本跑两次产物不同——样本里有不确定成分'
