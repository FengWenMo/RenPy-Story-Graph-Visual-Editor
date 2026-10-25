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


class SourceNotUtf8(ValueError):
    """
    要写回去的那份源文件**不是合法 UTF-8** —— 拒绝写。

    为什么必须在这一层拦（2026-10-25《不安全因素》第五轮，P0-B）：

      读源文件的唯一入口 `read_text()` 是 `errors='replace'` 的（解析器要能
      读完整份文件，一个坏字节不该让整章打不开）。可一旦**读回来再写回去**，
      那个 U+FFFD 就会以三个字节的身份留在盘上：用户的 0xE9 变成 `EF BF BD`，
      `git diff` 说这行改过，而人从没碰过它 —— 而且 `write_text_atomic` 默认
      不备份，原字节**不可恢复**。

      第三轮审计只给"新建章节"和"选择点"两条路各加了一次严格解码预检，
      于是同一个仓库里"追加"拒写、"接线 / 删拍 / 撤销"照旧腐蚀 —— 这说明
      修在调用点上必然漏。`apply(..., source=路径)` 是**所有落盘点唯一的
      共同末班车**（它本来就要读一遍源字节来判断每行的换行符），
      把严格解码放在这里，才没有下一处漏网。

    `offset` 是第一个坏字节的下标（报错里要给出坐标，人才知道去哪儿修）。
    """

    def __init__(self, path, offset: int = 0, reason: str = ''):
        self.path = str(path)
        self.offset = int(offset or 0)
        super().__init__(
            f'{self.path} 不是 UTF-8（Ren\'Py 只认 UTF-8）：'
            f'第 {self.offset} 字节起{("，" + reason) if reason else ""}。'
            f'为了不改坏原字节，这次没有写。')


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

        ⚠ **给了 `source` 就会严格解码那份文件**：解不开抛
        `SourceNotUtf8`（P0-B，2026-10-25 第五轮）。为什么不是在各个调用点
        自己预检：这里是"读源字节"和"要写盘"唯一同时在场的地方，
        修在调用点上必然漏（第三轮就是这么漏掉了接线 / 删行 / undo 的）。

        所以：**凡是"读回来再写回去"的路径，一律传 `source=`** —— 那一传，
        既拿到了逐行换行符，也拿到了"字节能不能碰"的判据。
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
    """
    `(每行内容, 每行自己的换行符)` —— **只按 `\\n` 切**。

    ⚠ 为什么不能用 `str.splitlines()`（2026-10-25《不安全因素》第五轮，P0-A）：

      `splitlines()` 认的"行边界"远不止 `\\n` —— 它把 `U+2028`(LINE
      SEPARATOR)、`U+2029`、`U+0085`(NEL)、`\\x0b`、`\\x0c`、`\\x1c`~`\\x1e`
      都当换行。Ren'Py 的 lexer **只认 `\\n`**。于是这些字符一旦出现在**一行
      中间**（从网页 / Word 粘台词最容易带进来 U+2028），两条"行"的账就对不上：

        原文 `    "hi<U+2028>there"\\n    "b"\\n`（2 行）
        splitlines 数出 3 行，第 2 行的"换行符"被记成 `U+2028`；
        逐行还原时 `apply` 把真正的 `\\n` 换成了 `U+2028` ——

      结果：**用户没碰过的那一行，行尾换行符被换成了一个 Ren'Py 不认的字符**，
      两条语句并成一条（脚本直接语法错），而 `git diff` 只显示"这行变了"。
      审计探针实测：4 种字符 × 中段插入 = 4/4 全部被改写。

      原文没有 `\\n` 时最后一行没有换行符（`''`），下面靠它判"末尾要不要补"。
    """
    if not raw:
        return [], []
    from storygraph.core.textlines import split_eols
    return split_eols(raw)


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
        raw_bytes = Path(source).read_bytes()
    except OSError:
        return body                    # 读不到就照老路：不知道的不动
    # ⚠ **严格解码**（P0-B）：这段字节马上就要被"读回来再写回去"。
    # `errors='replace'` 会把坏字节换成 U+FFFD，然后再以三个字节的身份写回盘上
    # —— 用户的 0xE9 变成 `EF BF BD`，无提示、无备份、不可恢复。
    # 解不开就抛 `SourceNotUtf8`，让**落盘点**拒绝写（一处拦，处处拦）。
    try:
        raw = raw_bytes.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise SourceNotUtf8(source, exc.start) from exc
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

    ⚠ **有损**（`errors='replace'`）：坏字节变成 U+FFFD，原文找不回来了。
    所以它只适合"看" —— 解析、比较、显示。**任何要把文本写回去的地方**，
    必须走 `apply(..., source=路径)`（那里会严格解码，坏字节直接拒写）。

    文本**已剥掉开头 BOM、换行已统一成 LF**（解析器要的就是这个形态），
    风格留给写盘时 `apply()` 还原。正文中间出现的 `\\ufeff` 是作者内容，
    一个字不动（只在最开头剥一个）。

    只把 `\\r\\n` 折成 `\\n`；**孤立的 `\\r` 也折**（历史行为，保持不动）——
    见 `apply()` 的 `_restore_line_endings` 说明：那种文件 Ren'Py 自己也
    读不成行，属已知边界。
    """
    style = detect(path)
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return '', style
    if text.startswith(BOM):
        text = text[1:]
    return text.replace('\r\n', '\n').replace('\r', '\n'), style


__all__ = ['BOM', 'PLAIN', 'SourceNotUtf8', 'SourceStyle', 'detect', 'read_text']
