import os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')
from storygraph.app import beats as B
from storygraph.authoring.spec_groups import parse_attr_groups
class Sym:
    project_root=None; registry=None; expressions={}; project_words={'expression':set(),'position':set()}
    def attrs_of_tag(self,t): return ['swim_suit','uniform','happy','sad','mask']
    def expression_word_for(self,t,a): return ''
groups=parse_attr_groups([{'name':'outfit','label':'服装','options':['swim_suit','uniform']}])
for spec in ['lan swim_suit happy','lan happy','lan swim_suit uniform','lan swim_suit mask']:
    rows=B.attr_slot_rows(spec, Sym(), groups, facts=None, layer_model=None)
    print(spec,'->',[(r['group'],r['label'],r['value'],r['options'],r.get('extras')) for r in rows])
