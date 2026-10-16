"""
分支内的注释必须**留在块里**（2026-10-15 修 · 真机 `mini_game_flipcard.rpy` 那条不幂等）
================================================================================

现场（真机 `game/minigame/mini_game_flipcard.rpy` 第 392-398 行）::

    392:    ## 到这里说明左右都翻开了，开始结算
    393:    $ fc_moves += 1
    394:
    395:    if fc_left[fc_left_up] == fc_right[fc_right_up]:
    396:        ## 配对成功：两张一起消掉        ← 分支**体**里的注释
    397:        $ fc_left[fc_left_up] = -1

导出后那条注释**跑到了 `if` 之前、缩进也从 8 掉到 4**（分支归属当场丢失），
而且**一轮之内还不稳定**：产物里它已经在 `if` 前面了，再解析时归属到**上一拍**，
第 2 轮又换一个位置 —— `audit_idempotence` 因此报"第 1→2 轮变形（**不幂等**）"，
即**这个文件不能逐字节往返**（撞铁律 #1）。

两处根因，各修一句：

1. **`beat_build._emit_comment` 漏了条件转发**：`branch_guard` 只负责"条件不同
   就落拍、开新拍"，**不写条件**；别的发射器紧接着都会写
   `pending.condition = stmt.condition or pending.condition`，只有注释这一支漏了
   → 分支内的注释成了 `condition=''` 而 `nest_path='if:2'` 的**孤立拍**
   （结构位说"在 if 里"、条件却是空的）。
2. **`beat_render` 把注释无条件排在条件头前面**：`pre + [cond_head] + 块体`。
   修后改成 `[cond_head] + (pre + 块体)` —— 能跟条件拍并进同一拍的注释
   **一定来自分支内部**（`if` 之前的注释带的是外层条件，会被 `branch_guard`
   拆成自己那一拍），所以它该在块体里。

⚠ **仍然存在的残留局限**（本文件用"必须幂等"钉住，别让它退化；位置之美满另行定）：
  · 分支体**末尾**的注释会被提到该拍**最前**（一拍内"注释永远排第一"的旧口径）；
  · 分支内注释后面紧跟**嵌套 `if`** 时，外层那一层的包裹会丢（条件被合并成合取式）。
  两者都是**改前改后都一样**的既有行为，且都**幂等** —— 不在本次修的范围里。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy  # noqa: E402

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')

_IN = ('label a:\n'
       '    $ x = 1\n'
       '    if flag:\n'
       '        ## 两张一起消掉\n'
       '        $ y = 2\n'
       '    return\n')
_BEFORE = ('label a:\n'
           '    ## 这里说明开始结算\n'
           '    $ x = 1\n'
           '    if flag:\n'
           '        $ y = 2\n'
           '    return\n')
_TAIL = ('label a:\n'
         '    if flag:\n'
         '        $ y = 2\n'
         '        ## 收尾说明\n'
         '    return\n')
_NEST = ('label a:\n'
         '    if a:\n'
         '        ## 内层说明\n'
         '        if b:\n'
         '            $ y = 1\n'
         '    return\n')


def _roundtrip(write_rpy, text, tag='ch'):
    """原文 → 拍 → 渲染（第 1 轮）；再拿产物渲染一遍（第 2 轮）。"""
    fp = write_rpy(text, f'{tag}1.rpy')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
    out1 = render_beats_to_rpy(beats_from_parse_result(pr))
    fp2 = write_rpy(out1, f'{tag}2.rpy')
    pr2, _ = IncrementalRenPyAnalyzer().analyze(files=[fp2], graph=None)
    return out1, render_beats_to_rpy(beats_from_parse_result(pr2))


def _body(out: str) -> list:
    """去掉生成器标记行与它后面的空行 —— 只留正文。"""
    lines = [ln for ln in out.splitlines() if not ln.startswith('# 由剧本表')]
    while lines and not lines[0].strip():
        lines.pop(0)
    return lines


def test_分支内的注释留在块里(write_rpy):
    out1, out2 = _roundtrip(write_rpy, _IN)
    assert _body(out1) == ['label a:', '    $ x = 1', '    if flag:',
                           '        ## 两张一起消掉', '        $ y = 2',
                           '    return'], _body(out1)
    assert out1 == out2, '第 1→2 轮变形（不幂等）'


def test_if_之前的注释照旧排在前面(write_rpy):
    """对照：**别**把 `if` 之前的注释也吸进块里（修法不许矫枉过正）。"""
    out1, _ = _roundtrip(write_rpy, _BEFORE, 'b')
    body = _body(out1)
    assert body.index('    ## 这里说明开始结算') < body.index('    if flag:'), body


def test_一拍内注释永远排第一的残留局限必须幂等(write_rpy):
    """
    残留局限（改前改后一样）：分支体**末尾**的注释会跑到该拍最前。
    位置之美满另行定，但**它必须收敛** —— 不许再出现"第 2 轮又换一个位置"。
    """
    for tag, text in (('t', _TAIL), ('n', _NEST)):
        out1, out2 = _roundtrip(write_rpy, text, tag)
        assert out1 == out2, (tag, out1, out2)


def test_根因层_分支内注释与分支体同拍且带条件(write_rpy):
    """`_emit_comment` 漏条件转发是根因 —— 这里直接钉住那一环。"""
    fp = write_rpy(_IN, 'r.rpy')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
    beats = beats_from_parse_result(pr)
    hit = [b for b in beats if '两张一起消掉' in str(getattr(b, 'comment', ''))]
    assert len(hit) == 1, hit
    b = hit[0]
    assert (b.condition or '').strip() == 'flag', (
        '分支内注释那拍没有带上分支条件 —— 它会渲染成 `if` 之外的孤立注释',
        b.condition, getattr(b, 'nest_path', None))
    assert (b.code or '').strip() == 'y = 2', (
        '注释没有与分支体并进同一拍', b.code)


@pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机')
def test_真机幂等审计必须全过():
    """就是那条 P1：真机 54 个文件"导出一次 == 再导出一次"逐字节相同。"""
    import tempfile

    sys.path.insert(0, str(ROOT / 'tools'))
    import audit_idempotence as A  # noqa: E402

    from storygraph.parsing.discover import discover_scripts  # noqa: E402

    scripts = sorted(Path(str(p)) for p in discover_scripts(REAL))
    tmp = Path(tempfile.mkdtemp(prefix='idem_'))
    bad = []
    for p in scripts:
        t0 = p.read_text(encoding='utf-8', errors='ignore')
        t1 = A._render(p, t0, tmp, 'r1')
        if A._render(p, t1, tmp, 'r2') != t1:
            bad.append(p.name)
    assert bad == [], ('这些文件不能逐字节往返', bad)
