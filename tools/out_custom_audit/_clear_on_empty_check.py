import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')
from storygraph.story import statement_registry as SR
root=Path(tempfile.mkdtemp(prefix='audit_clear_on_empty_'))/'proj'; (root/'game').mkdir(parents=True)
p=root/'storygraph.rules.json'
p.write_text(json.dumps({'statements':[{'name':'swim_suit'}],'attr_groups':[{'name':'outfit','label':'服装','options':['swim_suit']},{'name':'pose','label':'动作','options':['stand']}]}, ensure_ascii=False, indent=2), encoding='utf-8')
SR.clear_cache()
print('before:', [g['name'] for g in json.loads(p.read_text(encoding='utf-8'))['attr_groups']])
res=SR.declare_attr_groups(root, [], replace=True)
print('res:', res)
data=json.loads(p.read_text(encoding='utf-8'))
print('after attr_groups:', data.get('attr_groups'))
print('statements:', [s['name'] for s in data.get('statements',[])])
print('backups:', [x.name for x in (root/'storygraph.rules.backup').glob('*.json')])
