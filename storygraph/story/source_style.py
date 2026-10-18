"""
storygraph.story.source_style
=============================
**源文件的"载体风格"**：BOM / 换行符 —— 读的时候记住，写回去的时候还原。

为什么必须单独一层（2026-10-18，审计 P0-1）

"逐字节保真"包含两件容易被忽略的事：

  · **UTF-8 BOM**：Windows 上的中文项目（记事本 / VS Code "UTF-8 with BOM"）
    保存出来就带 BOM。解析器读的时候必须吃掉它（否则 `^label` 认不出标签，
    整个文件一条语句都解析不到），但**写回去必须原样还回来**——否则
    `git diff` 的第一行永远是 `-<U+FEFF># …`，用户看到的是"工具又在改我的文件"。
  · **换行符**：`Path.read_text()` 默认做 universal-newline 翻译（CRLF → LF），
    `Path.write_text()` 在 Windows 上又把 LF 翻回 CRLF。两者合起来，
    "打开再导出"看起来没变，实际**每个字节都变**（只有原文件恰好也是 CRLF
    时才撞对）。审计实测：`tutorial` 的 23 个脚本里 23 个都是 CRLF，
    而工具写出来的是 LF —— 逐字节相同率 0/23。

设计：**只在一个地方判定、只在一个地方还原**。
`detect()` 读一次原始字节给出风格；`normalize()` 把任何文本规整成 LF；
`apply()` 在**落盘前**把 BOM / 换行按风格铺回去。

⚠ 刻意**不**试图保留"混合换行"：那种文件里 CRLF/LF 交错，任何按行重排
都会破坏它；按占多数的那个统一，并且这是**写盘**的最后一层，不会再被
别的代码翻回去（`write_text_atomic` 用 `newline=''` 写，零翻译）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

BOM = '\ufeff'


@dataclass(frozen=True)
class SourceStyle:
    """一份源文件的载体风格。"""

    #: 开头是否带 UTF-8 BOM
    bom: bool = False
    #: 行分隔符（`'\\n'` 或 `'\\r\\n'`）
    newline: str = '\n'

    def describe(self) -> str:
        """人话（报告 / 日志用）。"""
        nl = 'CRLF' if self.newline == '\r\n' else 'LF'
        return f'{nl}{"+BOM" if self.bom else ""}'

    def apply(self, text: str) -> str:
        """把文本按这份风格铺回去（**落盘前的最后一步**）。"""
        body = str(text or '')
        # 先统一成 LF，再按目标换行符展开 —— 不然会写出 `\r\r\n`。
        body = body.replace('\r\n', '\n').replace('\r', '\n')
        if self.newline != '\n':
            body = body.replace('\n', self.newline)
        if self.bom and not body.startswith(BOM):
            body = BOM + body
        return body


#: 不改变任何字节的风格（新建文件 / 不知道源时的默认）。
PLAIN = SourceStyle()


def detect(path) -> SourceStyle:
    """
    读 `path` 的**原始字节**，判定 BOM 与换行风格。

    读不出来（文件不存在 / 权限）→ `PLAIN`（调用方不该因此失败：
    这只是一层"还原"，不知道就不动）。
    """
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError:
        return PLAIN
    bom = data.startswith(b'\xef\xbb\xbf')
    crlf = data.count(b'\r\n')
    lf = data.count(b'\n') - crlf
    # 一个换行都没有（单行文件）→ LF 是安全默认
    newline = '\r\n' if crlf > lf else '\n'
    return SourceStyle(bom=bom, newline=newline)


def read_text(path) -> tuple:
    """
    读源文件 → `(文本, 风格)`。

    文本**已剥掉开头 BOM、换行已统一成 LF**（解析器要的就是这个形态），
    风格留给写盘时 `apply()` 还原。正文中间出现的 `\\ufeff` 是作者内容，
    一个字不动（只在最开头剥一个）。
    """
    style = detect(path)
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return '', style
    if text.startswith(BOM):
        text = text[1:]
    return text.replace('\r\n', '\n').replace('\r', '\n'), style


__all__ = ['BOM', 'PLAIN', 'SourceStyle', 'detect', 'read_text']
