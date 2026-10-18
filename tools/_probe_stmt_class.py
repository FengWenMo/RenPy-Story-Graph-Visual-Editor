import sys

sys.path.insert(0, '.')
from storygraph.story.beat_model import (
    _is_bare_renpy_verb, _looks_like_renpy_statement)

cases = [
    's "Sure, but what\'s a \\"visual novel?\\""',
    's "Sure, but what\'s a "visual novel?""',
    'e "hello"',
    '"narration line"',
    'm "It\'d be hard for me to make a visual novel alone."',
    'm "It\'s a videogame."',
]
for c in cases:
    print(repr(c), '-> statement?', _looks_like_renpy_statement(c),
          '| verb?', _is_bare_renpy_verb(c))
