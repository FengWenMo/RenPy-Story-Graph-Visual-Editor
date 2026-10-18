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
a = strip_banner(src.read_bytes())[0]
b = strip_banner(out.read_bytes())[0]
print('len', len(a), len(b))
print('bom', a[:3] == b'\xef\xbb\xbf', b[:3] == b'\xef\xbb\xbf')
print('crlf', a.count(b'\r\n'), b.count(b'\r\n'))
print('lf', a.count(b'\n'), b.count(b'\n'))
print('tail a', a[-20:])
print('tail b', b[-20:])
for i, (x, y) in enumerate(zip(a, b)):
    if x != y:
        print('first diff at', i, a[max(0,i-40):i+40], b[max(0,i-40):i+40])
        break
