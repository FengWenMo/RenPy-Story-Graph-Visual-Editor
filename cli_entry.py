"""
根目录启动垫片（thin shim）：**打包成 exe 用的命令行入口**。

为什么不复用 `main.py`：`main.py` 是**图形界面**入口（`python main.py`
开窗口）。PyInstaller 的每个 `EXE()` 都要绑定一个入口脚本 ——
如果命令行那个也绑 `main.py`，那 `StoryGraph-CLI.exe` 双击/调用时
**会去开窗口**，而不是解析命令行参数。

所以这里和 `cli.py` 一样，只做一件事：把参数交给
`storygraph.cli.main`，逻辑一行都不重复实现。

    StoryGraph-CLI.exe validate <项目根>
    StoryGraph-CLI.exe build <项目根> --engine unity
"""
import sys
from pathlib import Path

# 源码运行（`python cli_entry.py …`）时把仓库根放上 sys.path；
# 冻结成 exe 之后包已经在归档里，这一步无害。
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ⚠ **必须在任何输出之前**：打包成 exe 后 Windows 控制台是 GBK/cp936，
# 而 `--help` 文案里有 `↔` —— 不先配好编码，连 `--help` 都是
# `UnicodeEncodeError` 崩溃（交付质检实测，见 core/consoleio.py）。
from storygraph.core import consoleio          # noqa: E402

consoleio.configure()

# 冻结环境里把崩溃日志挂上：控制台程序崩了至少留一份 traceback。
if getattr(sys, 'frozen', False):              # pragma: no cover - 打包后才走
    from contextlib import suppress

    with suppress(Exception):
        from storygraph.core import crashlog

        crashlog.install()

from storygraph.cli import main          # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
