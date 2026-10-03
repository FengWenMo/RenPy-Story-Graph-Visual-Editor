# editor_utils.py
import os
import subprocess
import sys
from pathlib import Path

from storygraph.core import notify


def open_with_system_default(path) -> bool:
    """
    用系统默认程序打开文件（Windows 上是 Excel，macOS 是 Numbers/Excel）。

    返回 False 表示没能唤起任何程序 —— 调用方应提示用户手动打开，
    而不是假装成功了。

    2026-10-01 从 `storygraph/ui/graph_view.py` 搬来这里：它是纯平台
    逻辑（一行 tkinter 都没有），却因为住在 UI 包里，导致"用 Excel 打开
    这一章"这件事只有 tkinter 前端能用。
    """
    path = Path(path)
    if not path.exists():
        return False
    try:
        if sys.platform == 'win32':
            os.startfile(str(path))
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(path)])
        return True
    except Exception:                         # noqa: BLE001 - 交给调用方提示
        return False


def open_file_at_line(file_path, line_number):
    if not os.path.exists(file_path):
        notify.error(f"文件不存在：{file_path}", title="错误")
        return
    commands = []
    if sys.platform == "win32":
        commands.append(["code", "-g", f"{file_path}:{line_number}"])
        commands.append(["notepad++", f"-n{line_number}", file_path])
        commands.append(["subl", f"{file_path}:{line_number}"])
    elif sys.platform == "darwin":
        commands.append(["code", "-g", f"{file_path}:{line_number}"])
        commands.append(["subl", f"{file_path}:{line_number}"])
        commands.append(["open", "-a", "TextEdit", "--args", "-l", str(line_number), file_path])
    else:
        commands.append(["code", "-g", f"{file_path}:{line_number}"])
        commands.append(["subl", f"{file_path}:{line_number}"])
        commands.append(["xdg-open", file_path])
    for cmd in commands:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue
    try:
        if sys.platform == "win32":
            os.startfile(file_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", file_path])
        else:
            subprocess.Popen(["xdg-open", file_path])
    except Exception as e:
        notify.error(f"无法打开文件：{e}", title="错误")