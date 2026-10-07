# -*- mode: python ; coding: utf-8 -*-
"""
packaging/storygraph.spec
=========================
PyInstaller 打包配方：把**图形界面**和**命令行**一起打成 exe。

产出（`dist/` 下）：
    StoryGraph.exe        ← 窗口程序，双击即用（`python main.py` 的等价物）
    StoryGraph-CLI.exe    ← 控制台程序（`python cli.py …` 的等价物）

为什么是**两个** exe 而不是一个
--------------------------------
两者的"有没有控制台窗口"要求正好相反，而这是 PE 头里的一个标志位，
运行时改不了：

  · 图形界面要 `console=False` —— 双击时不能弹出一个黑框；
  · 命令行要 `console=True` —— 它是给 CI/批处理用的，且**必须能打印**。

它们共用同一批依赖（打进同一个 onedir 里），所以总共只多出一个 exe。

为什么是 `--onedir` 而不是 `--onefile`
--------------------------------------
`--onefile` 每次启动都要把上百 MB 解压到临时目录：这个项目要加载
PySide6（Qt 插件 + 上百个 DLL）+ openpyxl + Pillow，实测启动会多等
数秒，而"点一下等 8 秒"对一个桌面编辑器是不能接受的。
`--onedir` 直接就地加载，启动快得多；代价是交付物是一个文件夹
（里面那份 `StoryGraph.exe` 就是入口）。

用法
----
    python -m PyInstaller packaging/storygraph.spec --noconfirm

跑完产物在 `dist/StoryGraph/`。
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# spec 文件在 packaging/ 下，仓库根是它的上一级。
ROOT = Path(SPECPATH).resolve().parent          # noqa: F821 - spec 注入
if not (ROOT / 'storygraph').is_dir():          # 兜底：直接从根目录跑
    ROOT = Path.cwd()

# `storygraph` 里没有 `__init__` 的隐式导入要到运行时才解析 ——
# 静态分析看不全，所以整包显式收进来（这是"打包后某个功能一按就
# ModuleNotFoundError"最常见的原因）。
hidden = collect_submodules('storygraph')

# 这些是**延迟导入**（函数体里才 import），PyInstaller 的静态分析
# 容易漏。逐个写出来，宁可多余也不要"打包后才发现"。
hidden += [
    'openpyxl',
    'openpyxl.styles',
    'openpyxl.worksheet.datavalidation',
    'openpyxl.worksheet.worksheet',
    'openpyxl.chart',
    'PIL',
    'PIL.Image',
    'PIL.ImageOps',
    'PIL.ImageQt',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtSvg',                # 图标/矢量预览可能用到
]

#: 明确**不要**的东西。Qt 全家桶默认会把 WebEngine / 3D / 多媒体后端
#: 一起拖进来（几百 MB），而这个项目只用 Core/Gui/Widgets(+Svg)。
#: 少一个模块 = 少几十 MB，也少一处加载失败的可能。
excludes = [
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebEngineQuick', 'PySide6.QtWebChannel', 'PySide6.QtWebSockets',
    'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DAnimation',
    'PySide6.Qt3DExtras', 'PySide6.Qt3DInput', 'PySide6.Qt3DLogic',
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
    'PySide6.QtQuick', 'PySide6.QtQuick3D', 'PySide6.QtQml',
    'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
    'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtSql',
    'PySide6.QtTest', 'PySide6.QtDesigner', 'PySide6.QtHelp',
    'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtPdf',
    'PySide6.QtPdfWidgets', 'PySide6.QtPositioning', 'PySide6.QtSerialPort',
    'PySide6.QtSensors', 'PySide6.QtSpatialAudio', 'PySide6.QtStateMachine',
    'PySide6.QtTextToSpeech', 'PySide6.QtUiTools', 'PySide6.QtNetworkAuth',
    'PySide6.QtRemoteObjects', 'PySide6.QtScxml', 'PySide6.QtSvgWidgets',
    # 测试与开发期依赖绝不进交付物
    'pytest', '_pytest', 'pytest_asyncio', 'unittest', 'pdb', 'doctest',
    'tkinter', 'matplotlib', 'numpy', 'pandas', 'scipy', 'IPython',
    # pygame 是 PyInstaller 的 contrib hook 顺手拖进来的（本项目一行没用）。
    # 它进来还会在**启动时**打印 "Hello from the pygame community" ——
    # 直接污染 CLI 输出（自检/CI 的 stdout 是要被解析的）。
    'pygame',
    # 这个项目历史上用过 tkinter，已彻底移除；万一有残留 import 也不打包
    'test', 'tests',
]

a = Analysis(                               # noqa: F821 - spec 注入
    [str(ROOT / 'main.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# ⚠ 两个 exe **必须是两份 Analysis**：PyInstaller 把"入口脚本"写在
# EXE 上，同一个 Analysis 派生的两个 EXE 会**跑同一个入口** ——
# 那样 `StoryGraph-CLI.exe` 会去开图形窗口而完全不解析命令行参数
# （写完第一版就这么错过一次，靠"跑一下 CLI 看它有没有开窗口"抓到）。
a_cli = Analysis(                           # noqa: F821 - spec 注入
    [str(ROOT / 'cli_entry.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)                           # noqa: F821 - spec 注入
pyz_cli = PYZ(a_cli.pure)                   # noqa: F821 - spec 注入

# ---- 图形界面：窗口程序（没有控制台） ----
exe_gui = EXE(                              # noqa: F821 - spec 注入
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='StoryGraph',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                              # UPX 压缩过的 Qt 偶尔会被杀软误报
    console=False,                          # ← 双击不弹黑框
    disable_windowed_traceback=False,       # 保留 PyInstaller 自己的错误弹窗
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ---- 命令行：控制台程序（可打印、给 CI 用） ----
exe_cli = EXE(                              # noqa: F821 - spec 注入
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name='StoryGraph-CLI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,                           # ← 必须能打印
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(                             # noqa: F821 - spec 注入
    exe_gui,
    exe_cli,
    a.binaries,
    a_cli.binaries,
    a.datas,
    a_cli.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='StoryGraph',
)
