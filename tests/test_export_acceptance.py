"""
P0 验收矩阵（**搬迁之后也必须逐条成立**）
==========================================

`export` + `_patch_note` 是"patch 失败不许静默退回全量覆盖"这个 P0 的
全部实现（`docs/archive/P0_静默全量覆盖_2026-10-03.md`）。它们住在
`app/session_export.py`（P2-6 第四步搬过去的）。

为什么单开一个文件、而不是散在 `test_p0_no_silent_full_regen.py` 里：
那个文件测的是**每一层的单元行为**（`write_generation` 的护栏、
`_patch_note` 的返回、`export` 的字段…）。这里测的是**端到端矩阵** ——
"用户真按下去会发生什么"，七种场景一次跑完：

    1a. 原地导出、增量补丁对上了 → **放行**，只动改过的那一行，
        `default`/`init python` 逐字节保留（2026-10-05 用户实测改口）
    1b. 原地导出、补丁没对上     → 拒绝 + 文件逐字节不变（P0 的核心）
    2.  同一动作 + force         → 放行
    3.  导出到新路径             → 放行
    4.  连续导出同一路径         → 幂等（产物首行是生成标记）
    5.  产物里有手写错句         → 原样透传，**不补 `$`**（产物可运行）
    6.  坏产物 + force           → 仍然拒绝（`force` 只越过手写那一条）

它同时是**搬迁的安全网**：把 `export` / `_patch_note` 搬到别的文件、
别的类、别的 mixin 时，只要这几条还成立，用户就不会被静默毁文件。
搬迁本身不该改行为，而这是**唯一一块"搬坏了会静默毁用户文件"**的代码。

> 1a 为什么要改口（2026-10-05，用户报）：这一条原来钉的是"原地导出到手写
> 章节 → **一律拒绝**"，于是**每一个**改自己章节的人都会撞上
> 「覆盖手写文件？」对话框（默认按钮还是取消）—— 用户的原话是
> "明明只是增量的对一个卡片的修改……为何要阻止我拿新的文件去替换旧的？"。
> 判据错在"看目标像不像手写"，而该看的是**这刀会不会丢东西**：
> 增量合并出来的全文里，目标文件的每一个字节都还在（只有改过的那几行被换掉），
> 覆盖它什么都不毁。**补丁没对上**（1b）才是真正危险的那一刀 —— 那时产物
> 只认得"拍"，全量重写会把 `default` / `init python` 抹掉，照旧拒绝。
> 护栏因此从"目标有没有生成标记"改成"底稿是不是就是目标"，
> 见 `generation_guard.merging_onto`。
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


def _loaded_session(root, path):
    """
    走**真正的** EditorSession.load（而不是手工塞 beats）。

    为什么这两个用例特意用它：load 会记下我这一份拍是从哪份字节解析出来的
    （_source_fingerprint），而 	rust_ranges 正是靠它证明行号还新鲜。
    手工塞 beats 的辅助函数（_session）没有那一步 —— 于是它代表的正是
    行号来路不明那条路（表格路径），那里必须继续依赖对位守卫。
    """
    from storygraph.app.editor import EditorSession
    s = EditorSession(project_root=root)
    s.load(path)
    return s


def _forced_patch_failure(*args, **kwargs):
    """强制 patcher 失败（对位 0/5 并带原因）—— 用它模拟"补丁没对上"。"""
    from storygraph.story.patcher import PatchResult
    src = args[1] if len(args) > 1 else kwargs.get('source_path')
    return PatchResult(text='# 全量重生成的坏文本', source_path=src,
                       matched_beats=0, total_beats=5, changed_lines=0,
                       reasons=['测试强制：对位失败'])


class TestP0AcceptanceMatrix:
    """七种场景（见模块 docstring）。"""

    def test_1a_in_place_incremental_write_is_allowed(self, project):
        """
        改一处 → 原地导出 → **写进去**（增量合并无损，不许拦）。

        这是用户 2026-10-05 报的那条：改自己章节里的一句话，却被告知
        "目标是你手写的文件"，只能取消或强行覆盖 —— 而那一刀其实是
        逐字节无损的（合并底稿就是目标本身）。
        """
        src = project / 'ch.rpy'
        src.write_text(OPAQUE, encoding='utf-8')
        s = _session(project, src)
        s.beats[0].text = '改过的一句'

        res = s.export(src)
        assert res['ok'] is True, res
        assert res['used_patch'] is True, res
        assert 'HANDWRITTEN_TARGET' not in res['codes'], res['codes']

        body = src.read_text(encoding='utf-8')
        assert '改过的一句' in body, '改的那一句没落地'
        assert '原文台词' not in body, '旧的那一句还在（没被替换）'
        # 第三类内容逐字保留 —— 这才是"增量"的意义
        assert 'default score = 0' in body, 'default 被抹掉了'
        assert 'init python:' in body, 'init python 被抹掉了'
        assert 'transform my_tf:' in body, 'transform 被抹掉了'

    def test_1c_a_fully_rewritten_card_still_writes_in_place(self, project):
        """
        一张卡**同时改三处**（旁白 → 台词、换掉正文、再加一张立绘）也要能原地写回。

        用户 2026-10-05：

        > 我只调了一个卡片，都不能替换，那就太搞用户心态了！

        对位守卫是靠**内容证据**认行的（说话人 / 正文 / 图像名）——这三样
        全被改掉时它一条都命中不了，只能判"错位"，于是**整份拒绝**：

            1 处新增内容没能落进产物 / 1 句台词在补丁结果里找不到

        而编辑器这条路的行号是**解析出来的真迹**：只要磁盘上那份文件还是载入
        时那一份（`EditorSession.source_unchanged`），行号就逐行可靠 ——
        此时按行号替换才是对的（`patch_export(trust_ranges=True)`）。
        """
        src = project / 'ch.rpy'
        src.write_text(OPAQUE, encoding='utf-8')
        s = _loaded_session(project, src)          # 走真正的 load（才会记指纹）
        assert s.source_unchanged(), '前置条件：文件没被外部改过'

        beat = s.beats[0]
        beat.speaker = 'e'                 # 旁白 → 台词
        beat.text = '全新的正文'
        beat.show = 'ling angry@d11'       # 顺手上台
        res = s.export(src)

        assert res['ok'] is True, res
        assert res['used_patch'] is True, res
        body = src.read_text(encoding='utf-8')
        assert 'e "全新的正文"' in body, body
        assert 'show ling angry at d11' in body, body
        # 第三类内容照样逐字保留（这才是"增量"的意义）
        assert 'default score = 0' in body
        assert 'init python:' in body

    def test_1d_the_same_rewrite_after_an_external_edit_is_refused(
            self, project):
        """
        **文件被外部改过之后，同一刀必须拒绝**（行号不再可信）。

        这是 1c 的保险丝：`trust_ranges` 只许在"证明过文件没变"时打开。
        否则就是当年那次事故的形态 —— 源文件被手工改过、行号整体错位，
        补丁照着行号写，**把别人的行覆盖掉**。
        """
        src = project / 'ch.rpy'
        src.write_text(OPAQUE, encoding='utf-8')
        s = _loaded_session(project, src)
        beat = s.beats[0]
        beat.speaker = 'e'
        beat.text = '全新的正文'
        beat.show = 'ling angry@d11'

        # 外部改一行（模拟用户拿 VSCode 改了这个文件）
        src.write_text('# 外部加的一行\n' + OPAQUE, encoding='utf-8')
        assert not s.source_unchanged(), '前置条件：指纹必须发现文件变了'

        res = s.export(src)
        assert res['ok'] is False, (
            f'文件被外部改过还按老行号写 —— 会覆盖别人的行：{res}')
        assert src.read_text(encoding='utf-8') == '# 外部加的一行\n' + OPAQUE, \
            '拒绝时文件一个字节都不许动'

    def test_1b_in_place_full_regen_is_still_refused(self, project,
                                                     monkeypatch):
        """
        补丁没对上 + 目标就是手写源文件 → **拒绝**，文件逐字节不变。

        P0 的核心：这时唯一的替代是"全量重生成"，而全量只认得"拍" ——
        写出去就是 `default` / `init python` 全没。
        """
        import storygraph.story.patcher as P
        monkeypatch.setattr(P, 'patch_export', _forced_patch_failure)

        src = project / 'ch.rpy'
        src.write_text(OPAQUE, encoding='utf-8')
        s = _session(project, src)

        res = s.export(src)
        assert res['ok'] is False, res
        assert 'PATCH_FAILED' in res['codes'], res['codes']
        assert src.read_text(encoding='utf-8') == OPAQUE, '文件被改写了'

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
