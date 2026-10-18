import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '.')
from storygraph.story.script_table import open_rpy_as_table
from storygraph.story.excel_import import import_beats_from_excel

src = Path(r'D:\software\renpy-8.4.1-sdk\the_question\game\script.rpy')
tmp = Path(tempfile.mkdtemp())
x = tmp / 'q.xlsx'
open_rpy_as_table(str(src), str(x))
beats = import_beats_from_excel(x)
b = next(b for b in beats if b.line_id == 'rightaway.0030')
for k in ('kind', 'text', 'raw', 'code', 'ordered', 'code_blocks',
          'menu_name', 'menu_args', 'opaque', 'comment'):
    print(f'{k} = {getattr(b, k, None)!r}'[:300])
