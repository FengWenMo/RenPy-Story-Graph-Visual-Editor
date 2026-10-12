"""真机验证：把"服装"和"表情"分开（用户要实测的那件事）。

    python tools/probe_real_outfit_groups.py

⚠ 不碰真项目：复制一份（忽略 .git / saves / build），在副本里写声明。
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
if not REAL.is_dir():
    print('真机项目不在本机，跳过')
    raise SystemExit(0)

from storygraph.app.beats import expression_groups_for                 # noqa: E402
from storygraph.app.editor import EditorSession                        # noqa: E402
from storygraph.pipeline.build import load_asset_registry              # noqa: E402
from storygraph.story import statement_registry as SR                  # noqa: E402
from storygraph.story.beat_render import make_symbols                  # noqa: E402

work = Path(tempfile.mkdtemp(prefix='outfitgrp_')) / 'sr_general'
shutil.copytree(REAL, work,
                ignore=shutil.ignore_patterns('.git', 'saves', 'build',
                                              '*.rpyc', 'log.txt'))
print('副本：', work)

reg = load_asset_registry(work)
symbols = make_symbols(reg)
tags = sorted({str(r.get('Tag') or '') for r in reg.images.values()
               if r.get('Tag')})
print('资源表里有哪些 tag：', tags[:12],
      f'（共 {len(tags)}，图片行 {len(reg.images)}）')

# ⚠ 挑 tag 的判据：**像"表情"的属性最多的那个**。
#    我第一版挑"属性最多" → 挑到 `ui`（692 个界面控件属性：button/frame/…），
#    那个演示毫无意义（真机上当场看出来）。`characters` 表里也有 `ui`。
import re as _re                                                       # noqa: E402
_FACE = _re.compile(r'happy|calm|sad|angry|smile|sigh|shock|shy|face|'
                    r'cry|laugh|blank|serious|surprise', _re.I)
scored = []
for t in tags:
    a = [str(x) for x in (symbols.attrs_of_tag(t) or []) if str(x).strip()]
    scored.append((len([x for x in a if _FACE.search(x)]), len(a), t, a))
scored.sort(key=lambda r: (-r[0], -r[1]))
best, best_attrs = scored[0][2], scored[0][3]
print(f'\n=== 角色 `{best}` 的属性候选（{len(best_attrs)} 个，'
      f'其中像表情的 {scored[0][0]} 个）')
print('  ', best_attrs[:24])
print('  其它 tag 得分（像表情的个数 / 属性总数）：',
      [(t, n, m) for n, m, t, _a in scored[:5]])

print('\n=== ① 项目**没有**声明词组时（一般人就是这个状态）')
SR.clear_cache()
print('  declared_attr_groups =', SR.StatementRegistry.for_project(work).attr_groups)
print('  expression_groups_for =', expression_groups_for(best, symbols, []))
print('  → 平铺候选，界面上一个栏都没有（通用性底线）')

print('\n=== ② 声明两组：服装 / 表情（按这个项目**真实存在**的词来写）')
outfit_pool = [a for a in best_attrs
               if _re.search(r'swim|uniform|casual|naked|dress|cloth|suit',
                             a, _re.I)]
expr_pool = [a for a in best_attrs if _FACE.search(a)]
print('  这个角色身上像服装的：', outfit_pool[:8] or '（没有 → 用 swim_* 通配）')
print('  这个角色身上像表情的：', expr_pool[:8] or '（没有 → 用 happy/calm/sad）')
res = SR.declare_attr_groups(work, [
    {'name': 'outfit', 'label': '服装',
     'options': outfit_pool[:8] or ['swim_*']},
    {'name': 'expression', 'label': '表情',
     'options': expr_pool[:8] or ['happy', 'calm', 'sad']},
    {'name': 'rest', 'label': '其他'},
])
print('  写入结果：', {k: v for k, v in res.items() if k != 'path'})
SR.clear_cache()
reg_obj = SR.StatementRegistry.for_project(work)
print('  现在声明的组：',
      [(g.name, g.label, len(g.options)) for g in reg_obj.attr_groups])

print('\n=== ③ 分栏结果')
groups = expression_groups_for(best, symbols, reg_obj.attr_groups)
for g in groups:
    vals = g['values']
    print(f"  【{g['label'] or g['group']}】{len(vals)} 个：", vals[:12])
# ⚠ 覆盖率要跟**这个 tag 的全部属性**比：平铺候选有 limit=500 截断，
#    拿它当基准会得出"分组丢了词"的**假警报**（我第一版就是这么错的）
allattrs = [a for a in (symbols.attrs_of_tag(best) or []) if str(a).strip()]
allgrouped = [v for g in groups for v in g['values']]
print(f'\n  这个 tag 的属性共 {len(allattrs)} 个；分组覆盖 {len(allgrouped)} 个 —— '
      f'一个都没丢 = {set(allgrouped) == set(map(str, allattrs))}')

print('\n=== ④ 会话把声明交给前端（界面拿到的就是这个）')
s = EditorSession(project_root=work)
print('  EditorSession.declared_attr_groups =',
      [(g.name, g.label) for g in s.declared_attr_groups()])
print('\n清单文件：', work / 'storygraph.rules.json')
print((work / 'storygraph.rules.json').read_text(encoding='utf-8')[:400])

# ⚠⚠ 跑完**必须删掉副本**：2026-10-11 实测这条探针漏了清理，
#   TEMP 里堆了 6 份 3.6GB 的 `sr_general`，一共吃掉 **46.9GB** 磁盘 ——
#   与本仓早先那次"把 C 盘写满"是同一类事故。
#   纪律：**探针跑完，临时目录要为 0 个**（有静态判据钉这一条）。
shutil.rmtree(work.parent, ignore_errors=True)
print('（副本已删）', work.parent)
