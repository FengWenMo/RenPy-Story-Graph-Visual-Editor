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

⚠ **混合换行**（一部分 CRLF、一部分 LF）从前一律按"占多数的那一种"统一 ——
2026-10-25《不安全因素》第二轮审计实测：50/50 的文件被判成 LF，前 4 行 CRLF
**静默变成 LF**，而用户一处都没改。现在 `apply(text, source=路径)` 会**逐行**
还原（没碰过的行原封不动，插入的新行借前一行的换行符）；不给 `source`
时仍是老行为（整份统一，供新建文件用）。`write_text_atomic` 用
`newline=''` 写，零翻译 —— 这一层铺成什么，盘上就是什么。
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

    def apply(self, text: str, source=None) -> str:
        """
        把文本按这份风格铺回去（**落盘前的最后一步**）。

        `source` = 这段文本要写回的那个文件（路径）。给了它就走**逐行还原**
        （P1-5，2026-10-25《不安全因素》第二轮）：原文那一行是 CRLF 就还
        CRLF、是 LF 就还 LF，**没碰过的行一个字节都不动**；不给就退回
        "整份翻成一种风格"的老行为（新建文件 / 不知道源时用这条）。
        """
        body = str(text or '')
        # 先统一成 LF，再按目标换行符展开 —— 不然会写出 `\r\r\n`。
        body = body.replace('\r\n', '\n').replace('\r', '\n')
        if source is not None:
            body = _restore_line_endings(body, source, self.newline)
        elif self.newline != '\n':
            body = body.replace('\n', self.newline)
        if self.bom and not body.startswith(BOM):
            body = BOM + body
        return body


def _split_eols(raw: str) -> tuple:
    """`(每行内容, 每行自己的换行符)` —— 与 `str.splitlines()` 同一口径。"""
    keep = raw.splitlines(keepends=True)
    plain = raw.splitlines()
    if len(keep) != len(plain):
        return plain, ['' for _ in plain]
    eols = [(chunk[len(line):] if chunk.startswith(line) else '')
            for chunk, line in zip(keep, plain)]
    return plain, eols


def _dominant_eol(eols, fallback: str) -> str:
    """占多数的那个换行符（一个都没有就用 `fallback`）。"""
    pool = [e for e in (eols or ()) if e]
    if not pool:
        return fallback or '\n'
    return max(set(pool), key=pool.count)


def _align_eols(rows, old_lines, old_eols, fallback: str) -> list:
    """
    新文本每一行该用哪个换行符：**对齐原文**。

    没动过的行一对一配上、用它自己那一行的换行符；新插进来的行借它前一行
    的（文件末尾插入就借整份文件的主导风格）。
    """
    import difflib

    dominant = _dominant_eol(old_eols, fallback)
    out = [None] * len(rows)
    matcher = difflib.SequenceMatcher(a=list(old_lines), b=list(rows),
                                      autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for k in range(j2 - j1):
                out[j1 + k] = old_eols[i1 + k]
        elif tag == 'insert':
            prev = old_eols[i1 - 1] if i1 > 0 else dominant
            for k in range(j1, j2):
                out[k] = prev
        elif tag == 'replace':
            for k in range(j1, j2):
                src = i1 + (k - j1)
                if src >= i2:                   # 换得比原文多：沿用最后一行
                    src = i2 - 1 if i2 > i1 else i1 - 1
                out[k] = (old_eols[src] if 0 <= src < len(old_eols)
                          else dominant)
    return [e or dominant for e in out]


def _restore_line_endings(body: str, source, fallback: str) -> str:
    """
    **逐行**还原换行符（P1-5）：混合换行文件里，没碰过的行不许变。

    为什么不能只按"主导风格"铺（`apply` 从前的做法）：混合文件里 50/50 时
    根本不存在"主导"，`detect()` 判 LF → 前 4 行 CRLF **静默变 LF**
    （审计实测：用户一处都没改，字节却变了，git diff 脏、版本对照乱）。

    单一风格（绝大多数文件）走老路径 —— 与从前逐字节等价，且不用对齐。
    """
    try:
        raw = Path(source).read_bytes().decode('utf-8', 'replace')
    except OSError:
        return body                    # 读不到就照老路：不知道的不动
    if raw.startswith(BOM):
        raw = raw[1:]
    old_lines, old_eols = _split_eols(raw)
    styles = {e for e in old_eols if e}
    if len(styles) <= 1:
        if fallback and fallback != '\n':
            return body.replace('\n', fallback)
        return body
    rows = body.split('\n')
    trailing = bool(rows) and rows[-1] == ''
    if trailing:
        rows = rows[:-1]
    if not rows:
        return body
    eols = _align_eols(rows, old_lines, old_eols, fallback)
    out = ''.join(r + e for r, e in zip(rows[:-1], eols[:-1]))
    out += rows[-1]
    if trailing:
        out += eols[-1] or fallback or '\n'
    return out


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
