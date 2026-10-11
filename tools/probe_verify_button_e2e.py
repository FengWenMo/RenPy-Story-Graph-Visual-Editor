"""端到端：**真的点一次「▶ 验证它能跑」**，看结论能不能显示出来。

之前只验到"按钮能调通、异常不炸"；这一条验**完整路径**：
真实窗口 → 真实 `learn_custom_statement` → 真实 `_on_verify` → 真实引擎。

做法（offscreen，不弹窗）：
  · 真项目**轻量副本** + 一个用到自定义写法的 label（引擎会认识它，
    这样才能跑到那一句）；
  · 把弹窗类换成桩：`exec()` 里**直接调它自己的 `_do_verify()`**（等于用户点按钮）；
  · 断言：结论文本里出现"通过/失败/没证明成"之一 + `_log` 里有 `[验证]`。

    python tools/probe_verify_button_e2e.py
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
if not REAL.is_dir():
    print('真机项目不在本机，跳过')
    raise SystemExit(0)

from PySide6.QtWidgets import QApplication                            # noqa: E402

from storygraph.pipeline import statement_probe as SP                 # noqa: E402
from storygraph.pipeline.build import load_asset_registry             # noqa: E402
from storygraph.ui_qt import dialog_learn                             # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix='e2e_verify_'))
work = SP._copy_for_ask(REAL, WORK / 'proj')
for extra in SP.TEST_EXTRA_DIRS:                    # test 需要 gui/fonts
    s = REAL / 'game' / extra
    if s.is_dir():
        shutil.copytree(s, work / 'game' / extra, dirs_exist_ok=True)
# 一个"引擎**本来就认识**、能被跑到"的写法。
# ⚠ 不能用 `register_statement` 注册的自定义语句来验"通过"路径 ——
#   真机实测（引擎源码 script.py:447-452）：`init python` 属于后面的解析组，
#   解析期注册还没生效，所以 `sgcue` 那种写法**引擎编译不过**，
#   验证只能得到"没证明成"（这本身是个**诚实的结论**，不是按钮坏了）。
#   所以要验"通过"路径，得用引擎本来就认识的语句（`pause`）。
(work / 'game' / 'zz_e2e.rpy').write_text('''label sg_e2e_label:
    pause 0.01
    return
''', encoding='utf-8')
print('副本：', work)

app = QApplication.instance() or QApplication([])
from storygraph.ui_qt.main_window import MainWindowQt                 # noqa: E402


class _Engine:
    def __init__(self, root):
        self.project_root = root
        self._registry = load_asset_registry(root)

    def get_registry(self):
        return self._registry


win = MainWindowQt(_Engine(work), work / 'game' / 'zz_e2e.rpy')
logged = []
win._log = lambda m: logged.append(str(m))

# 弹窗桩：`exec()` 里直接调**真实的** `_do_verify()` —— 等价于用户点了按钮
opened = {}


class _AutoVerifyDialog(dialog_learn.CustomStatementDialog):
    def exec(self):
        opened['dlg'] = self
        self.name.setText('pause')          # 用户填的动作名
        self._do_verify()                   # ← 真的点按钮
        opened['preview'] = self.preview.toPlainText()
        return 0                            # 等于"用户随后取消了"


original = dialog_learn.CustomStatementDialog
dialog_learn.CustomStatementDialog = _AutoVerifyDialog
try:
    action = next(a for a in win._tb_graph.actions()
                  if '自定义模块' in a.text())
    print('点了工具栏的「自定义模块」…')
    action.trigger()
finally:
    dialog_learn.CustomStatementDialog = original

text = opened.get('preview') or ''
print('\n=== 按钮跑完，弹窗预览区显示：')
for ln in text.splitlines()[:12]:
    print('   ', ln[:130])
print('\n=== 控制台日志里的 [验证] 行：')
for ln in logged:
    if '[验证]' in ln:
        print('   ', ln[:150])

ok_preview = any(k in text for k in ('通过', '失败', '没证明成'))
ok_log = any('[验证]' in m for m in logged)
ok_verdict = '✅ 通过' in text
print('\n=== 判定')
print('    预览区显示了结论 →', '✅' if ok_preview else '❌')
print('    控制台有 [验证]     →', '✅' if ok_log else '❌')
print('    结论是"通过"        →', '✅' if ok_verdict else '⚠（不是通过，看上面原文）')

shutil.rmtree(WORK, ignore_errors=True)
print('\n（临时目录已删；真项目未被触碰）')
