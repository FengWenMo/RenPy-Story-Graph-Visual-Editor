"""S4 实机演练：造一个"像 Word 导出的" .docx → 走完整纯文字导入链路。

    python tools/probe_s4_docx.py
"""

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
WPS = 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape'

DOC = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:mc="{MC}" xmlns:wps="{WPS}">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
         <w:r><w:t>1.0 chapter0-0 初入大学</w:t></w:r></w:p>
    <w:p><w:r><w:t>（bg1.1）（bgm_quiet）</w:t></w:r></w:p>
    <w:p><w:r><w:t xml:space="preserve">MOM</w:t></w:r>
         <w:r><w:t>“跟妈客气啥，上班顺手的事儿。”</w:t></w:r></w:p>
    <w:p><w:r><w:t>妈妈说</w:t></w:r><w:r><w:br/></w:r>
         <w:r><w:t>“同学之间一定要和睦。”</w:t></w:r></w:p>
    <w:p>
      <w:hyperlink r:id="rId1">
        <w:r><w:t>「蒋忱」</w:t></w:r></w:hyperlink>
      <w:r><w:t>：你终于来了。</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>世界上再也没有什么比</w:t></w:r>
         <w:r><w:t>“自由”</w:t></w:r><w:r><w:t>更重要</w:t></w:r></w:p>
    <w:tbl>
      <w:tr><w:tc><w:p><w:r><w:t>表格第一格</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>表格第二格</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
    <w:p><mc:AlternateContent>
        <mc:Fallback><w:r><w:t>文本框里的备注</w:t></w:r></mc:Fallback>
    </mc:AlternateContent></w:p>
    <w:p/>
  </w:body>
</w:document>'''

tmp = Path(tempfile.mkdtemp(prefix='s4_'))
docx = tmp / '1-0初入大学-定稿.docx'
with zipfile.ZipFile(docx, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('[Content_Types].xml', '<Types/>')
    zf.writestr('word/document.xml', DOC)
    zf.writestr('word/styles.xml', f'<w:styles xmlns:w="{W}"/>')
print(f'造的 .docx：{docx.name}（{docx.stat().st_size} 字节，真 zip + 真 OOXML）')

from storygraph.authoring.docx_import import docx_text              # noqa: E402

text = docx_text(docx)
print('\n抽出来的文本：')
for i, line in enumerate(text.splitlines(), 1):
    print(f'  {i:2d} | {line}')

# —— 走完整链路（项目目录里放一张角色表）——
from storygraph.assets.asset_registry import (                       # noqa: E402
    _TABLE_FIELDS, write_dict_to_excel)
from storygraph.authoring import txt_pipeline as TP                  # noqa: E402

proj = tmp / 'proj'
(proj / 'game' / 'asset_tables').mkdir(parents=True)
write_dict_to_excel(proj / 'game' / 'asset_tables' / 'characters.xlsx',
                    {'jc': {'ID': 'jc', 'Name': '「蒋忱」'},
                     'mm': {'ID': 'mm', 'Name': '妈妈'}},
                    _TABLE_FIELDS['character'])
rows = {'jc': {'ID': 'jc', 'Name': '「蒋忱」'},
        'mm': {'ID': 'mm', 'Name': '妈妈'}}


def _ask(proposals):
    out = []
    for p in proposals:
        if p.token == 'MOM':
            out.append({'token': 'MOM', 'action': 'existing', 'target': 'mm'})
        else:
            out.append({'token': p.token, 'action': 'new'})
    return out


res = TP.run(proj, [docx], rows=rows, project_root=proj, ask=_ask)
print(f"\n落盘 {len(res.written)} 个；被拦 {len(res.blocked)} 个；"
      f"跳过 {len(res.skipped)} 个（`TP.run` 返回 ImportResult，不是 dict）")
for path, why in res.skipped:
    print('  ⚠', Path(path).name, why)
for path, why in res.blocked:
    print('  🚫', Path(path).name, why[:80])
if res.written:
    out = Path(res.written[0])
    print(f'\n产物 {out.name}：')
    for line in out.read_text(encoding='utf-8').splitlines():
        print('  |', line)
