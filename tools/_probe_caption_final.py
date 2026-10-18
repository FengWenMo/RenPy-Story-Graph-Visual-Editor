import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '.')
from storygraph.story.script_table import open_rpy_as_table
from storygraph.pipeline.compile import compile_table

src = Path(r'D:\software\renpy-8.4.1-sdk\the_question\game\script.rpy')
tmp = Path(tempfile.mkdtemp())
x = tmp / 'q.xlsx'
out = tmp / 'q.rpy'
open_rpy_as_table(str(src), str(x))
print('meta:', __import__('storygraph.story.table_meta', fromlist=['read_meta']).read_meta(x))
_p, _s, res = compile_table(str(x), rpy_path=out, write_sheet=False,
                            force=True, skip_empty=True, engine_lint=False)
print('used_patch:', res.used_patch)
lines = out.read_text(encoding='utf-8').splitlines()
for i, ln in enumerate(lines, 1):
    if 'visual novel?' in ln:
        print(f'{i}: {ln!r}')
print('total lines:', len(lines))
