"""
storygraph.core.consoleio
=========================
**控制台输出绝不能因为一个字符打不出来就崩掉。**

为什么需要它（2026-10-05 交付质检，打包后实测）：

    > StoryGraph-CLI.exe --help
    UnicodeEncodeError: 'gbk' codec can't encode character '\u2194'
    [PYI-49864:ERROR] Failed to execute script 'cli_entry'

`cli.py` 的 `--help` 文案里有「Excel ↔ .rpy 双向联通」这种字符。源码运行
时开发机的 `PYTHONUTF8` 常常是开的，看不出问题；**打成 exe 之后**
Windows 上 `sys.stdout.encoding` 是控制台代码页（简体中文机器 = GBK/cp936），
于是**连 `--help` 都打不开**，更别说 `validate` / `build` 的结论里
还有 `✓` / `→` / 表格线。

这不是"显示难看"，是**功能不可用**：CI 里 `StoryGraph-CLI.exe validate`
一跑就崩，退出码还是 1（看起来像"校验不通过"），排查方向完全是错的。

做法（按优先级）：
  1. 把 stdout/stderr 换成 **UTF-8** —— 现代 Windows Terminal 与
     `chcp 65001` 都正常，纯文本也最不容易乱码；
  2. UTF-8 也设不上（老环境、被重定向到怪东西）→ 退到**当前编码 +
     `errors='backslashreplace'`**：打不出的字符写成 `\\u2194`，
     **信息不丢**，而且绝不抛异常；
  3. 全程 `suppress`：这是"让输出更稳"的辅助模块，
     **它自己绝不能成为新的崩溃点**。

导入时机：`main.py` / `cli_entry.py` 的**最前面**（任何 print 之前）。
`tests/conftest.py` 之外不要动全局流，所以 `configure()` 是可重复调用的。
"""

from __future__ import annotations

import sys
from contextlib import suppress

__all__ = ['configure']


def _try_reconfigure(stream, encoding: str, errors: str) -> bool:
    """
    尽量把 `stream` 换成指定编码；换不了返回 False（**不抛**）。

    只处理有 `reconfigure` 的文本流：`sys.stdout` 被重定向成别的对象
    （pytest 的捕获、`io.StringIO`、管道包装）时没有这个方法，
    那就说明它有自己的人管编码 —— 不该我们去动。
    """
    if stream is None:
        return False
    reconfigure = getattr(stream, 'reconfigure', None)
    if reconfigure is None:
        return False
    ok = False
    with suppress(Exception):
        reconfigure(encoding=encoding, errors=errors)
        ok = True
    return ok


def _fallback(stream) -> bool:
    """
    UTF-8 换不上 → 至少保证**打不出的字符不抛异常**。

    用 `backslashreplace`（写成 `\\u2194`）而不是 `replace`（写成 `?`）：
    前者还能认出"原来是什么"，后者等于把信息扔了。
    """
    if stream is None:
        return False
    reconfigure = getattr(stream, 'reconfigure', None)
    if reconfigure is None:
        return False
    ok = False
    with suppress(Exception):
        reconfigure(errors='backslashreplace')
        ok = True
    return ok


def configure() -> bool:
    """
    把标准输出/错误配成"打不崩"的状态。返回是否成功切到了 UTF-8。

    可重复调用（幂等）：第二次调用只是再确认一遍。
    """
    utf8 = True
    for stream in (sys.stdout, sys.stderr):
        if not _try_reconfigure(stream, 'utf-8', 'backslashreplace'):
            utf8 = False
            _fallback(stream)
    return utf8
