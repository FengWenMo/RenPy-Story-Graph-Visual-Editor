"""
守卫 · **落盘入口唯一性**（M1 收尾，2026-10-08/09）
====================================================

契约 B1：`.rpy` 落盘一律过 `generation_guard.write_generation`。

为什么需要一条**结构性**守卫（而不只是修好那一处）：D2 的实锤是控制流导出
用裸 `open(path, 'w')` 写盘、**绕过门禁** —— 于是"覆盖手写文件 / label 重名"
这两条谁都不报。而那个函数体里**没有 `.rpy` 字面量**，所以任何"按名字找 .rpy
写入"的检查都会漏掉它。

做法（实测：全包只有 **14 处落盘、12 个函数**，完全可枚举）：

  · 用 AST 扫出 `write_text` / `write_bytes` / `open(..., 'w'|'a'|'x')` 的**全部**落盘点；
  · 每个落盘点都必须在 `WRITERS` 里**说清"写的是什么"**；
  · 新增一个落盘点 → 本测试**变红**，逼人做一次有意识的分类（并想清楚
    要不要过门禁），而不是悄悄多一条绕过门禁的写路径。

⚠ 变异验证：在任一未登记的函数里加一句 `Path(p).write_text('x')` → 必红。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'storygraph'

#: 允许直接落盘的地方 —— 每个都要说明**写的是什么**。
WRITERS = {
    'story/paths.py::write_text_atomic':
        '**原子写原语**（唯一实现）：临时文件 + fsync + os.replace，'
        '`newline=""` 保留换行。它**不是旁路** —— 就是 '
        '`generation_guard.write_generation` / `write_beats_to_rpy` /'
        '改名补丁（`asset_extensions.apply_rename_patch`）落盘时调的那一处'
        '（P1-2：从前 `write_text` 会改 CRLF、且非原子）。',
    'app/session_graph.py::save_layout': '写布局 JSON（不是脚本）',
    'assets/asset_extensions.py::save': '写扩展配置 JSON',
    'assets/asset_registry.py::_write_autoscan_index': '写侧车 _autoscan.json',
    'pipeline/build.py::build_project': '写构建报告 JSON',
    'story/statement_registry.py::declare_statement': '写项目清单 storygraph.rules.json',
    'mcp_surface.py::declare': 'MCP 的写入口 —— **自己不写盘**，只调上面那个唯一写入口'
                               '（`declare_statement`）；额外做三件事：'
                               '`dry_run` 试算（只给 diff 不落盘）、写前留 `.bak`、'
                               '回执里如实报 `written`/`diff`/`backup`。'
                               '⚠ 资源表一个字不动（硬红线，判据钉住）',
    'core/ai_settings.py::save': '写**用户自己的 AI 配置**（API key），位置在'
                                 '**用户级目录**（`%LOCALAPPDATA%/StoryGraph/ai.json`，'
                                 '`STORYGRAPH_AI_CONFIG` 可覆盖）——'
                                 '⚠ **绝不写进项目仓库**（否则随 git 泄漏），'
                                 '并尽力收紧文件权限',
    'core/session_store.py::save': '写**会话记忆**（上次的项目 / 最近列表 / 各项目布局），'
                                   '位置同上**用户级目录**'
                                   '（`%LOCALAPPDATA%/StoryGraph/session.json`，'
                                   '`STORYGRAPH_SESSION_CONFIG` 可覆盖）——'
                                   '⚠ **绝不写进项目仓库**（那会进 git diff、多人协作互相打架）。'
                                   '原子替换（先写 `.tmp` 再 `os.replace`），'
                                   '写失败只记台账、绝不拦退出',
    'ui_qt/dialog_ai.py::_clear': '「清掉已存的 key」= 把那份**用户级**配置写成 `{}`。'
                                  '只清这一个文件；清完界面改口（不静默）',
    'story/statement_registry.py::declare_attr_groups':
        '写项目清单 storygraph.rules.json 的 attr_groups 段（词组分栏声明）',
    'story/statement_registry.py::remove_statement':
        '改/删项目清单（移除一条声明；**先备份**到 storygraph.rules.backup/）',
    'story/statement_registry.py::_backup_manifest':
        '把改之前的整份 rules.json 备份到 storygraph.rules.backup/'
        '（不是写 .rpy；清空/重置前先留退路）',
    'story/statement_registry.py::_write_manifest_after_removal':
        '清空/重置声明时写回规则清单（只剩空段则删文件；**先备份**）',
    'pipeline/engine_collect.py::write_facts':
        '写 engine_facts.json（引擎事实快照 —— 不是 .rpy，不碰用户脚本）',
    'pipeline/engine_collect.py::_write_probe':
        '写**临时探针** zz_storygraph_probe.rpy（注入工程 → 跑一次引擎 → 连 .rpyc 一起删）。'
        '**不是产物**，所以刻意不过 generation_guard（它只需要"有生成标记"，对探针没意义）；'
        '代价由三件事兜住：固定文件名前缀、**同名非本工具写的就拒写**、采集完必删 + 清单比对',
    'pipeline/statement_probe.py::_write_ask':
        '写**临时探针** zz_storygraph_ask_*.rpy（与上面同一个道理，但只写进**轻量副本**：'
        '只拷 .rpy/.json/.txt、跳过图像音频 —— 真机 8GB 项目整份 copytree 会把磁盘写满，'
        '那是实测事故）。同样"同名非本工具写的就拒写"，跑完连 .rpyc 一起删',
    'pipeline/statement_probe.py::_inject_evidence':
        '往**副本**的某个 label 末尾注入一句证据（第三层行为验证用：证明那段真的执行了）。'
        '⚠ 只改副本 —— 调用方 `verify_head` 保证先 copytree，真项目一个字节都不动',
    'pipeline/engine_lint.py::_lint_once':
        '写**一次性临时工程**（`game/script.rpy` = 待检文本 + 空 options.rpy + 可选的 define 骨架），'
        '只为了让 SDK `lint` 读它一遍；`TemporaryDirectory` 退出即整目录删除。'
        '⚠ 不是产物、不进用户项目、不覆盖任何东西 —— 刻意不过 generation_guard'
        '（这道闸门本身就是"产物能不能被引擎读"的判据，让它再去过一遍门禁会成环）',
    'story/excel_export.py::_thumbnail_bytes': '写缩略图缓存 png',
    'schema.py::main': '生成 docs/schema.json（CLI 入口）',
    'selfcheck.py::_check_build_pipeline': '自检夹具（写临时目录里的样例）',
    'selfcheck.py::_check_export': '自检夹具（写临时目录里的样例）',
}

_WRITE_METHODS = {'write_text', 'write_bytes'}


def _enclosing_func(tree: ast.AST, node: ast.AST):
    """最内层的包含函数（取范围最小的那个，别被外层同名/更大范围骗到）。"""
    best = None
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = fn.end_lineno or fn.lineno
        if fn.lineno <= node.lineno <= end:
            if best is None or (end - fn.lineno) < (best[1] - best[0]):
                best = (fn.lineno, end, fn.name)
    return best[2] if best else '<module>'


def _is_write_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else (
        f.id if isinstance(f, ast.Name) else '')
    if name in _WRITE_METHODS:
        return True
    if name == 'open':
        modes = [a for a in node.args[1:] if isinstance(a, ast.Constant)]
        return any(isinstance(m.value, str)
                   and any(c in m.value for c in 'wax') for m in modes)
    return False


def _scan() -> set:
    found = set()
    for p in sorted(PKG.rglob('*.py')):
        try:
            tree = ast.parse(p.read_text(encoding='utf-8'))
        except (SyntaxError, OSError):
            continue
        rel = p.relative_to(PKG).as_posix()
        for node in ast.walk(tree):
            if _is_write_call(node):
                found.add(f'{rel}::{_enclosing_func(tree, node)}')
    return found


def test_every_write_site_is_accounted_for():
    """
    新增落盘点必须**显式登记**（并说明写的是什么）—— 挡住"悄悄多一条
    绕过门禁的写路径"。这条判据抓得住裸 `open(path,'w')` 那一类
    （它不依赖函数名/字面量里出现 `.rpy`）。
    """
    found = _scan()
    unknown = sorted(found - set(WRITERS))
    assert unknown == [], (
        '这些地方在直接落盘但没登记 —— 想清楚它写的是什么：\n'
        + '\n'.join(f'  · {k}' for k in unknown)
        + '\n（若它写 .rpy：必须改走 `generation_guard.write_generation`，'
          '不许裸写；确实该独立写盘的，在 `tests/test_single_write_entry.py` '
          '的 `WRITERS` 里登记 + 写清理由）')


def test_writers_table_has_no_stale_entries():
    """反向：登记表里过期（函数已删/改名）的条目也要清掉，否则它会变成噪声。"""
    stale = sorted(set(WRITERS) - _scan())
    assert stale == [], f'登记表里的这些条目已经不存在了：{stale}'


def test_control_flow_export_goes_through_the_gate():
    """
    **定向判据**（D2 的实锤，防回退）：控制流导出必须调 `write_generation`，
    且函数体里**不许**再有裸写。

    为什么单列一条：上面那条通用守卫只看"登记没登记"，而这条盯住
    "这一个曾经绕过门禁的入口**不再**绕过"。
    """
    p = PKG / 'pipeline' / 'renpy_exporter.py'
    src = p.read_text(encoding='utf-8')
    tree = ast.parse(src)
    target = None
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == 'export_to_renpy':
            target = fn
    assert target is not None, '找不到 export_to_renpy'
    body = ast.get_source_segment(src, target) or ''
    assert 'write_generation' in body, '控制流导出不再走生成门禁了'
    # ⚠ 判据必须是 **AST 调用点**，不能是子串 —— 那段注释里就写着裸
    # `open(filepath, 'w')` 的历史，子串判据会被自己的注释骗红（第一版就是）。
    bare = [n.lineno for n in ast.walk(target) if _is_write_call(n)]
    assert bare == [], f'控制流导出又出现裸写盘（行 {bare}）'
