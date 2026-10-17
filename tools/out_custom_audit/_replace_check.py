import json, tempfile, sys
from pathlib import Path
sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')
from storygraph.story import statement_registry as SR
root=Path(tempfile.mkdtemp(prefix='audit_replace_'))/'proj'; (root/'game').mkdir(parents=True)
p=root/'storygraph.rules.json'
p.write_text(json.dumps({'attr_groups':[{'name':'outfit','label':'服装','options':['swim_suit'],'when':'clothes2','备注':'手写字段'}],'statements':[{'name':'swim_suit'}]}, ensure_ascii=False, indent=2), encoding='utf-8')
SR.clear_cache()
print('before:', json.loads(p.read_text(encoding='utf-8'))['attr_groups'])
res=SR.declare_attr_groups(root,[{'name':'outfit','label':'服装','options':['swim_suit']}], replace=True)
print('res:', res)
print('after :', json.loads(p.read_text(encoding='utf-8'))['attr_groups'])
print('backup:', [x.name for x in (root/'storygraph.rules.backup').glob('*.json')])
