import sys

sys.path.insert(0, '.')
from storygraph.story.beat_model import _split_code_cell

cases = [
    ('带块标记的 python 块（docstring）',
     '\ue001def f():\n\ue002    """说明"""\n\ue002    return 1', [1, 2, 3]),
    ('没有块标记的老表（docstring 单行）', '"""说明"""', None),
    ('菜单标题', 's "Sure, but what\'s a \\"visual novel?\\""', None),
]
for name, cell, blocks in cases:
    raw, code, _ordered, out_blocks = _split_code_cell(cell, blocks)
    print(f'--- {name}')
    print('  raw =', repr(raw))
    print('  code=', repr(code))
    print('  blocks=', out_blocks)
