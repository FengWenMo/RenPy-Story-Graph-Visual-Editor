"""
`.docx` → 纯文本（S4）
========================

用户 2026-10-10 拍板的口径：

> docx：**标准库** `zipfile` + `xml.etree`（最大限度降低依赖）。

项目依赖只有 openpyxl/PySide6/Pillow，**没有 python-docx**，所以这里自己抽段落。

## `.docx` 其实是个 zip

    a.docx
      ├─ [Content_Types].xml
      ├─ word/document.xml      ← 正文在这里
      ├─ word/styles.xml
      └─ word/media/…

正文是 `<w:body>` 下一串 `<w:p>`（段落），段落里是 `<w:r>`（run），
run 里是 `<w:t>`（文本）；`<w:br/>` 是**行内换行**，`<w:tab/>` 是制表符。

## 抽取规则（**只做"变成纯文字"这一件事**）

| Word 里的东西 | 我们怎么处理 | 为什么 |
|---|---|---|
| 段落 | **一行**（`\\n` 分隔） | 与 `.txt` 里的"一行"完全同构，后续链路直接复用 |
| `<w:br/>` 行内换行 | 也当**一行** | 作者用 Shift+Enter 分行，就是"想换行" |
| run 之间的空格/格式 | 原样拼接 | 文本内容归 `w:t`，不猜格式 |
| 表格 | 每个单元格**各自成段**（按 XML 顺序） | Word 的 `<w:p>` 本来就在 `<w:tc>` 里，自然就成行 |
| 超链接 / 文本框 | 一起抽（它们在 `<w:p>` 里） | 文本框是作者写的台词，不能静默丢 |
| 页眉 / 页脚 / 批注 / 脚注 | **不抽** | 那不是章节正文；页眉往往是书名/页码 |
| 图片 | 不抽（`drawing` 里没有文字） | 图片不影响文本转换 |
| 样式（加粗/字号/颜色） | **全丢** | 纯文字只有"谁说了什么"，格式归 Word 自己的事 |

⚠ **不做语义猜测**：不把"标题样式"当章节头、不把列表转成 Ren'Py 的 menu。
章节头仍由 `txt_import._HEADER_RE` 认（`1.0 chapter0-0 初入大学` 那种写法）。
理由：文档里"看着像标题"的东西太多，猜错就是改结构。

## 与 `txt_import` 的关系

本模块**只产文本**，后续完全复用 `txt_import.convert_text`（行型判定、括号当注释、
角色映射、往返一致……）。于是 `.docx` 与 `.txt` 走的是**同一条链路**，
不存在"两套转换规则"。
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

#: OOXML 主命名空间（正文里所有 `w:` 元素都在它下面）
_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

#: zip 里的成员名（大小写敏感；Word 一直用这个）
_DOCUMENT = 'word/document.xml'
_DOCUMENT_ALT = 'word/document2.xml'

#: 老 `.doc`（OLE 复合文档）的魔数 —— 认出来要给人**能照做的**提示
_OLE_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
#: 所有 docx/pptx/xlsx 都是 `PK\x03\x04`
_ZIP_MAGIC = b'PK\x03\x04'


def is_zip(data: bytes) -> bool:
    return bytes(data or b'')[:4] == _ZIP_MAGIC


def is_legacy_doc(path) -> bool:
    """`.doc`（不是 `.docx`）—— 二进制格式，标准库解不了。"""
    try:
        with open(path, 'rb') as fh:
            return fh.read(8) == _OLE_MAGIC
    except OSError:
        return False


def _tag(name: str) -> str:
    return f'{{{_W}}}{name}'


def _text_of(paragraph: ET.Element) -> str:
    """
    一个 `<w:p>` → 一行文本（**保留行内换行与制表符**）。

    遍历的是段落的**全部后代**（`iter`）而不是只找直接的 `w:r`：
    run 可能被包在 `<w:hyperlink>`、`<w:smartTag>`、`<mc:AlternateContent>`
    （文本框）里 —— 只认直接子节点会**静默丢掉**那些文字（作者写的台词）。
    """
    parts = []
    for node in paragraph.iter():
        tag = node.tag
        if tag == _tag('t'):
            parts.append(node.text or '')
        elif tag == _tag('br'):
            parts.append('\n')            # Shift+Enter：作者就是想换行
        elif tag == _tag('tab'):
            parts.append('\t')
    return ''.join(parts)


def _paragraphs(body: ET.Element) -> list:
    """`<w:body>` → `[一行文本]`（按 XML 顺序；表格单元格里的段落也在其中）。"""
    lines = []
    for para in body.iter(_tag('p')):
        text = _text_of(para)
        for piece in text.split('\n'):
            lines.append(piece.rstrip())
    return lines


def document_bytes(data: bytes) -> bytes:
    """zip 字节 → `word/document.xml` 的字节（读不到就抛清楚的原因）。"""
    raw = bytes(data or b'')
    if not raw:
        raise ValueError('空文件')
    if raw[:8] == _OLE_MAGIC:
        raise ValueError(
            '这是老的 .doc（OLE 二进制）格式，不是 .docx —— '
            '请在 Word/WPS 里"另存为" .docx（或直接存成 .txt）再导入')
    if not is_zip(raw):
        raise ValueError('不是 .docx（zip）文件：开头没有 PK 标记'
                         '（是不是存成了 .rtf / .pdf / .doc？）')
    import io
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = set(zf.namelist())
            for member in (_DOCUMENT, _DOCUMENT_ALT):
                if member in names:
                    return zf.read(member)
            if not any(n.startswith('word/') for n in names):
                raise ValueError('zip 里没有 word/ 目录 —— 不像 .docx'
                                 '（是不是 .odt / .epub？）')
            raise ValueError(f'zip 里没有 {_DOCUMENT} —— 这份 .docx 不完整')
    except zipfile.BadZipFile as exc:
        raise ValueError(f'.docx 解不开（zip 损坏）：{exc}') from exc


def docx_text_from_bytes(data: bytes) -> str:
    """
    `.docx` 字节 → 纯文本（**纯函数**：确定性、不写盘、不读配置）。

    返回**没有末尾换行**的文本（行之间用 `\\n`）—— 与 `read_text_file` 的
    形状一致，调用方直接喂 `txt_import.convert_text`。
    """
    xml = document_bytes(data)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f'word/document.xml 不是合法 XML：{exc}') from exc
    body = root.find(_tag('body'))
    if body is None:
        # 有些生成器把 body 放在别处/没有 body —— 那就从根开始找段落，
        # 宁可多抽也不静默给空（读者一眼能看出不对）
        body = root
    lines = _paragraphs(body)
    while lines and not lines[-1].strip():
        lines.pop()                       # Word 文档末尾那串空段落没用
    while lines and not lines[0].strip():
        lines.pop(0)
    return '\n'.join(lines)


def docx_text(path) -> str:
    """读 `.docx` 文件 → 纯文本（**不静默吞异常**：读不出来要说清原因）。"""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise ValueError(f'读不了 {p.name}：{exc}') from exc
    return docx_text_from_bytes(data)


__all__ = ['docx_text', 'docx_text_from_bytes', 'document_bytes',
           'is_legacy_doc', 'is_zip']
