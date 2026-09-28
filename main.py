"""
根目录启动垫片（thin shim）。

真实实现在 `storygraph/main.py`，这里只是让既有的
`python main.py` 调用方式继续可用。逻辑不重复实现。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from storygraph.main import main          # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
