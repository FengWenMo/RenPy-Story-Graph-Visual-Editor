import sys
from pathlib import Path
sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')
sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor\tools\out_custom_audit')
from audit_project import build_audit_project, write_facts
from storygraph.app import beats as B
from storygraph.authoring.spec_groups import parse_attr_groups
from storygraph.core import engine_facts as EF
import tempfile
root = build_audit_project(Path(tempfile.mkdtemp(prefix='audit_apply_'))/'proj')
write_facts(root)
f = EF.loads((root/'storygraph.engine_facts.json').read_text(encoding='utf-8'))
groups = parse_attr_groups([{'name':'outfit','label':'服装','options':['swim_suit','uniform']},
                            {'name':'pose','label':'动作','options':['stand','wave']},
                            {'name':'expressions','label':'神态','options':['happy','sad']}])
class Sym:
    project_root=str(root); expressions={}; project_words={'expression':set(),'position':set()}
    def attrs_of_tag(self,t): return ['swim_suit','uniform','stand','wave','happy','sad']
    def expression_word_for(self,t,a): return ''
s=Sym()
for spec in ['mix swim_suit stand','mix swim_suit happy']:
    rows=B.attr_slot_rows(spec,s,groups,f)
    print(spec, '->', [(r['group'],r['label'],r['value'],r['options']) for r in rows])
    for g,v in [('outfit','stand'),('pose','wave'),('pose','stand')]:
        print('   apply',g,v,'->',B.apply_attr_slot(spec,g,v,symbols=s,groups=groups,facts=f))
