"""
守卫：**`python:` 块不许静默消失**。
======================================

为什么单开一个文件（2026-10-04 交付评审实测发现的，**最危险的一条**）

真实项目 `D:/千连万理/RenPyTest/sr_general` 上实测：

  · `whackamole.rpy`  原文 2 个 `python:` 块 / 67 行 → **产物 0 个**，
    56 行游戏逻辑整块消失；
  · `mini_game1.rpy`  原文 6 个块 → 产物 5 个，另有 18 行续行对不上；
  · 全项目 464 个 python 块里丢了 3 个（0.6%）。

**为什么它比"build 失败"危险得多**：

  · `build`（全项目合并）会报 10 条 `PYTHON_SYNTAX_ERROR`，
    被生成门禁拦下 —— 产物**没**写进目标路径，人是安全的；
  · 但**单章导出时块是"整块没了"，产物语法完全合法** →
    门禁判不出问题 → **写出一份能跑、但少了小游戏逻辑的脚本，且不报错**。

这正是本仓库最怕那一类缺陷（契约 H1：**静默改数据**比报错坏得多）。
所以这里不只测"块还在"，还测"**丢了必须说出来**"。

判据用**块体行**而不是块数：块数相同但内容被压平（续行缩进变了）
同样是坏的 —— mini_game1 就是这种。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.pipeline.compile import compile_beats
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

#: 几种真实存在的 `python:` 形态。名字即形态。
CASES = {
    'python_in_label': (
        'label a:\n'
        '    python:\n'
        '        x = 1\n'
        '        y = 2\n'
        '    e "hi"\n'
    ),
    'init_python': (
        'init python:\n'
        '    def helper():\n'
        '        return 1\n'
        '\n'
        'label a:\n'
        '    e "hi"\n'
    ),
    'python_with_continuation': (
        'label a:\n'
        '    python:\n'
        '        positions = [\n'
        '            (200, 200), (600, 200),\n'
        '            (200, 400), (600, 400),\n'
        '        ]\n'
        '    e "hi"\n'
    ),
    'python_with_blank_lines': (
        'label a:\n'
        '    python:\n'
        '        import random\n'
        '\n'
        '        def f():\n'
        '            return random.randint(1, 3)\n'
        '    e "hi"\n'
    ),
}


def _python_body_lines(text: str) -> list:
    """所有 `python:` 块的**块体行**（不含 `python:` 那一行）。"""
    out, cur, base = [], None, None
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith('python:') or s.startswith('init python:'):
            cur, base = [], None
            continue
        if cur is None:
            continue
        if not s:
            continue                       # 空行跳过（不影响语义）
        if base is None:
            base = len(ln) - len(ln.lstrip())
        if (len(ln) - len(ln.lstrip())) < base:
            cur = None                     # 缩进退回 → 块结束
            continue
        cur.append(s)
        out.append(s)
    return out


def _roundtrip(src_text: str, tmp_path) -> str:
    f = tmp_path / 's.rpy'
    f.write_text(src_text, encoding='utf-8')
    a = IncrementalRenPyAnalyzer()
    pr, _ = a.analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    xl = tmp_path / 's.xlsx'
    export_beats_to_excel(beats, xl)
    return compile_beats(import_beats_from_excel(xl)).rpy_text


class TestPythonBlockSurvives:
    @pytest.mark.parametrize('name', sorted(CASES))
    def test_block_body_survives(self, tmp_path, name):
        """
        python 块的**每一行**都必须在产物里。

        不比块数：块数对得上但行被压平（续行缩进变了）同样是坏的。
        """
        src = CASES[name]
        want = _python_body_lines(src)
        assert want, f'{name} 样本里没解析出 python 块体，判据写坏了'

        got = _python_body_lines(_roundtrip(src, tmp_path))
        lost = [l for l in want if l not in got]
        assert not lost, (
            f'{name}：python 块体丢了 {len(lost)}/{len(want)} 行\n'
            f'  丢了：{lost[:6]}\n'
            f'  （静默丢代码是最坏的一类缺陷：产物语法合法、'
            f'门禁拦不住、游戏能跑但逻辑没了）')


class TestDroppedPythonMustBeReported:
    """
    如果块确实保不住（能力边界），**必须说出来**，不许静默成功。

    这条是上面那条的兜底：真做不到就明说，让人知道这一章不能用工具改。
    """

    def test_compile_result_flags_python_blocks(self, tmp_path):
        """
        源文件里有 python 块 → 编译结果必须让人知道（TODO / 告警 / 保留）。

        现状（2026-10-04）这条**大概率是红的**：块整块消失而编译照报成功。
        修法二选一：
          · 保真（让块原样出去）—— 走 `CodeBlock` 通道，见契约 A3；
          · 保不住就报（validate 阶段给一条"本文件含 python 块，
            导出会丢失，请勿用工具编辑此章"）。
        **沉默不是选项。**
        """
        # 用 `init_python`：**实测这一形态会整块丢**（顶层 `init python:`
        # 属"第三类"，不进 Beats），正好是这条要守的场景。
        src = CASES['init_python']
        out = _roundtrip(src, tmp_path)
        survived = 'x = 1' in out and 'y = 2' in out
        flagged = ('TODO' in out) or ('python' in out.lower())
        assert survived or flagged, (
            'python 块既没保住、也没在产物里留下任何提示 —— 静默丢代码。\n'
            f'产物：\n{out}')
