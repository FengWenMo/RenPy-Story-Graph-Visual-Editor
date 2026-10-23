"""
tests/test_fidelity_structure.py
================================
把"真机长什么样"搬进仓库：真实结构样本的**可移植**保真判据。

为什么需要这个文件：
    能移植的那条逐字节用例（`test_incremental_export.py::test_unchanged_file_is_byte_identical`）
    输入只有 9 行简单对白 —— 没有 `python:` 块、没有 `menu`、没有 `transform`、
    没有 BOM / CRLF。这些结构的覆盖**全部**藏在 `skipif(REAL)` 后面：
    换一台机器，本仓库最硬的那句承诺就没有证据了。
    （实测：真机 55/55 逐字节相同；但那条证据只在我这台机器上成立。）

判据三层，缺一层都能糊弄过去：
    ① **样本自检** —— 这份样本真的带着它声称的结构，不许悄悄退化；
    ② **往返逐字节相同**（剥掉生成标记后），且必须**真的走了增量补丁**
       （全量重生成碰巧一样不算数 —— 那说明补丁根本没跑）；
    ③ **顶层语句不丢**（`opaque_loss` 为空）—— 防"字节相同但结构没进去"。

另外两条（`docs/路线图_下一步执行.md` 步骤 2 点名没有测试的分支）：
    · **拒绝分支**：补丁配不上 + 目标是用户手写文件 → 一个字都不写 + 给出 block 级原因；
    · **已知缺口**：菜单选项带 `if` 条件时补丁会退回全量（真机里恰好一条都没有）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.pipeline.compile import compile_table          # noqa: E402
from storygraph.story.generation_guard import opaque_loss      # noqa: E402
from storygraph.story.script_table import open_rpy_as_table    # noqa: E402
from tools.audit_byte_exact import strip_banner                # noqa: E402
from tools.fidelity_report import (                            # noqa: E402
    CONTENT, IDENTICAL, LAYOUT, classify, roundtrip,
)

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'fidelity'

#: 每份样本 **必须带着** 的结构（自检用）。写成正则而不是"看注释"：
#: 样本被谁顺手改简单了，这里立刻红。
CASES = {
    '01_python_block.rpy': [
        r'(?m)^init python\s*:',          # 顶层 init python 块
        r'(?m)^\s{4}python\s*:',          # label 里的 python 块
        r'\S\s+#',                        # 行尾内联注释
        r'(?m)^\s*\$ ',                   # 脚本层赋值
    ],
    '02_menu_nested.rpy': [
        r'(?m)^\s{4}menu\s*:',            # label 里的 menu
        r'(?m)^\s{12}menu\s*:',           # 选项体里再套 menu
        r'(?m)^\s*jump \w',               # 选项体里的跳转
    ],
    '03_atl_transform.rpy': [
        r'(?m)^transform \w+',            # 顶层 transform
        r'(?m)^\s{4}block:',              # ATL 的 block
        r'(?m)^\s{4}repeat \w',           # ATL 的 repeat
        r'(?m)^\s{4}with \w',             # with 语句
    ],
    '04_conditional_option.rpy': [
        r'(?m)^\s{8}"[^"]+"\s+if\s+\S+:',  # 带 if 条件的菜单选项
        r'(?m)^\s{4}menu\s*:',             # 菜单
        r'(?m)^\s+jump \w',                # 选项体里的跳转
    ],
}


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def _roundtrip(src: Path, target: Path, *, force: bool = True):
    """走生产那条路：`.rpy → 表 → 带 --source 写回`。"""
    xlsx = target.parent / 'roundtrip.xlsx'
    open_rpy_as_table(str(src), str(xlsx))
    _p, _s, res = compile_table(
        str(xlsx), rpy_path=target, write_sheet=False, force=force,
        source_path=str(src), skip_empty=True, engine_lint=False)
    return res


def _diff_message(src_text: str, out_text: str) -> str:
    import difflib
    return '\n'.join(difflib.unified_diff(
        src_text.splitlines(), out_text.splitlines(),
        '原文', '产物', lineterm='', n=2))


class TestFixturesCarryTheirStructure:
    """判据①：样本不许悄悄退化成"干净的简单对白"。"""

    @pytest.mark.parametrize('name, patterns', list(CASES.items()))
    def test_the_fixture_really_has_the_structure(self, name, patterns):
        import re
        text = _read(FIXTURES / name)
        missing = [p for p in patterns if not re.search(p, text)]
        assert not missing, f'{name} 不再带着这些结构：{missing}'
        # 台词必须是占位（隐私底线：仓库里不许出现真机台词）
        assert '台词' in text, f'{name} 应当用占位台词'


class TestPortableByteFidelity:
    """判据②③：可移植的逐字节判据 —— 不 skip、不依赖真机项目。"""

    @pytest.mark.parametrize('name', list(CASES))
    def test_roundtrip_is_byte_identical(self, name, tmp_path):
        src = FIXTURES / name
        out = tmp_path / name
        res = _roundtrip(src, out)
        assert res.written, f'{name} 没写出产物：{res.patch_note}'
        src_body, _ = strip_banner(src.read_bytes())
        out_body, _ = strip_banner(out.read_bytes())
        assert out_body == src_body, (
            f'{name} 往返后不是逐字节相同：\n'
            + _diff_message(src_body.decode('utf-8'), out_body.decode('utf-8')))

    @pytest.mark.parametrize('name', list(CASES))
    def test_it_really_went_through_the_patch(self, name, tmp_path):
        """
        **必须真的走增量**：全量重生成碰巧字节相同，不等于"没碰用户的行"。
        这条防的是"补丁悄悄没跑，用例却绿了"。
        """
        res = _roundtrip(FIXTURES / name, tmp_path / name)
        assert res.used_patch, (
            f'{name} 没走增量补丁（{res.patch_note}）—— 逐字节相同可能只是碰巧')

    @pytest.mark.parametrize('name', list(CASES))
    def test_no_top_level_statement_is_lost(self, name, tmp_path):
        """判据③：`define` / `init python` / `transform` 这些顶层块必须都在。"""
        src = FIXTURES / name
        out = tmp_path / name
        _roundtrip(src, out)
        lost = opaque_loss(_read(src), _read(out))
        assert not lost, f'{name} 丢了顶层语句：{lost}'


class TestLayoutAttributesSurvive:
    """字节层面的载体属性：BOM、CRLF、行尾空格、结尾无换行。"""

    def test_bom_and_crlf_survive(self, tmp_path):
        """真机 5 个文件带 BOM、51 个 CRLF —— 这两种载体不许被"标准化"掉。"""
        src = tmp_path / 'bom_crlf.rpy'
        body = _read(FIXTURES / '01_python_block.rpy').replace('\n', '\r\n')
        src.write_bytes(b'\xef\xbb\xbf' + body.encode('utf-8'))
        before = src.read_bytes()

        res = _roundtrip(src, tmp_path / 'out.rpy')
        assert res.used_patch
        got = (tmp_path / 'out.rpy').read_bytes()
        assert got == before, 'BOM / CRLF 被改写了'
        assert got.startswith(b'\xef\xbb\xbf'), 'BOM 丢了'

    def test_trailing_spaces_and_missing_final_newline_survive(self, tmp_path):
        """行尾空格与"结尾没有换行"都属于用户的字节，补丁不许顺手整理。"""
        src = tmp_path / 'ws.rpy'
        text = _read(FIXTURES / '01_python_block.rpy').rstrip('\n') + '   '
        src.write_bytes(text.encode('utf-8'))
        before = src.read_bytes()

        res = _roundtrip(src, tmp_path / 'out.rpy')
        assert res.used_patch
        assert (tmp_path / 'out.rpy').read_bytes() == before


class TestRefusalBranch:
    """
    "**要么全对、要么完全不动**"里"完全不动"那半 ——
    路线图说这条分支"现在没有任何测试"，这个类就是补它。

    怎么稳定地造出"补丁配不上"：拿样本 04 的表，去对**另一个内容完全不同的
    手写文件**（对位率 0%）。这比"等某个结构自己失败"可靠 ——
    2026-10-23 条件菜单选项修好之后，原来那个造法就失效了。
    """

    @staticmethod
    def _mismatched_pair(tmp_path):
        """表来自样本 04，目标/底稿是一个对不上的手写文件。"""
        src = tmp_path / 'handwritten.rpy'
        src.write_text('label other:\n    e "完全不同的内容。"\n    return\n',
                       encoding='utf-8')
        xlsx = tmp_path / 't.xlsx'
        open_rpy_as_table(str(FIXTURES / '04_conditional_option.rpy'), str(xlsx))
        out = tmp_path / 'unused.rpy'
        _p, _s, res = compile_table(
            str(xlsx), rpy_path=src, write_sheet=False, force=False,
            source_path=str(src), skip_empty=True, engine_lint=False)
        return src, res, out

    def test_handwritten_target_is_not_overwritten_when_patch_fails(self, tmp_path):
        src, res, _out = self._mismatched_pair(tmp_path)
        before = src.read_bytes()

        assert res.written is False, '补丁配不上却写了用户的手写文件'
        assert src.read_bytes() == before, '拒绝了覆盖，字节却变了'
        codes = [getattr(g, 'code', '') for g in (res.guard_issues or [])]
        assert 'HANDWRITTEN_TARGET' in codes, (
            f'拒绝覆盖要给 block 级原因，实际：{codes}')
        levels = [getattr(g, 'level', '') for g in (res.guard_issues or [])]
        assert 'block' in levels
        # 原因必须说人话（不是"未知错误"）
        message = ' '.join(getattr(g, 'message', '') for g in res.guard_issues or [])
        assert '对不上' in message and '拒绝覆盖' in message

    def test_the_preview_is_still_produced(self, tmp_path):
        """拒绝写，但要把"本来会写成什么样"留一份给人看 —— 否则用户无从判断。"""
        _src, res, _out = self._mismatched_pair(tmp_path)
        assert res.review_path and Path(res.review_path).exists(), (
            '拒绝了覆盖，却没有留下 blocked 预览')


class TestClassifier:
    """报告工具的判定口径 —— 它判错，整份报告就没意义。"""

    SRC = '# 注释\n\nlabel a:\n    e "一"\n'

    def test_identical(self):
        status, *_ = classify(self.SRC, self.SRC)
        assert status == IDENTICAL

    def test_blank_line_and_trailing_space_are_layout(self):
        out = '# 注释\nlabel a:\n    e "一"   \n'
        status, *_ = classify(self.SRC, out)
        assert status == LAYOUT

    def test_crlf_is_layout(self):
        status, *_ = classify(self.SRC, self.SRC.replace('\n', '\r\n'))
        assert status == LAYOUT

    def test_bom_is_layout_not_content(self):
        """BOM 是载体（和 CRLF 同类）：丢了是字节变了，不是内容变了。"""
        status, *_ = classify('\ufeff' + self.SRC, self.SRC)
        assert status == LAYOUT

    def test_blank_line_inside_triple_quoted_string_is_content(self):
        """
        `python:` 块里的三引号字符串之间，空行是**字符串内容**，不是排版。
        判成 layout 就等于把"字符串被改了一个空行"放过去了 —— 那是数据损坏。
        """
        src = 'label a:\n    python:\n        s = """x\n\n        y"""\n'
        out = 'label a:\n    python:\n        s = """x\n        y"""\n'
        status, *_ = classify(src, out)
        assert status == CONTENT

    def test_changed_dialogue_is_content_with_line_numbers(self):
        out = self.SRC.replace('"一"', '"二"')
        status, missing, extra, changed, unified = classify(self.SRC, out)
        assert status == CONTENT
        assert changed == [(4, '    e "一"', '    e "二"')]
        assert not missing and not extra
        assert 'e "二"' in unified

    def test_missing_and_extra_are_located(self):
        out = self.SRC.replace('    e "一"\n', '    e "一"\n    e "二"\n')
        status, missing, extra, changed, _unified = classify(self.SRC, out)
        assert status == CONTENT
        assert not missing and not changed
        assert extra == [(5, '    e "二"')]

    def test_report_tool_sees_the_conditional_option_as_identical(self, tmp_path):
        """工具与用例共用同一把尺：样本 04 在报告里必须是 identical（且真走了补丁）。"""
        rep = roundtrip(FIXTURES / '04_conditional_option.rpy', tmp_path, 1)
        assert rep.status == IDENTICAL, rep.unified
        assert rep.used_patch is True
