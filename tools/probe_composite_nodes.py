"""看 `_value_of` 对 `(10, 20)` 这种元组节点返回什么（决定 Composite 修法）。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story.layered_model import (_value_of, expand_displayable,     # noqa: E402
                                           parse_expr)

for src in ('(10, 20)', '(0, 0)', 'a.png', '5'):
    node = parse_expr(src)
    print(f'{src!r:12s} kind={node.kind!r:8s} value={getattr(node, "value", None)!r} '
          f'args={[getattr(a, "value", None) for a in getattr(node, "args", [])]}')
    print(f'{"":12s} _value_of -> {_value_of(node, {}, "", {}, 0)!r}')

print()
for src in (
        'Composite((300, 600), (0, 0), "body.png", (0, 0), "clothes.png")',
        'LiveComposite((1280, 720), (0, 0), "bsod_1", (0, 0), "bsod_2")',
        'Composite("a.png", "b.png")',
        'Composite((300, 600), "only.png")'):
    print(f'{src}\n    -> {expand_displayable(src, {}, "")}')
