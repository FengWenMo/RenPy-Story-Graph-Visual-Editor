"""诊断：别名写进表之后，`_reload_rows` / `_reload_character_rows` 到底读到了什么。"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.app.editor import EditorSession                 # noqa: E402
from storygraph.authoring import char_decisions as CD           # noqa: E402
from storygraph.authoring import txt_pipeline as TP             # noqa: E402

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
TXT_DIR = Path(r'D:\千连万理\txt--renpy脚本转换工具')

tmp = Path(tempfile.mkdtemp(prefix='s3dbg_'))
copy = tmp / 'sr_general'
shutil.copytree(REAL, copy, dirs_exist_ok=True)
s = EditorSession(project_root=copy)
s.load_assets()

print('asset_dir_of   =', CD.asset_dir_of(copy))
print('session rows   =', len(s.character_rows()), '个角色')
print('  mm 的别名    =', s.character_rows().get('mm', {}).get('Aliases'))

fp = sorted(TXT_DIR.glob('1-0*-定稿.txt'))[0]
plan = TP.build_plan(copy, [fp], s.character_rows())
props = TP.proposals_for(plan, s.character_rows())
print('待定写法       =', [p.token for p in props])

stats = s.apply_character_decisions([{'token': 'MOM', 'action': 'existing',
                                      'target': 'mm'}])
print('apply 统计     =', {k: v for k, v in stats.items() if k != 'messages'})
print('session rows 之后 mm 的别名 =',
      s.character_rows().get('mm', {}).get('Aliases'))

rows2 = TP._reload_rows(copy, s.character_rows())
print('TP._reload_rows  mm 的别名 =', rows2.get('mm', {}).get('Aliases'))

from storygraph.authoring.txt_import import CharacterMap        # noqa: E402
print('lookup(MOM) 用 session 表 =', CharacterMap(s.character_rows()).lookup('MOM'))
print('lookup(MOM) 用 reload 表  =', CharacterMap(rows2).lookup('MOM'))
plan2 = TP.build_plan(copy, [fp], rows2)
print('重跑后还剩   =', [p.token for p in TP.proposals_for(plan2, rows2)])
