import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import storygraph.authoring.txt_import as m

print('ABBREV =', m._ABBREV_TOKEN_RE.pattern)

CHARS = {'jc': {'ID': 'jc', 'Name': '「蒋忱」'}}
COLLIDE = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
           'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'},
           'jc': {'ID': 'jc', 'Name': '「蒋忱」'}}

for name, table in (('CHARS', CHARS), ('COLLIDE', COLLIDE)):
    cm = m.CharacterMap(table)
    print(f'\n--- {name}: by_key={cm.by_key}')
    print(f'    shadowed={ {k: sorted(v) for k, v in cm._shadowed.items()} }')
    for tok in ('L_J_K', 'LJK', 'Ljk', 'ljk', 'MOM', 'OK', 'USA', 'A'):
        print(f'    lookup({tok!r})={cm.lookup(tok)}  '
              f'looks={m._looks_like_speaker(tok, cm)}')
        r = m.convert_text(f'{tok}"内容"', characters=table)
        line = [x for x in r.rpy_text.splitlines()
                if x.strip() and not x.startswith('label')]
        print(f'        -> {line[0].strip() if line else ""}')
