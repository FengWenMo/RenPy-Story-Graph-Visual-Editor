import os, sys
os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')
from PySide6.QtWidgets import QApplication
app=QApplication.instance() or QApplication([])
from storygraph.ui_qt.sprite_grid import SpriteCardWidget
class Sym:
    registry=None; project_root=None; expressions={}; project_words={'expression':set(),'position':set()}
    def attrs_of_tag(self,t): return ['swim_suit','stand']
    def expression_word_for(self,t,a): return ''
class L:
    project_root=r'D:\千连万理\Renpy_Story_Graph_Editor\tools\out_custom_audit\proj_audit'
    def request(self,*a,**k): pass
card=SpriteCardWidget({'image':'lan swim_suit stand','expression':'swim_suit stand','position':'','at':'','speaking':False,'order_index':0,'extra_rows':[]},symbols=Sym(),loader=L(),on_resolve_path=lambda spec:'',assets_ready=True)
card.refresh(); app.processEvents()
print('thumb=',repr(card._thumb.text()),'missing=',card._missing)
print('chips=',[b.text() for b in card._slot_btns],'single=',card._expr_btn.text() if card._expr_btn else None)
