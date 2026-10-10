"""看 symtable 为什么报 layered_model.py:658 'idx'（只读诊断）。"""

import builtins
import symtable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'storygraph' / 'story' / 'layered_model.py'
top = symtable.symtable(P.read_text(encoding='utf-8'), str(P), 'exec')
module_names = {s.get_name() for s in top.get_symbols()}


def walk(table, path):
    for sym in table.get_symbols():
        name = sym.get_name()
        if (sym.is_referenced() and sym.is_global()
                and name not in module_names and not hasattr(builtins, name)):
            print(f'  {table.get_type():9s} {table.get_name():30s} '
                  f'line={table.get_lineno():5d}  name={name!r}  '
                  f'path={path}')
    for child in table.get_children():
        walk(child, path + '/' + child.get_name())


walk(top, '')

print('\n--- `_expand_composite_call` / `_expand_node` 的符号表细节 ---')
for child in top.get_children():
    if child.get_name() not in ('_expand_composite_call', '_expand_node'):
        continue
    print(f'{child.get_name()}  line={child.get_lineno()} '
          f'type={child.get_type()}')
    for sym in child.get_symbols():
        print(f'    {sym.get_name():16s} global={sym.is_global()} '
              f'ref={sym.is_referenced()} local={sym.is_local()} '
              f'param={sym.is_parameter()}')
    for gc in child.get_children():
        print(f'    [inner] {gc.get_name()} line={gc.get_lineno()}')
