"""
**设计稿原型**：把 v2 的新界面渲染成 PNG（给人评审，不是最终实现）
==================================================================

用户在 2026-10-10 截图评审里否掉了 v1，要求"重新设计它，先写一版新的设计方案"，
并在看过设计之后才决定要不要做反推 / 弹窗重做 / 动作清单页。

这个脚本就是**设计稿的图**：布局与文案按
`docs/archive/设计_自定义动作界面_v2_编剧版_2026-10-10.md` 的 §二 三段式，
数据用**真项目**（角色表、真实的角色 ID），只把"自定义动作"注入到副本里当素材。

    python tools/proto_learn_v2.py

⚠ 它是**设计稿**，不是产品代码：
  · 只画一遍、只为了出图；
  · 真正的弹窗重做在 V2-3（会复用这里的布局与文案，但数据来自 §三 的反推）；
  · 反推已经把真实项目跑通了，所以这里的"要填什么"**不是假数据**。
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

from PySide6.QtCore import Qt                                        # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog,      # noqa: E402
                               QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from storygraph.authoring.learn_statement import suggest_shape       # noqa: E402
from storygraph.story import statement_registry as SR                # noqa: E402
from storygraph.ui_qt import theme, tokens as T                      # noqa: E402

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')

#: 注入到副本里的"服装动作"素材（用真项目角色表里的角色 ID）
INJECT = '''label proto_outfit_demo:
    outfit lyj "泳装" mood=happy
    outfit lyj "校服" mood=calm
    outfit lyj "浴衣" mood=happy
    outfit lxj "泳装" mood=happy
    outfit lxj "校服"
    weather rain 强度=3
    weather snow 强度=1
    return
'''


def _caption(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty('role', 'caption')
    lab.setWordWrap(True)
    return lab


def _muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {T.t('text.muted')};")
    return lab


def _step(n: int, text: str) -> QLabel:
    lab = QLabel(f'{n}　{text}')
    font = lab.font()
    font.setBold(True)
    font.setPointSize(font.pointSize() + 1)
    lab.setFont(font)
    return lab


class ProtoDialog(QDialog):
    """设计稿：单栏三段（要认识哪个动作 / 起名字 / 要填什么 + 现场预览）。"""

    def __init__(self, parent=None, discovered=None, shape=None,
                 declared_names=None):
        super().__init__(parent)
        self.setWindowTitle('让工具认识一个新动作 —— 设计稿 v2')
        theme.apply_to(self)
        self._discovered = list(discovered or [])
        self._shape = dict(shape or {})

        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['lg'], T.SPACE['lg'],
                                T.SPACE['lg'], T.SPACE['lg'])
        root.setSpacing(T.SPACE['md'])
        root.addWidget(_muted(
            '剧本里有些写法工具还不认识（比如换服装/天气）。在这里教它一次，'
            '以后在卡片上就能像"显示立绘"一样，从下拉里直接选。'))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        col = QVBoxLayout(body)
        col.setSpacing(T.SPACE['md'])

        # ① 要认识哪个动作
        col.addWidget(_step(1, '要认识哪个动作？'))
        col.addWidget(_muted('只列真正不认识的写法；Ren\'Py 自带的'
                             '（with / pause / window / show …）工具本来就会，'
                             '不在这里。'))
        table = QTableWidget(len(self._discovered), 3)
        table.setHorizontalHeaderLabels(['剧本里的写法', '用过几次', '剧本里长这样'])
        table.verticalHeader().setVisible(False)
        from PySide6.QtWidgets import QHeaderView
        table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        for i, rec in enumerate(self._discovered):
            table.setItem(i, 0, QTableWidgetItem(rec['head']))
            table.setItem(i, 1, QTableWidgetItem(f"用了 {rec['count']} 次"))
            sample = rec['samples'][0][1] if rec['samples'] else ''
            table.setItem(i, 2, QTableWidgetItem(sample))
        table.setFixedHeight(38 + 30 * max(1, len(self._discovered)))
        table.selectRow(0)
        col.addWidget(table)
        col.addWidget(_muted('列表里没有？[ 手动输入动作名… ] '
                             '（也可以先把剧本导进来，工具会自动发现）'))

        # ② 起名字
        col.addWidget(_step(2, '给它起个编剧看得懂的名字'))
        row = QHBoxLayout()
        row.addWidget(QLabel('在编辑器里显示为'))
        name = QLineEdit('泳装切换')
        name.setMinimumWidth(200)
        row.addWidget(name)
        row.addWidget(QLabel('一句话说明（可空）'))
        desc = QLineEdit('换角色立绘/服装')
        row.addWidget(desc, 1)
        col.addLayout(row)

        # ③ 要填什么（每项一行，竖向）
        col.addWidget(_step(3, '这个动作要填什么？'))
        col.addWidget(_muted(
            f"工具已经从你写过的 {self._shape.get('occurrences', 0)} 次里"
            f"猜出来了 —— 名字/候选都能改。「例子」是剧本里真实出现过的写法。"))
        for item in self._shape.get('items') or ():
            box = QFrame()
            box.setProperty('role', 'card')
            gl = QGridLayout(box)
            gl.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                                  T.SPACE['sm'], T.SPACE['sm'])
            gl.addWidget(QLabel(f"第 {item['index'] + 1} 项"), 0, 0)
            gl.addWidget(QLabel('编剧看到的名字'), 0, 1)
            nm = QLineEdit(item['name'])
            nm.setMaximumWidth(120)
            gl.addWidget(nm, 0, 2)
            gl.addWidget(QLabel('例子'), 0, 3)
            ex = QComboBox()
            ex.addItems([str(v) for v in item['examples'][:5]] or ['—'])
            ex.setMinimumWidth(150)
            gl.addWidget(ex, 0, 4)
            if item['choices']:
                gl.addWidget(QLabel(f"☑ 只能填这些（{item['choices_from']}）："
                                    + '、'.join(map(str, item['choices'][:5]))),
                            1, 1, 1, 4)
            else:
                gl.addWidget(_muted('☐ 随便填（工具没看出固定范围）'), 1, 1, 1, 4)
            col.addWidget(box)
        col.addWidget(_muted('                       [＋ 再加一项]   [－ 删这项]'))

        # ④ 现场预览（真控件）
        col.addWidget(_step(4, '以后在编辑器里长这样（下面就是真控件，可以点）'))
        prev = QFrame()
        prev.setProperty('role', 'card')
        pl = QHBoxLayout(prev)
        pl.addWidget(QLabel('动作'))
        act = QComboBox()
        act.addItems([str(x) for x in (declared_names or ['泳装切换'])])
        act.setMinimumWidth(120)
        pl.addWidget(act)
        for item in self._shape.get('items') or ():
            pl.addWidget(QLabel(item['name']))
            cb = QComboBox()
            vals = [str(v) for v in (item['choices'] or item['examples'])[:6]]
            cb.addItems(vals or ['—'])
            cb.setMinimumWidth(110)
            pl.addWidget(cb)
        pl.addStretch(1)
        col.addWidget(prev)
        col.addStretch(1)

        root.addWidget(scroll, 1)
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton('算了')
        ok = QPushButton('就这样，教会它')
        ok.setProperty('variant', 'primary')
        btns.addWidget(cancel)
        btns.addWidget(ok)
        root.addLayout(btns)
        self.resize(1000, 720)


def main():
    if not REAL.is_dir():
        print('真机项目不在本机，跳过')
        return 1
    work = Path(tempfile.mkdtemp(prefix='proto_v2_')) / 'sr_general'
    shutil.copytree(REAL, work,
                    ignore=shutil.ignore_patterns('.git', 'saves', 'build',
                                                  '*.rpyc', 'log.txt',
                                                  '*.backup', 'cache'))
    demo = work / 'game' / 'proto_outfit_demo.rpy'      # 注入素材（只在副本里）
    demo.write_text(INJECT, encoding='utf-8')
    SR.clear_cache()

    discovered = SR.discover_undeclared(work)
    print('真项目副本里发现的"还不认识的动作"：',
          [(r['head'], r['count']) for r in discovered])
    if not discovered:
        print('（没发现 —— 检查注入是否生效）')
        return 1
    head = discovered[0]['head']
    shape = suggest_shape(work, head)
    print(f"=== 反推 `{head}` 的形态（真实数据，不是假数据）")
    for it in shape['items']:
        print(f"   第{it['index'] + 1}项 {it['name']!r} 例子={it['examples'][:4]} "
              f"候选={it['choices'][:5]} 来源={it['choices_from'] or '—'}")
    print('   样例：', [s['text'] for s in shape['samples'][:3]])

    app = QApplication.instance() or QApplication([])
    dlg = ProtoDialog(None, discovered=discovered, shape=shape,
                      declared_names=['泳装切换', '下雨', '淡入淡出（工具自带）'])
    dlg.show()
    app.processEvents()
    out = ROOT / '.pip-tmp' / 'shots' / 'design_v2.png'
    out.parent.mkdir(parents=True, exist_ok=True)
    ok = dlg.grab().save(str(out))
    print('设计稿', 'ok' if ok else '失败', '->', out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

# ⚠ 跑完删掉副本（2026-10-11：漏了清理，TEMP 里堆了 46.9GB 的 sr_general）
shutil.rmtree(work.parent, ignore_errors=True)
