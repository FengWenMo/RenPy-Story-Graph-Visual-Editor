"""
探针：**顶层与 label 内的 `python:` 块**在往返里有没有被压平 / 整块丢掉。

怀疑来源（2026-10-04）：`build`（全项目合并导出）报了 10 条
PYTHON_SYNTAX_ERROR，全在 minigame 的 python 块里。

2026-10-05 复核结论（拓展边界①③之后）：

  · 那 10 条**全是门禁自己的假阳性**（逐行编译块体，见
    `generation_guard._python_syntax_issues` 的 docstring）—— 已修，
    产物里 7 个块整块编译**一个都不错**；
  · 真正的损失是**顶层 `init python:` 整块消失**（whackamole 2 个块 /
    56 行游戏逻辑），已修：它随表走隐藏列 `FileBlocks`，渲染时回到文件级；
  · 剩下对不上的行是**排版漂移**（行内注释被挪到独立行等），不是内容丢失
    —— 所以本探针现在分开报"真丢"与"只是排版"。

只读真实项目，产物写临时目录。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.pipeline.compile import compile_beats
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

FILES = ['game/minigame/mini_game1.rpy',
         'game/minigame/whackamole.rpy',
         'game/pomodorro/script_pomo.rpy']


def _py_blocks(text: str) -> list:
    """
    抽出所有 `python:` 块（含 `init python:`），按行。

    块体判据：缩进**比块头更深**（含"后面还有块体行"的空行）——
    与 `beat_render._render_marked_code_block` / 门禁同口径。
    第一版只看"这行缩进了"，于是块后面的 `while …:` / `call …`
    这些 **Ren'Py 语句**被算进了块体，报出一堆假丢失。
    """
    out, cur, base = [], None, 0
    for ln in str(text or '').splitlines():
        s = ln.strip()
        indent = len(ln) - len(ln.lstrip())
        if cur is not None:
            if not s or indent > base:
                cur.append(ln)
                continue
            cur, base = None, 0
        if s.startswith('python:') or s.startswith('init python:'):
            cur, base = [ln], indent
            out.append(cur)
    return [b for b in out if len(b) > 1]


def _squash(s: str) -> str:
    return ''.join(str(s).split())


def _without_comment(line: str) -> str:
    """粗略去掉行尾 `#` 注释（探针用；精确实现见 `RenPyLexer._strip_line_comment`）。"""
    head = str(line).split('#', 1)[0]
    return head.rstrip()


def main() -> int:
    if not REAL.exists():
        print('真实项目不在')
        return 0
    bad = 0
    comment_only_total = 0
    for rel in FILES:
        f = REAL / rel
        if not f.exists():
            print(f'[SKIP] {rel}')
            continue
        src = f.read_text(encoding='utf-8')
        before = _py_blocks(src)
        print(f'\n=== {rel} ===')
        print(f'  原文 python 块 {len(before)} 个，'
              f'共 {sum(len(b) for b in before)} 行')

        a = IncrementalRenPyAnalyzer()
        pr, _ = a.analyze(files=[f], graph=None)
        beats = beats_from_parse_result(pr)
        with tempfile.TemporaryDirectory() as td:
            xl = Path(td) / 'p.xlsx'
            export_beats_to_excel(beats, xl)
            out = compile_beats(import_beats_from_excel(xl)).rpy_text

        after = _py_blocks(out)
        print(f'  产物 python 块 {len(after)} 个，'
              f'共 {sum(len(b) for b in after)} 行')

        # 判据一：块体行在不在。**分四档**（不分开就会把"设计如此"报成 bug）：
        #   · 在                      → OK
        #   · 不在，但去掉行尾注释后在  → **行内注释被剥掉**（词法器层，刻意如此，
        #     见 `RenPyLexer._strip_inline_comment` 与
        #     `test_real_project_regressions.test_inline_comment_strip_is_line_aware`）
        #   · 不在，但产物里有对应的 `$ <同一行>` → **单行块被规范成 `$` 语句**
        #     （内容一字未少，只是块形状变了；Ren'Py 里两者等价）
        #   · 连这些都不在             → **真丢代码**
        want = [l.strip() for b in before for l in b[1:] if l.strip()]
        got = [l.strip() for b in after for l in b[1:] if l.strip()]
        got_squash = {_squash(l) for l in got}
        # "内容还在不在"要拿**整个产物**去比，不能只比块体：
        # 一个单行 `python:` 块在产物里可能已经被规范成块**外**的 `$ x = 1`
        # （内容一字未少，形状变了）——只比块体就会把它误报成"真丢"。
        all_lines = [l.strip() for l in out.splitlines() if l.strip()]
        all_squash = {_squash(l) for l in all_lines}
        all_code = {_squash(_without_comment(l)) for l in all_lines}
        lost = [l for l in want if l not in got]
        really_lost, comment_only, dollarised = [], [], []
        for l in lost:
            if _squash(l) in got_squash:
                continue
            if _squash(_without_comment(l)) in all_code:
                comment_only.append(l)
            elif _squash('$ ' + _without_comment(l)) in all_code:
                dollarised.append(l)
            else:
                really_lost.append(l)
        comment_only_total += len(comment_only)
        print(f'  块体行：原文 {len(want)} → 产物 {len(got)}；'
              f'逐字对不上 {len(lost)} 行 = 行内注释 {len(comment_only)} + '
              f'规范成 `$` {len(dollarised)} + **真丢代码 {len(really_lost)}**')
        for l in dollarised[:3]:
            print(f'     规范成 `$`：{l[:80]}')
        for l in really_lost[:8]:
            print(f'     真缺：{l[:90]}')
        if really_lost:
            bad += 1

        # 判据二：产物里的 python 块**整块**能不能编译。
        # 复用门禁那一份实现（同一条判据只该有一处）—— 探针自己再写一遍
        # 正是"逐行编译"那个假阳性的来路。
        from storygraph.story.generation_guard import generation_guard
        syntax = [i for i in generation_guard(out, None)
                  if i.code == 'PYTHON_SYNTAX_ERROR']
        for i in syntax:
            print(f'  ✗ {i.format()}')
        if syntax:
            bad += 1

    print(f'\n（行内注释共被剥掉 {comment_only_total} 行 —— 那是词法器层的'
          f'**既有设计**：`#` 之后的内容在解析时就丢掉，'
          f'见 `RenPyLexer._strip_inline_comment`；'
          f'不属于本探针管辖的三条边界，单独记着。）')
    print('结论：', '有 python 块代码损坏' if bad else 'python 块代码完好')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
