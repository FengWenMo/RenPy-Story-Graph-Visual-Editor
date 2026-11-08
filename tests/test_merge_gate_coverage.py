# -*- coding: utf-8 -*-
"""
tests/test_merge_gate_coverage.py
=================================
**每一处"把整份文本写回文件"的落盘点，都必须回答同一个问题**：
别人在他算完计划之后改了这个文件，我这一笔怎么办？

这个问题在 2026-11-08 第十四轮之前，答案散在十几个文件里，而且大多是
"整份不一样就拒写"；`write_gate.commit_text` 把答案收成一处之后，这个守卫
负责**不让下一处再散出去**。四类落盘点，每类一条判据：

  · **过闸**（`commit_text`）：写章节脚本 / 编排层必须走它 ——
    别人改的是别处就合并，撞在同一处才拒写；
  · **认锚点**（`_locate_anchor`）：只动一行的接线 / 断线 / 改接线，靠"那一行
    还在不在"认，不靠行号；
  · **撤销 / 回滚**（按名字）：**故意**不合并（合并会把别人新删的行又塞回去），
    但必须自带 `now != …` 那道闸；
  · **例外**（`EXEMPT`）：每条都得说清理由，不是"懒得改"。

写**非剧本**（JSON 清单 / 报告 / 备份 / 用户设置）的模块整块排除（`NOT_SCRIPTS`），
理由也写在表里 —— 新模块要进那张表，得先回答"它写不写 `.rpy`"。

它不是风格检查：漏一处 = 用户点一下，别人刚写的那几行就没了。
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / 'storygraph'

#: 这些模块**不写章节脚本**（写 JSON 清单 / 报告 / 设置 / 备份 / 新建文件）。
#: 合并对它们没有意义；新模块要进这张表，得先回答"它写不写 `.rpy`"。
NOT_SCRIPTS = {
    'core/atomicio.py': '原子写本身（.rpy/.xlsx/.json 都走它，它不判定"该不该写"）',
    'core/user_prefs.py': '用户级设置 JSON',
    'core/versions.py': '历史版本放回（自带 `expect_current` 那道闸）',
    'ext/registry.py': '`storygraph.ext.json` 清单（自带三方合并 + 跨进程锁）',
    'story/statement_registry.py': '`storygraph.rules.json` 清单（自带锁 + 三方合并）',
    'story/project_conventions.py': '项目约定 JSON',
    'story/version_store.py': '写的是 `.storygraph/versions/` 里的备份本身',
    'story/generation_guard.py': '它**就是**整份导出的门禁（另一条路，另有指纹闸）',
    'story/patcher.py': '整份补丁（`patch_export` 自带 `expect_sha1`）',
    'app/batch_recipes.py': '批量配方 JSON',
    'pipeline/ir_loss.py': '报告 markdown',
    'pipeline/ir_validate.py': '报告 markdown',
    'pipeline/compile.py': '产物 / 复本（不覆盖源，另有 patch 闸）',
    'ui_qt/dialog_extensions.py': '导出的 `*.sg-ext.json` 档案',
    'ui_qt/window_nodes.py': '新建文件 / 骨架（盘上还没有"计划时那份"）',
    'story/paths.py': '转发层（真正写盘在 `core/atomicio`）',
}

#: 撤销 / 回滚：故意不合并 —— 合并会把别人新删的行又塞回去。
#: 代价是它们必须**自带**"盘上还是我写的那份吗"那道闸（下面同时钉住）。
UNDO_NAMES = {'undo', '_rollback_init', '_restore_text', '_restore_locked',
              'rollback_wrapper_writes', '_revert_locked'}

#: 只动一行的落盘点：它们不合并整份，靠"按内容找回锚点"（`_locate_anchor`）。
ANCHORED = {
    ('story/control_route.py', 'apply_control_route'),
    ('story/control_route.py', 'apply_control_unroute'),
    ('story/control_route.py', 'apply_control_edit'),
}

#: 其余例外：每条都得说清为什么不走 `commit_text` / 不认锚点。
EXEMPT = {
    ('app/batch_edit.py', 'apply_plan'):
        '批量有预演：预览里的行号和内容必须与写下的逐字一致；'
        '合并会让"看到的 ≠ 写下的"，那比拒写更坏（另开一轮）',
    ('story/control_takeover.py', 'apply_takeover'):
        '一次写好几个文件（编排层 + 各章），要么全落要么全不落；'
        '中途合并会把这条判据搅乱（它已经"先核对所有文件再写"）',
    ('story/choice_point.py', 'apply_choice_point'):
        '只在文件末尾追加一段，读和写在同一次调用里，严格解码兜住坏字节；'
        '没有"计划时那份"可比，也没有别处会被覆盖',
    ('story/sg_transforms.py', 'apply'):
        '写的是工具自己生成的 `sg_transforms.rpy`（带 `# sg-gen` 指纹），'
        '下一次生成本来就会覆盖它 —— 别人的手改不参与合并',
    ('assets/asset_extensions.py', 'apply_rename_patch'):
        '全局改名：一处对不上就**整份**拒写，且自带逐文件复读 + `.bak` 回滚；'
        '合并会让"预览里的改名清单"和写下的不一致（同 batch 那条理由）',
}

#: 闸本身不算"落盘点"（它是答案，不是问题）
_SELF = {('story/write_gate.py', 'commit_text')}


def _call_names(source: str) -> set:
    """
    一个函数体里**真正调用**了哪些名字（`ast`，不是子串找）。

    ⚠ `textwrap.dedent` 不能省：方法体截出来是以 4 个空格开头的
    （`    def undo(self):`），直接 `ast.parse` 会 `IndentationError` → 返回空集
    → 那个方法就从守卫眼里消失了（变异测试抓过一次：`undo` 整批蒸发）。
    """
    names = set()
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                names.add(fn.attr)
    return names


def _write_back_functions():
    """
    所有"会落盘"的函数：`(相对路径, 函数名, 源码, 调用名)`。

    ⚠ 判据是**调用名里有 `write_text_atomic`**，不是 `style.apply` 出现过的
    子串：变异测试抓过一次 —— 真正的退化是"把 `style.apply` 那一层顺手去掉、
    直接 `write_text_atomic(path, 新文本)`"，那样函数就从"子串视角"里消失了，
    守卫反而变绿（躲开检查比不躲更危险）。`write_text_atomic` 才是"我一定
    写了盘"这件事本身。
    """
    out = []
    for path in sorted(PACKAGE.rglob('*.py')):
        try:
            src = path.read_text(encoding='utf-8', errors='replace')
            if 'write_text_atomic' not in src:
                continue
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        rel = path.relative_to(PACKAGE).as_posix()
        if rel in NOT_SCRIPTS:
            continue
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = '\n'.join(lines[node.lineno - 1:node.end_lineno])
            calls = _call_names(body)
            if 'write_text_atomic' not in calls:
                continue
            out.append((rel, node.name, body, calls))
    return out


FUNCS = _write_back_functions()


def test_the_sweep_finds_the_known_families():
    """守卫自己的前提：扫到的数量级不能是 0（否则它永远是绿的）。"""
    names = {n for _p, n, _b, _c in FUNCS}
    assert len(FUNCS) >= 14, sorted(names)
    assert 'undo' in names, '撤销那几处没扫到 —— 判据 3 就成了空话'
    assert 'apply_control_route' in names, '接线那处没扫到 —— 判据 2 就成了空话'


def test_every_write_back_point_has_an_answer():
    """每一处都得归进四类之一；新长出来的一处没有答案 → 红。"""
    unclassified = []
    for rel, name, body, calls in FUNCS:
        if (rel, name) in _SELF:
            continue
        if 'commit_text' in calls:
            continue
        if (rel, name) in ANCHORED or name in UNDO_NAMES:
            continue
        if (rel, name) in EXEMPT:
            continue
        unclassified.append(f'{rel}::{name}')
    assert not unclassified, (
        '"别人改了别处"时这一处只会拒写（或者更糟：静默覆盖别人的改动）。'
        '请改用 `story.write_gate.commit_text`，或在 ANCHORED / EXEMPT 里'
        '写明理由：\n  ' + '\n  '.join(unclassified))


def test_undo_paths_are_exempt_but_not_unchecked():
    """
    撤销 / 回滚走白名单，但必须**自带**那道闸。

    `now != self.new_text` 这类比较是"盘上还是我写的那份吗"——没有它，
    撤销就会把别人之后写的东西一起抹掉。第十轮、第十二轮都这么踩过。
    """
    missing = []
    for rel, name, body, _calls in FUNCS:
        if name not in UNDO_NAMES:
            continue
        if '!=' in body or 'expect' in body:
            continue
        missing.append(f'{rel}::{name}')
    assert not missing, '撤销/回滚没有自己的闸：' + '、'.join(missing)


def test_anchored_families_share_one_judgement():
    """接线 / 断线 / 改接线三处必须共用同一个锚点判定（各写一套必然漂）。"""
    seen = {}
    for rel, name, _body, calls in FUNCS:
        if (rel, name) in ANCHORED:
            seen[name] = '_locate_anchor' in calls
    assert seen, 'ANCHORED 里的函数一个都没扫到 —— 名字改了'
    bad = [n for n, ok in seen.items() if not ok]
    assert not bad, f'这几处没走 `_locate_anchor`：{bad}'


def test_exempt_entries_still_describe_real_functions():
    """例外清单不许变成"早就删掉的函数"的坟场（否则它只是噪音）。"""
    real = {(rel, name) for rel, name, _b, _c in FUNCS}
    stale = sorted(k for k in EXEMPT if k not in real)
    assert not stale, f'这些例外已经不在了，删掉那几条：{stale}'


def test_not_script_modules_really_exist():
    """排除表的每一行都得指向真文件（拼错一个就等于少检查一个模块）。"""
    missing = [rel for rel in NOT_SCRIPTS if not (PACKAGE / rel).exists()]
    assert not missing, f'这些模块不在了，删掉那几行：{missing}'


def test_the_gate_is_wired_where_the_user_clicks():
    """三个真入口（删源行 / 补包装 / 写分支）必须接上 `commit_text`。"""
    wired = [
        ('app/source_line_delete.py', 'apply_source_line_delete'),
        ('story/control_branch.py', 'apply_control_branch'),
        ('story/control_route.py', 'apply_control_init'),
    ]
    for rel, name in wired:
        src = (PACKAGE / rel).read_text(encoding='utf-8')
        tree = ast.parse(src)
        lines = src.splitlines()
        node = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == name)
        body = '\n'.join(lines[node.lineno - 1:node.end_lineno])
        calls = _call_names(body)
        assert 'commit_text' in calls, f'{rel}::{name} 没接上闸'
        assert 'plan.old_text' in body, (
            f'{rel}::{name} 没把计划的原文交给闸 —— 没有 base 就合不了')
