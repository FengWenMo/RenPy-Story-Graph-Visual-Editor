#!/usr/bin/env python
"""
根目录命令行垫片（thin shim）。

真实实现在 `storygraph/cli.py`，这里只是让既有的
`python cli.py validate|build|table|compile|collab` 调用方式继续可用
（CI 与文档都依赖它）。逻辑不重复实现。

用法：
    python cli.py validate <项目根>            # 只体检（不写盘）
    python cli.py build <项目根> --engine renpy # 体检 + 出产物
    python cli.py table rpy2xlsx <a.rpy>       # 单文件摊表
    python cli.py table xlsx2rpy <a.xlsx>      # 表写回脚本
    python cli.py collab init|pull|push|status|lock|unlock|approve

退出码：0 通过；1 有错误；2 用法/环境问题。CI 直接看退出码。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from storygraph.cli import main            # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
