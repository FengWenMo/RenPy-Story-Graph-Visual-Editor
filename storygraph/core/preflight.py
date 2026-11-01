"""
storygraph.core.preflight
=========================
**图形界面起不来时，先说人话**：检查 Qt 依赖的系统 DLL 在不在。

为什么要有（2026-11-01，外部审查 5.12）
--------------------------------------
`PySide6/Qt6Core.dll` 的导入表里有 `icuuc.dll`（实测，见
`tools/_probe_pe_imports.py`）。它是 **Windows 10 1607+ 自带的系统组件**
（`System32\\icuuc.dll` 转发到 `icu.dll`）。

于是有一类机器**双击没反应**：

  · 较老的 Windows（7 / 8.1）、Windows Server Core；
  · 精简过的系统镜像；
  · **Wine**（Linux 上跑这个 exe 的人，第一条反馈就是它）。

打包不会带上它（那是微软的系统文件，不该由本项目分发），所以只能在
**启动的第一秒**把"缺什么"说出来 —— 而不是让 python 抛一句
`ImportError: DLL load failed while importing QtCore`，或者更糟：
窗口程序没有控制台，`--noconsole` 把 stderr 接到空设备，用户看到的就是
"双击了一下，什么都没发生"。

判据
----
用 `ctypes.WinDLL(name)` **真的去加载一次**。不要看文件在不在：
  · 系统目录里有、但版本不匹配（32/64 位混装）照样加载失败；
  · Wine 里 `icuuc.dll` 可能是个空壳 —— 加载成功才算数。

非 Windows 平台直接返回"通过"（那套系统不需要这个文件）。
"""

from __future__ import annotations

import sys

#: 图形界面依赖的**系统** DLL（不是本项目的依赖，所以只能检查、不能打包）
GUI_SYSTEM_DLLS = ('icuuc.dll',)

#: 缺件时给用户看的原话（GUI 弹窗与 CLI 共用同一段，避免两处说法漂移）
MISSING_DLL_HINT = (
    '图形界面启动失败：这台机器缺少系统组件 {dll}。\n\n'
    '这个文件是 Windows 10（1607 以后）自带的，不需要安装本工具的任何东西 ——\n'
    '它在较老的 Windows、Windows Server Core、精简系统镜像和 Wine 里可能没有。\n\n'
    '可以这样做：\n'
    '  · 换一台 Windows 10 / 11 上用图形界面；或者\n'
    '  · 先用命令行（StoryGraph-CLI.exe …）：\n'
    '    它不依赖这个文件，体检 / 导表 / 导出都能用。\n\n'
    '（诊断信息：{detail}）'
)


def check_gui_system_dlls() -> dict:
    """
    检查图形界面依赖的系统 DLL。返回::

        {'ok': bool, 'missing': [名字…], 'platform': 'win32',
         'detail': '人能读的一句话'}

    非 Windows：`ok=True`、`missing=[]`（这套检查只对 PE 依赖有意义）。
    """
    if not sys.platform.startswith('win'):
        return {'ok': True, 'missing': [], 'platform': sys.platform,
                'detail': '非 Windows：不需要检查 icuuc.dll'}

    missing, notes = [], []
    for name in GUI_SYSTEM_DLLS:
        try:
            import ctypes
            ctypes.WinDLL(name)          # 真的加载一次，不是查文件
        except OSError as e:
            missing.append(name)
            notes.append(f'{name}: {e}')
        else:
            notes.append(f'{name}: 可用')
    return {
        'ok': not missing,
        'missing': missing,
        'platform': sys.platform,
        'detail': '；'.join(notes),
    }


def hint_for(result: dict) -> str:
    """把检查结果翻成用户能照做的一段话（没有缺失时返回空串）。"""
    if not result or result.get('ok'):
        return ''
    return MISSING_DLL_HINT.format(
        dll=' / '.join(result.get('missing') or []) or 'icuuc.dll',
        detail=result.get('detail') or '')


def warn_gui_startup_failure(result: dict) -> None:
    """
    图形界面起不来时**必须让人看见**：弹一个系统对话框（不依赖 Qt）。

    为什么用 `MessageBoxW`：此刻 Qt 根本加载不了，任何"用 Qt 弹窗"的方案
    都在这条路之外。`user32` 是每个 Windows 都有的。
    """
    text = hint_for(result)
    if not text:
        return
    if not sys.platform.startswith('win'):
        print(text, file=sys.stderr)
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, text, '剧演编辑器 · 启动失败', 0x10)   # MB_ICONERROR
    except Exception:                   # noqa: BLE001 - 弹不出来也不能再炸一次
        print(text, file=sys.stderr)


__all__ = ['GUI_SYSTEM_DLLS', 'MISSING_DLL_HINT', 'check_gui_system_dlls',
           'hint_for', 'warn_gui_startup_failure']
