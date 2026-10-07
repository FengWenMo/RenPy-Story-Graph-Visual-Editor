"""
storygraph.selfcheck
====================
**打包后的 exe 自己验一遍**：`StoryGraph.exe --selfcheck`。

为什么住在**包根**而不是 `core/`
--------------------------------
它要做的事就是"跨层走一遍主链路"：`ui_qt` 建窗口、`app` 开会话、
`pipeline` 导出。而 `core` 是最底层 —— 由
`tests/test_package_layering.py` 守着"后端不许依赖前端"。
放在 `core/` 里等于让最底层反向依赖 `ui_qt`，那条守卫**当场就红了**
（交付质检时实测过）。包根下的散文件不参与包间分层判定，
它正是"编排器"该待的地方。

为什么需要它（交付质检的结论）：打包能把"源码里明明跑得通"的功能悄悄弄坏，
而坏法都是**运行期才发现**的：

  · 某个模块是延迟 import 的，PyInstaller 静态分析没看见 →
    `ModuleNotFoundError`，而且往往在**导出产物**那一步才炸；
  · Qt 插件（`platforms/qwindows.dll`）没进包 → 窗口起不来；
  · 资源表 / Excel 读写依赖（openpyxl）被 exclude 掉 → 落到磁盘时失败。

这些**在源码测试里一个都测不到**（`pytest` 永远在源码环境跑）。
所以判据必须是"**在冻结环境里真的走一遍主链路**"，尤其是**导出**：
本项目的产物会直接进别人的 `game/`，一次"打包后导出坏了"就是线上事故。

设计上刻意**不碰用户文件**：导出写进临时目录，跑完删掉。
退出码：0 全部通过；1 有失败（并把失败项打出来 + 写进崩溃日志）。
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from contextlib import suppress
from pathlib import Path

__all__ = ['run', 'format_report']

#: 主链路要能 import 的模块。**包含延迟 import 的那些** ——
#: 它们正是打包最容易漏的一类。
REQUIRED_MODULES = (
    'storygraph',
    'storygraph.main',
    'storygraph.main_qt',
    'storygraph.cli',
    'storygraph.app.editor',
    'storygraph.core.consoleio',
    'storygraph.core.crashlog',
    'storygraph.core.safeguard',
    'storygraph.parsing.renpy_parser',
    'storygraph.story.beat_build',
    'storygraph.story.beat_render',
    'storygraph.story.patcher',
    'storygraph.story.generation_guard',
    'storygraph.story.excel_export',
    'storygraph.story.script_table',
    'storygraph.pipeline.build',
    'storygraph.pipeline.compile',
    'storygraph.pipeline.engine_exporters',
    'storygraph.pipeline.validate',
    'storygraph.assets.asset_registry',
    'storygraph.ui_qt.main_window',
    'storygraph.ui_qt.window_export',
    'storygraph.ui_qt.window_graph_ops',
    'openpyxl',
)

#: 一个最小但"五脏俱全"的章节：有台词、有舞台、有菜单、有一段
#: **第三类内容**（`default` / `init python`）—— 后者是导出里最容易被
#: 静默丢掉的东西，必须一起验。
SAMPLE_RPY = '''default score = 0

init python:
    def helper():
        return 1

label selfcheck:
    scene bg room
    show eileen happy
    e "自检台词"
    menu:
        "选项一":
            e "选了一"
        "选项二":
            e "选了二"
    return
'''


def _check_imports() -> list:
    """逐个 import；返回失败清单 `[(模块, 异常摘要), …]`。"""
    import importlib
    bad = []
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except Exception as e:                  # noqa: BLE001 - 就是要抓全部
            # 打包后这里**必须**能说清是哪个模块 —— 否则用户只看到
            # "Failed to execute script"。
            bad.append((name, f'{type(e).__name__}: {e}'))
    return bad


def _check_qt() -> str:
    """能不能真的建起 Qt 窗口（插件齐不齐）。返回错误描述，OK 返回空串。"""
    # **强制离屏**：自检不该在用户屏幕上闪一个窗口。
    # 必须在 import Qt 之前设 —— Qt 读一次就记下来了。
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as e:                      # noqa: BLE001
        return f'PySide6 不可用：{type(e).__name__}: {e}'
    try:
        app = QApplication.instance() or QApplication([])
        from storygraph.ui_qt.main_window import open_editor
        win = open_editor(None, None, None, None)
        win.close()
        app.processEvents()
        return ''
    except Exception as e:                      # noqa: BLE001
        return f'建窗口失败：{type(e).__name__}: {e}'


def _check_export() -> str:
    """
    **导出主链路**：解析 → 拍 → 多引擎导出 → 落盘 + 生成门禁。

    这是本自检的重点。判据不是"函数没抛异常"，而是
    "**产物真的在磁盘上、内容对得上**"。
    """
    from storygraph.app.editor import EditorSession
    from storygraph.pipeline.engine_exporters import export_ir

    with tempfile.TemporaryDirectory(prefix='sg_selfcheck_') as td:
        work = Path(td)
        src = work / 'game' / 'selfcheck.rpy'
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(SAMPLE_RPY, encoding='utf-8')

        s = EditorSession(project_root=work)
        n = s.load(src)
        if n <= 0:
            return '解析章节得到 0 拍（解析器或依赖没打进包）'

        # --- 多引擎导出：每个引擎的产物必须真的落在磁盘上 ---
        for engine in ('unity', 'godot', 'json', 'csv', 'renpy'):
            out = work / 'build' / engine
            res = s.export_ir(engine, out)
            if not res.get('ok'):
                return f'{engine} 导出失败：{res.get("error")}'
            files = [Path(f) for f in res.get('files') or []]
            if not files:
                return f'{engine} 导出没有产出任何文件'
            missing = [f.name for f in files if not f.is_file()]
            if missing:
                return f'{engine} 导出的文件不在磁盘上：{missing}'
            # 同名（含大小写）压掉是真实缺陷，必须在这里也拦住
            names = [f.name.casefold() for f in files]
            if len(names) != len(set(names)):
                return f'{engine} 的产物文件名互相覆盖：{names}'

        # --- 落盘导出（门禁 + 生成标记）---
        target = work / 'build' / 'renpy' / 'out.rpy'
        r = s.export(target)
        if not r.get('ok'):
            return f'export() 被拦下：{r.get("blocking")}'
        if not target.is_file():
            return 'export() 报告成功，但目标文件不在磁盘上'
        first = target.read_text(encoding='utf-8').splitlines()[0]
        if '由剧本表自动生成' not in first:
            return f'产物缺少生成标记（首行：{first[:40]}）'
        # 第二遍必须还能导出（自家产物不许被自己的护栏拦下）
        if not s.export(target).get('ok'):
            return '第二次导出自己的产物被拦下（生成标记没生效）'

        # --- 第三类内容必须活下来（这是导出最贵的失败）---
        body = target.read_text(encoding='utf-8')
        for token in ('default score = 0', 'init python:'):
            if token not in body:
                return f'产物丢了顶层内容：`{token}`'

    return ''


def _check_build_pipeline() -> str:
    """CLI `build` 那条路（CI 用的）也要能跑通。"""
    from storygraph.pipeline.build import build_project

    with tempfile.TemporaryDirectory(prefix='sg_selfcheck_build_') as td:
        work = Path(td)
        src = work / 'game' / 'selfcheck.rpy'
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(SAMPLE_RPY, encoding='utf-8')
        res = build_project(work, out_dir=work / 'build_out', engine='renpy',
                            strict=False)
        if not Path(res.report_path).is_file():
            return f'build 没写出报告（{res.report_path}）'
        if not res.artifacts:
            return 'build 没产出任何 artifact'
        for a in res.artifacts:
            if not Path(a).is_file():
                return f'build 报告的产物不在磁盘上：{a}'
    return ''


def run() -> dict:
    """
    跑完整自检。返回 `{'ok': bool, 'checks': [(名字, 通过?, 详情), …]}`。

    单项失败**不中断**后面几项 —— 一次跑完看到全部问题，比
    "修一个跑一次"有用得多。
    """
    checks: list = []

    bad = _check_imports()
    checks.append(('依赖模块可导入',
                   not bad,
                   '全部 %d 个模块通过' % len(REQUIRED_MODULES) if not bad
                   else '；'.join(f'{m} → {e}' for m, e in bad[:5])))

    try:
        err = _check_export()
    except Exception as e:                      # noqa: BLE001 - 自检必须报全
        # **报全**是刻意的：自检的意义就是"一次看到所有问题"，
        # 所以单项崩了不中断后面几项，但必须把 traceback 带进结论。
        err = (f'未捕获异常 {type(e).__name__}: {e}\n'
               + traceback.format_exc(limit=3))
    checks.append(('导出主链路（多引擎 + 落盘 + 门禁）', not err, err))

    try:
        err = _check_build_pipeline()
    except Exception as e:                      # noqa: BLE001
        err = (f'未捕获异常 {type(e).__name__}: {e}\n'
               + traceback.format_exc(limit=3))
    checks.append(('CLI build 流水线', not err, err))

    try:
        err = _check_qt()
    except Exception as e:                      # noqa: BLE001
        err = (f'未捕获异常 {type(e).__name__}: {e}\n'
               + traceback.format_exc(limit=3))
    checks.append(('Qt 界面可构建', not err, err))

    return {'ok': all(ok for _n, ok, _d in checks), 'checks': checks}


def format_report(result: dict) -> str:
    """给人看的一段话。"""
    lines = ['剧情编辑器自检', '=' * 40]
    for name, ok, detail in result['checks']:
        mark = '通过' if ok else '失败'
        lines.append(f'[{mark}] {name}' + (f' —— {detail}' if detail else ''))
    lines.append('=' * 40)
    lines.append('结论：' + ('全部通过' if result['ok'] else '**有失败项**'))
    if not result['ok']:
        lines.append('请把上面的失败项连同崩溃日志一起反馈。')
    return '\n'.join(lines)


def main(argv=None) -> int:
    """
    `--selfcheck` 的实现：跑一遍、打印、给退出码。

    顺序刻意是"**先落日志、再打印**"：窗口版 exe 根本没有 stdout
    （`print` 会抛），而自检结论恰恰是那时候最需要留下的东西 ——
    反过来的话，打印失败会把后面的日志记录一起挡掉（第一版就是这个顺序，
    被 `tests/test_no_silent_except.py` 抓出"这里在静默吞异常"）。
    """
    result = run()
    text = format_report(result)

    log_tail = ''
    if not result['ok']:
        from storygraph.core import crashlog
        log_tail = f'\n（已写入崩溃日志：{crashlog.log_path()}）'
        crashlog.record('--- 自检失败 ---\n' + text)

    try:
        print(text + log_tail)
    except Exception as e:                      # noqa: BLE001 - 窗口版没有 stdout
        # 打不出（窗口程序 / 编码怪）也不能让自检结果消失：
        # 结论已经进了崩溃日志，这里退回纯 ASCII 再试一次。
        from storygraph.core.safeguard import swallow
        swallow('selfcheck.main', e)
        with suppress(Exception):
            sys.stdout.write(
                (text + log_tail).encode('ascii', 'replace').decode('ascii'))
    return 0 if result['ok'] else 1
