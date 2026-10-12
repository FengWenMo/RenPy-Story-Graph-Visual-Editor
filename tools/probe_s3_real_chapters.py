"""实机演练：真机 5 章定稿 txt 走完整 S3 流程（**在真机项目的副本里**，只读原件）。

    python tools/probe_s3_real_chapters.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.app.editor import EditorSession                # noqa: E402

TXT_DIR = Path(r'D:\千连万理\txt--renpy脚本转换工具')
REAL = Path(r'D:\千连万理\RenpyTest\sr_general')

if not (TXT_DIR.is_dir() and REAL.is_dir()):
    print('真机素材不在本机，跳过')
    raise SystemExit(0)

tmp = Path(tempfile.mkdtemp(prefix='s3_real_'))
copy = tmp / 'sr_general'
# ⚠ 跳过 .git / saves / build（复制真机项目时把 C 盘塞满过一次）
IGNORE = shutil.ignore_patterns('.git', 'saves', 'build', 'build_out', 'tmp',
                                '__pycache__', '*.rpyc')
shutil.copytree(REAL, copy, ignore=IGNORE)
print(f'真机副本：{copy}')

files = sorted(TXT_DIR.glob('*-定稿.txt'))
print(f'输入 {len(files)} 个 txt')

s = EditorSession(project_root=copy)
s.load_assets()

asked = []


def _ask(proposals):
    """假装用户这样决定：把能对上的指过去，对不上的建新角色（立绘留空）。"""
    asked.extend(p.token for p in proposals)
    out = []
    for p in proposals:
        sug = p.suggestion
        alias = None
        for cand in sug.candidates:               # 有候选就用第一个
            alias = cand.var
            break
        if alias:
            out.append({'token': sug.token, 'action': 'existing',
                        'target': alias})
        else:
            out.append({'token': sug.token, 'action': 'new'})
    return out


res = s.import_plain_text(files, None, ask=_ask)
print('\n要人决定的写法：', asked)
print('决定统计       ：', {k: v for k, v in (res['decisions'] or {}).items()
                            if k != 'messages'})
for m in res['decisions'].get('messages', [])[:12]:
    print('   ·', m)
print('\n落盘：')
for p in res['written']:
    print('   ', Path(p).name)
for p, why in (res['blocked'] or []):
    print('    🚫', Path(p).name, why)
if res['needs_decision']:
    print('  ⚠ 还有写法没定 → 按设计**一个字都没写**：')
    for p in res['proposals']:
        cands = '、'.join(f'{c.var}({c.evidence})'
                          for c in p.suggestion.candidates)
        print(f'     · {p.token}  候选={cands or "（无）"}  现在={p.action}'
              f'  {p.note[:60]}')
print('导入：', {k: v for k, v in (res.get('import_result') or {}).items()
                if k in ('added_nodes', 'added_edges', 'nodes', 'edges')})
print('消息：')
for m in res['messages'][:8]:
    print('   ·', m)

if res['written']:
    text = Path(res['written'][0]).read_text(encoding='utf-8')
    print(f'\n第一个产物的前 6 行（{Path(res["written"][0]).name}）：')
    for line in text.splitlines()[:6]:
        print('   |', line)

# ⚠ 跑完删掉副本（2026-10-11：漏了清理，TEMP 里堆了 46.9GB 的 sr_general）
shutil.rmtree(tmp, ignore_errors=True)
