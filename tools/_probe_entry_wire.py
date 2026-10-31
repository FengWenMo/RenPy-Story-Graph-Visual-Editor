import time
from pathlib import Path
R = r'D:\千连万理\sr_general'
from storygraph.app.editor import EditorSession
from storygraph.app import stage_entry as SE
SE.clear_cache()
s = EditorSession(project_root=R)
f = Path(R) / 'game' / 'chapter' / 'chapter1_2.rpy'
print('load', s.load(f), '拍')
print('--- 有入口状态之前 ---')
print(s.stage_digest_text(0))
t0=time.time(); sig, rep = SE.analyze(R); t1=time.time()
print(f'analyze {t1-t0:.2f}s sig={sig[:8]}')
s.load(f)
entry = SE.entry_for(R, s.script_path)
print('entry:', entry.describe() if entry else None)
print('changed:', s.set_stage_entry(entry))
print('--- 有入口状态之后 ---')
print(s.stage_digest_text(0))
rows = s.stage_size_rows(0)
for r in rows:
    print('  row', r.get('kind'), r.get('spec'), 'source=', r.get('source'), 'why=', (r.get('why') or '')[:60])
