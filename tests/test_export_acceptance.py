"""
P0 验收矩阵（**搬迁之后也必须逐条成立**）
==========================================

`export` + `_patch_note` 是"patch 失败不许静默退回全量覆盖"这个 P0 的
全部实现（`docs/P0_静默全量覆盖_2026-10-03.md`）。它们住在
`app/session_export.py`（P2-6 第四步搬过去的）。

为什么单开一个文件、而不是散在 `test_p0_no_silent_full_regen.py` 里：
那个文件测的是**每一层的单元行为**（`write_generation` 的护栏、
`_patch_note` 的返回、`export` 的字段…）。这里测的是**端到端矩阵** ——
"用户真按下去会发生什么"，六种场景一次跑完：

    1. 原地导出到手写章节文件   → 拒绝 + 文件逐字节不变 + default/init python 在
    2. 同一动作 + force        → 放行
    3. 导出到新路径            → 放行
    4. 连续导出同一路径        → 幂等（产物首行是生成标记）
    5. 产物里有手写错句        → 原样透传，**不补 `$`**（产物可运行）
    6. 坏产物 + force          → 仍然拒绝（`force` 只越过手写那一条）

它同时是**搬迁的安全网**：把 `export` / `_patch_note` 搬到别的文件、
别的类、别的 mixin 时，只要这六条还成立，用户就不会被静默毁文件。
搬迁本身不该改行为，而这是**唯一一块"搬坏了会静默毁用户文件"**的代码。
"""

from __future__ import annotations

import pytest

OPAQUE = (
    'default score = 0\n'
    '\n'
    'init python:\n'
    '    def helper():\n'
    '        return 1\n'
    '\n'
    'transform my_tf:\n'
    '    xalign 0.5\n'
    '\n'
    'label a:\n'
    '    e "原文台词"\n'
)

BROKEN = 'label b:\n    l "1"\n    l oh "3"\n'


@pytest.fixture
def project(tmp_path):
    """一个只含"手写章节文件"的最小项目。"""
    return tmp_path


def _session(root, path):
    from storygraph.app.editor import EditorSession
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    s = EditorSession(project_root=root)
    s.script_path = path
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    s.beats = beats_from_parse_result(pr)
    return s


class TestP0AcceptanceMatrix:
    """六种场景（见模块 docstring）。"""

    def test_1_refuses_to_overwrite_handwritten_in_place(self, project):
        src = project / 'ch.rpy'
        src.write_text(OPAQUE, encoding='utf-8')
        s = _session(project, src)

        res = s.export(src)
        assert res['ok'] is False, res
        assert 'HANDWRITTEN_TARGET' in res['codes'], res['codes']
        body = src.read_text(encoding='utf-8')
        assert body == OPAQUE, '文件被改写了'
        assert 'default score = 0' in body, 'default 被抹掉了'
        assert 'init python:' in body, 'init python 被抹掉了'

    def test_2_force_allows_the_same_export(self, project):
        src = project / 'ch.rpy'
        src.write_text(OPAQUE, encoding='utf-8')
        s = _session(project, src)
        res = s.export(src, force=True)
        assert res['ok'] is True, res

    def test_3_writing_a_new_path_is_allowed(self, project):
        src = project / 'ch.rpy'
        src.write_text(OPAQUE, encoding='utf-8')
        s = _session(project, src)
        new = project / 'build' / 'out.rpy'
        res = s.export(new)
        assert res['ok'] is True, res
        assert new.exists()

    def test_4_re_exporting_the_same_path_is_idempotent(self, project):
        """
        连导三次都要成功。

        补丁那条路产出的首行是**源文件的首行**，所以必须由
        `ensure_generator_marker` 补上标记 —— 否则第二次会被自己的
        护栏当成"手写文件"拒写（实测踩过）。
        """
        src = project / 'ch.rpy'
        src.write_text(OPAQUE, encoding='utf-8')
        s = _session(project, src)
        new = project / 'build' / 'out.rpy'
        assert s.export(new)['ok'] is True
        head = new.read_text(encoding='utf-8').splitlines()[0]
        assert head.startswith('# 由剧本表自动生成'), head
        for n in (2, 3):
            assert s.export(new)['ok'] is True, f'第 {n} 次失败'

    def test_5_handwritten_renpy_statement_survives_verbatim(self, project):
        """
        `l oh "3"` 原样透传，**不补 `$`**。

        补了就是 Python 语法错误 → Ren'Py 加载就报错（产物跑不起来），
        而原文本来至少能跑。见 `tests/test_product_python_syntax.py`。
        """
        bad = project / 'broken.rpy'
        bad.write_text(BROKEN, encoding='utf-8')
        s = _session(project, bad)
        out = project / 'build' / 'broken.rpy'
        res = s.export(out)
        assert res['ok'] is True, res
        text = out.read_text(encoding='utf-8')
        assert '$ l oh' not in text, text
        assert 'l oh "3"' in text, text

    def test_6_force_does_not_bypass_a_broken_product(self, project):
        """
        **`force` 只越过"手写目标"那一条。**

        中文图像名（`show 凌跃捷`）是 Ren'Py 语法上根本不存在的图像 ——
        写进 `game/` 会让游戏起不来。这种 block 不该有逃生舱。
        """
        from storygraph.story.beat_model import Beat
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=project)
        s.script_path = None
        s.beats = [Beat(seq=1, label='a', kind='say', text='x',
                        show='凌跃捷 开心')]
        res = s.export(project / 'build' / 'cjk.rpy', force=True)
        assert res['ok'] is False, res
        assert 'CJK_IN_IMAGE_TAG' in res['codes'], res['codes']

    def test_7_two_in_place_exports_in_a_row(self, project):
        """
        **改一句 → 导出 → 再改一句 → 再导出**：第二次不许失败。

        这是单人最常用的一条循环，而它曾经在第二次断掉（2026-10-05，
        `tools/acceptance_workflow.py` 抓到）：

          · 第一次写回之后，**磁盘上那一份**已经是新内容（补丁插了几行）；
          · 而拍的 `src_first/src_last` 还是**改动前**那份文件的 →
            对位率掉到 0~50% → 补丁失败 → "目标就是源文件"那条 P0 护栏拒写；
          · 用户看到 `PATCH_FAILED：对位率不足`，而**没有任何提示说
            "重载一下章节就好"**。

        修法：原地导出成功后按刚写出的那份文件重贴行号
        （`session_export._resync_source_ranges`）。
        """
        src = project / 'ch.rpy'
        src.write_text(OPAQUE, encoding='utf-8')
        s = _session(project, src)

        s.beats[0].text = '第一次改'
        assert s.export(src, force=True)['ok'] is True

        s.beats[0].text = '第二次改'
        res = s.export(src, force=True)
        assert res['ok'] is True, f'第二次原地导出失败了：{res}'
        assert 'PATCH_FAILED' not in res['codes'], res

        body = src.read_text(encoding='utf-8')
        assert '第二次改' in body, body
        # 第三类内容在两次导出之后都还在（这才是"增量"的意义）
        assert 'default score = 0' in body
        assert 'init python:' in body
        assert 'transform my_tf:' in body
