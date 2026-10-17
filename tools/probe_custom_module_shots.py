"""注册一条自定义模块 → **卡片编辑器** before/after 截图对照。

    python tools/probe_custom_module_shots.py [输出目录]

做三件事：
  ① 合成一个 Ren'Py 工程（`tests/_ui_project.build_project`）并塞进作者的
     自定义写法（`swim_suit … intensity=3`）；
  ② 打开主窗口 → 切到**卡片编辑**页 → 截图（before）；
  ③ 走「自定义模块」弹窗把 `swim_suit` **登记**成声明（`declare_statement`
     是"教一次"的唯一写入口）→ 重开窗口 → 再截图（after）。
     同时打印**客观差异**（声明数 / 参数列 / 卡片上的控件与文字），
     免得"看着像变了"变成自说自话。
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from PySide6.QtWidgets import (QApplication, QLabel, QLineEdit,  # noqa: E402
                               QPushButton, QWidget)

from _ui_project import build_project                          # noqa: E402
from storygraph.story import statement_registry as SR            # noqa: E402
from storygraph.ui_qt.dialog_learn import CustomStatementDialog  # noqa: E402
from storygraph.ui_qt.main_window import MainWindowQt            # noqa: E402

CUSTOM = '''
label extra_swim:
    swim_suit lyj "泳装" intensity=3
    lyj "换好了"
    swim_suit lyj "再来一件" intensity=1
    return
'''

out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / '.pip-tmp' / 'shots'
out_dir.mkdir(parents=True, exist_ok=True)

proj = build_project(Path(tempfile.mkdtemp(prefix='modshot_')) / 'proj')
ch = proj / 'game' / 'script' / 'ch01.rpy'
ch.write_text(ch.read_text(encoding='utf-8') + CUSTOM, encoding='utf-8')
print('工程：', proj)


class _Engine:
    """与 `tests/conftest.ui_window` 同一个桩：真的加载资源表 + 脚本里的定义。"""

    def __init__(self, root):
        self.project_root = root
        self._registry = None
        try:
            from storygraph.pipeline.build import load_asset_registry
            self._registry = load_asset_registry(self.project_root)
        except Exception:                                    # noqa: BLE001
            self._registry = None
        if self._registry is None:
            return
        try:
            from storygraph.parsing.discover import discover_scripts
            from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
            from storygraph.story.beat_build import apply_script_definitions
            analyzer = IncrementalRenPyAnalyzer()
            pr, _g = analyzer.analyze(files=discover_scripts(self.project_root),
                                      graph=None)
            apply_script_definitions(self._registry, pr)
        except Exception:                                    # noqa: BLE001
            pass

    def get_registry(self):
        return self._registry


app = QApplication.instance() or QApplication([])


def fingerprint(win):
    """客观指纹：可见控件数 + 卡片页上的**按钮/输入框/文字**（用来判"真的变了吗"）。"""
    vis = [w for w in win.findChildren(QWidget) if w.isVisible()]
    return {
        'widgets': len(vis),
        'buttons': sorted({b.text() for b in win.findChildren(QPushButton)
                           if b.isVisible() and b.text()}),
        'edits': [e.text() for e in win.findChildren(QLineEdit) if e.isVisible()],
        'labels': sorted({ln.text() for ln in win.findChildren(QLabel)
                          if ln.isVisible() and ln.text()}),
    }


def custom_index(win, name='swim_suit'):
    """
    找**承载那条自定义写法的那一拍**的下标。

    ⚠ 不看列表文本、也不只看 `spec_name`：登记**前**它不是语句，
    只是个透传拍（`raw` 里那行原文），列表文本显示成「：（无台词）」。
    所以按**拍数据**找（spec_name / raw / code / stage 任一含那个名字），
    两个阶段都找得到同一拍。
    """
    sess = getattr(win, '_session', None)
    for i, b in enumerate(getattr(sess, 'beats', None) or ()):
        blob = ' '.join(str(getattr(b, f, '') or '')
                        for f in ('spec_name', 'raw', 'code', 'stage', 'text'))
        if name in blob:
            return i
    return None


def card_of(win, name='swim_suit', label='extra_swim'):
    """
    把卡片页翻到**那一拍**，返回它的下标。

    ⚠ 锚点用 **label 名**（`extra_swim`），不是语句名：登记**前**那条写法
    还不是语句（`spec_name` 为空、行文本里也不一定有它），但**它所在的 label
    两个阶段都在** —— 这样 before/after 截的才是**同一张卡**，
    "变了没有"才不是两张不同的卡在比。
    """
    idx = None
    for anchor in (label, name):
        try:
            for i in range(win._list.count()):
                item = win._list.item(i)
                if item is not None and anchor in str(item.text()):
                    idx = i
                    break
        except Exception as e:                               # noqa: BLE001
            print('   （扫拍列表失败：%s）' % e)
        if idx is not None:
            break
    if idx is None:
        idx = custom_index(win, name)
    if idx is None:
        return None
    # ⚠ **拍下标 ≠ 列表行下标**：`beats` 的第 0 拍是"文件级块"，**没有列表行**
    #   （实测：`beats` 15 条 ↔ 列表 15 项里第 14 项才是拍 14）。不校正的话
    #   截出来的卡会整体错一位（第一次就踩了：显示成了后一拍）。
    try:
        sess = getattr(win, '_session', None)
        offset = max(0, len(getattr(sess, 'beats', None) or ())
                     - win._list.count())
        idx = max(0, idx - offset)
    except Exception as e:                                   # noqa: BLE001
        print('   （行下标校正失败：%s）' % e)
    try:
        win._list.setCurrentRow(idx)
    except Exception as e:                                   # noqa: BLE001
        print('   （翻卡片失败：%s）' % e)
    app.processEvents()
    return idx


def shot_and_measure(tag):
    SR.clear_cache()
    win = MainWindowQt(_Engine(proj), proj / 'game' / 'script' / 'ch01.rpy')
    win.resize(1500, 900)
    win.show()
    app.processEvents()
    win._set_page(win.PAGE_CARD)            # ① 卡片编辑页
    app.processEvents()
    idx = card_of(win)
    # ⚠ 关键自查：**当前卡片到底是哪一拍**（拍下标 ≠ 列表行下标）——
    #    "登记后多了参数格"必须建立在"这张卡真的是那条自定义拍"之上，
    #    否则就是拿两张不同的卡在比（第一次就踩了）。
    try:
        row = win._list.currentRow()
        sess = getattr(win, '_session', None)
        beats = getattr(sess, 'beats', None) or []
        offset = max(0, len(beats) - win._list.count())
        b = beats[row + offset] if 0 <= row + offset < len(beats) else None
        print('       当前卡：列表行 %s → 拍下标 %s；seq=%s kind=%s '
              'spec_name=%r text=%r'
              % (row, row + offset, getattr(b, 'seq', None),
                 getattr(b, 'kind', ''), getattr(b, 'spec_name', ''),
                 str(getattr(b, 'text', ''))[:14]))
    except Exception as e:                                   # noqa: BLE001
        print('       （当前卡自查失败：%s）' % e)
        try:
            print('       拍列表共 %d 项，末尾几项：%s'
                  % (win._list.count(),
                     [str(win._list.item(i).text())[:26]
                      for i in range(max(0, win._list.count() - 5),
                                     win._list.count())]))
        except Exception as e:                               # noqa: BLE001
            print('       （拍列表 dump 失败：%s）' % e)
    png = out_dir / f'card_{tag}.png'
    ok = win.grab().save(str(png))
    fp = fingerprint(win)
    fp['card_index'] = idx
    print('  [%s] 截图 %s -> %s（翻到第 %s 拍）'
          % (tag, 'ok' if ok else '失败', png, idx))
    print('       可见控件 %d；按钮 %d 个；输入框 %d 个；文字 %d 条'
          % (fp['widgets'], len(fp['buttons']), len(fp['edits']),
             len(fp['labels'])))
    if fp['edits']:
        print('       输入框内容：%s' % fp['edits'][:8])
    win.close()
    return fp, png


print()
print('① 登记**之前**：')
before, png_before = shot_and_measure('before')

# —— ② 走「自定义模块」弹窗，把 swim_suit 登记成声明 ——
disc = SR.discover_undeclared(proj)
print()
print('② 「自定义模块」弹窗发现未声明的写法：', [r['head'] for r in disc])
dlg = CustomStatementDialog(None, project_root=proj, discovered=disc,
                            registry_report=SR.format_registry_report(None))
row = next((i for i, r in enumerate(disc) if r['head'] == 'swim_suit'), 0)
dlg.found.setCurrentCell(row, 0)           # 点中 → 名字自动填好
dlg.label.setText('泳装切换')
dlg.params.add_param('intensity', '强度', '1,2,3')
dlg_dump = out_dir / 'dialog_custom_module.png'
dlg.grab().save(str(dlg_dump))
built = dlg.result_declaration()
print('   弹窗给出：name=%r label=%r columns=%s'
      % (built.get('name'), built.get('label'),
         [c.get('name') for c in (built.get('columns') or [])]))
res = SR.declare_statement(proj, built['name'], label=built.get('label', ''),
                           columns=built.get('columns'), docs=built.get('docs'))
print('   落盘：', res['action'], res['path'])
print('   弹窗截图 ->', dlg_dump)

print()
print('③ 登记**之后**：')
after, png_after = shot_and_measure('after')

print()
print('=== 客观差异 ===')
print('  声明文件：', (proj / 'storygraph.rules.json').exists())
SR.clear_cache()
from storygraph.parsing.discover import discover_scripts      # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
_pr, _ = IncrementalRenPyAnalyzer().analyze(files=discover_scripts(proj),
                                            graph=None)
reg = SR.registry_for_parse_result(_pr)
print('  已声明语句：', reg.names())
print('  swim_suit 的参数列：',
      [c['name'] for c in SR.arg_columns(reg, 'swim_suit')])
print('  intensity 候选：',
      SR.spec_arg_choices(reg, 'swim_suit', 'intensity'))
added = [b for b in after['buttons'] if b not in before['buttons']]
gone = [b for b in before['buttons'] if b not in after['buttons']]
print('  卡片页按钮 新增 %d 个 / 消失 %d 个：%s'
      % (len(added), len(gone), (added or gone)[:6]))
new_labels = [t for t in after['labels'] if t not in before['labels']]
print('  卡片页新增文字 %d 条：%s' % (len(new_labels), new_labels[:12]))
print('  可见控件 %d -> %d' % (before['widgets'], after['widgets']))
print()
print('截图：', png_before)
print('      ', png_after)
