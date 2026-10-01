# editor_utils.py
import os
import subprocess
import sys

from storygraph.app import notify


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