import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '.')
from tools.audit_byte_exact import strip_banner
from storygraph.story.script_table import open_rpy_as_table
from storygraph.pipeline.compile import compile_table

src = Path(r'D:\千连万理\renpytest\sr_general\game\chapter\chapter1_4.generated.rpy')
tmp = Path(tempfile.mkdtemp())
xlsx = tmp / 'x.xlsx'
out = tmp / 'o.rpy'
open_rpy_as_table(str(src), str(xlsx))
_p, _s, res = compile_table(str(xlsx), rpy_path=out, write_sheet=False,
                            force=True, skip_empty=True, engine_lint=False)
print('used_patch', res.used_patch, '| note', res.patch_note)
a = strip_banner(src.read_bytes())[0].decode('utf-8').splitlines()
b = strip_banner(out.read_bytes())[0].decode('utf-8').splitlines()
print('lines', len(a), '->', len(b))
import difflib
n = 0
for line in difflib.unified_diff(a, b, 'src', 'out', lineterm='', n=2):
    print(line)
    n += 1
    if n > 60:
        print('...')
        break
