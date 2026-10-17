import json, os, sys, tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')
from PySide6.QtWidgets import QApplication, QMessageBox
app=QApplication.instance() or QApplication([])
root=Path(tempfile.mkdtemp(prefix='audit_game_manifest_'))/'proj'; (root/'game').mkdir(parents=True)
(root/'game'/'storygraph.rules.json').write_text(json.dumps({'attr_groups':[{'name':'outfit','label':'服装','options':['swim_suit']}],'statements':[{'name':'swim_suit'}]}, ensure_ascii=False), encoding='utf-8')
from storygraph.story import statement_registry as SR
from storygraph.ui_qt.dialog_learn import CustomStatementDialog
SR.clear_cache(); reg=SR.StatementRegistry.for_project(root)
print('registry groups from game manifest:', [(g.name,g.label) for g in reg.attr_groups])
dlg=CustomStatementDialog(None, project_root=root, discovered=[], registry_report=SR.format_registry_report(reg), declared_groups=reg.attr_groups)
dlg.show(); app.processEvents(); dlg.tabs.setCurrentIndex(3); app.processEvents()
print('manage_view first lines:', '\n'.join(dlg.manage_view.toPlainText().splitlines()[:5]))
orig=QMessageBox.question; origw=QMessageBox.warning
QMessageBox.question=staticmethod(lambda *a,**k: QMessageBox.Yes)
QMessageBox.warning=staticmethod(lambda *a,**k: None)
try:
    dlg._clear_groups()
    print('clear status:', dlg._manage_status.text())
finally:
    QMessageBox.question=orig; QMessageBox.warning=origw
print('files:', [str(p.relative_to(root)) for p in root.rglob('storygraph.rules.json')])
