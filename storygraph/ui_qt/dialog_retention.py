# -*- coding: utf-8 -*-
"""
storygraph.ui_qt.dialog_retention
=================================
**「留多久」**：脚本旧版本的留档档位（最后一轮补的那个开关）。

为什么要一个窗口，而不是让人手改 JSON（2026-11-08 第十三轮）：

  · 这个开关的代价是**磁盘**，而"5 代 + 6 个台阶"换算成"这个项目最多
    11 个文件/脚本、约 14 MB"是工具该算的账（探针
    `.pip-tmp/probe_r13_settings.py` 里那笔账就是窗口上这几个数）；
  · 三档之外**不让人填数字**：填 `keep: 999` 会把项目灌成几个 G，
    而填 `keep: 0` 等于把"退回去"这条退路关掉 —— 两个极端都不该顺手可达。

窗口只做三件事：显示**现在**的规矩、算清每档的代价、把选中的那一档存进
设置（`story.version_store.save_retention` → `core.user_prefs.update`）。

⚠ 两句实话说在窗口里（`FOOTNOTES`）：改小**不会立刻**腾地方（下一次写那个
脚本时才裁）；改大**不会**把已经删掉的找回来。不写这两句，用户会以为
"调大 = 找回昨天"，然后在真的需要那份旧版本时才发现没有。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QDialog, QGroupBox, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QRadioButton,
                               QVBoxLayout, QWidget)

from storygraph.core import user_prefs
from storygraph.core import versions as V
from storygraph.story import version_store as VS

#: 窗口底部那两句实话（判据钉住：别删，删了就是骗人）
FOOTNOTES = (
    '改小之后，已经存在的旧版本不会立刻删 —— 下一次改动那个脚本时才按新规矩裁；'
    '想立刻腾地方，用菜单里的「🧹 历史版本（占多少 / 清掉）…」。',
    '改大不会把已经删掉的找回来 —— 它只影响之后的改动。',
)
#: 没有项目时的估算提示
NO_PROJECT_HINT = '（打开一个项目，这里会算出这个项目最多占多少）'


def _gray(text: str, *, wrap: bool = True) -> QLabel:
    """灰字说明（窗口里所有"解释性"文字都走它）。"""
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    lab.setStyleSheet('color: #666;')
    return lab


def preset_detail(preset_id, scripts=None) -> str:
    """
    一档的一句话账：`只留最近几步，不跨天 · 每份脚本最多 3 个 · 这个项目约 3.8 MB`。

    `scripts` 是 `version_store.project_scripts(root)` 的结果；没有项目就
    只报"每份脚本最多几个"（估算那半句不硬编一个数出来）。
    """
    spec = VS.PRESETS[str(preset_id)]
    files = VS.max_files(spec['keep'], spec['ages'])
    text = f'{spec["note"]} · 每份脚本最多 {files} 个版本'
    total = int((scripts or {}).get('bytes') or 0)
    if total <= 0:
        return text + ' ' + NO_PROJECT_HINT
    return text + f' · 这个项目约 {V.human_size(total * files)}'


def current_line(stats) -> str:
    """这个项目**现在**占了多少（没有旧版本时如实说没有）。"""
    stats = stats or {}
    files = int(stats.get('files') or 0)
    if not files:
        return '这个项目还没有旧版本 —— 工具改脚本时才会留。'
    return (f'现在这个项目：{files} 个旧版本 · '
            f'{V.human_size(stats.get("bytes"))}'
            f'（{int(stats.get("scripts") or 0)} 个脚本，最早到 '
            f'{V.stamp_text(stats["oldest"])[:10] if stats.get("oldest") else "?"}）')


class RetentionDialog(QDialog):
    """选一档留档规矩。`saved` 在**真的写进设置之后**才发。"""

    saved = Signal(dict)

    def __init__(self, parent=None, *, rule=None, scripts=None, stats=None,
                 save=None, settings_path=None):
        super().__init__(parent)
        #: 打开这一刻的规矩（窗口只改它，不就地改全局状态）
        self._rule = dict(rule or VS.retention())
        self._scripts = dict(scripts or {})
        self._stats = dict(stats or {})
        #: 存哪儿：默认走 `version_store.save_retention`（判据里换掉它，
        #: 就不用为了"点一下确定"去改全局的 `STORYGRAPH_SETTINGS`）
        self._save = save or VS.save_retention
        self._settings_path = Path(str(settings_path or user_prefs.path()))
        self._buttons: dict = {}
        self.setWindowTitle('留多久 —— 脚本旧版本')
        self.resize(760, 300)
        self._build()

    # ------------------------------------------------------------------
    #  搭界面
    # ------------------------------------------------------------------

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)
        root.addWidget(_gray(
            '工具每次改动脚本之前，都会把当时那份存进项目的 '
            '「.storygraph/versions」目录（不在 game/ 里：Ren\'Py 不扫它，'
            '也不会进 git status）。这里定的是「留多少」。'))

        box = QGroupBox('留多少')
        col = QVBoxLayout(box)
        col.setSpacing(7)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for pid in VS.PRESETS:
            col.addLayout(self._preset_row(pid))
        if self._rule.get('custom'):
            col.addWidget(self._custom_row())
        root.addWidget(box)

        root.addWidget(_gray(current_line(self._stats), wrap=False))
        problems = list(self._rule.get('problems') or [])
        if problems:
            root.addWidget(self._problem_note(problems))
        where = _gray(f'设置文件：{self._settings_path}')
        # 路径要能选中复制（用户想去手改时得找得到它）
        where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(where)
        for note in FOOTNOTES:
            root.addWidget(_gray(note))

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_ok = QPushButton('确定')
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self._on_ok)
        self.btn_cancel = QPushButton('取消')
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_ok)
        row.addWidget(self.btn_cancel)
        root.addLayout(row)

    def _preset_row(self, pid: str):
        """一档：左边单选，右边那笔账（`preset_detail` 算出来的那句）。"""
        line = QHBoxLayout()
        spec = VS.PRESETS[pid]
        btn = QRadioButton(str(spec['label']))
        btn.setMinimumWidth(120)
        btn.setChecked(bool(self._rule.get('preset') == pid
                            and not self._rule.get('custom')))
        self._group.addButton(btn)
        self._buttons[pid] = btn
        line.addWidget(btn)
        line.addWidget(_gray(preset_detail(pid, self._scripts)), 1)
        return line

    def _custom_row(self):
        """
        「自定义」：设置文件里手写了 `keep`/`ages`（窗口不提供这条路，但得认）。

        这一档**只能看不能选**：选了就等于"把别人的手写值改成三档之一"，
        而我们并不知道他为什么那么写。想回到三档，点任意一档就行。
        """
        btn = QRadioButton('自定义（设置文件里手改的）')
        btn.setChecked(True)
        btn.setEnabled(False)
        keep, ages = self._rule.get('keep'), self._rule.get('ages')
        text = (f'现在按 keep={keep}、时间台阶 {len(tuple(ages or ()))} 档在留'
                f'（每份最多 {VS.max_files(keep, ages or ())} 个）')
        line = QHBoxLayout()
        line.addWidget(btn)
        line.addWidget(_gray(text), 1)
        holder = QWidget()
        holder.setLayout(line)
        return holder

    def _problem_note(self, problems) -> QLabel:
        """设置文件里有看不懂的东西 —— 说出来（不静默按默认算）。"""
        lab = _gray('设置文件里有 ' + str(len(problems)) + ' 处看不懂：'
                    + '；'.join(str(p) for p in problems[:3])
                    + ('…' if len(problems) > 3 else ''))
        lab.setStyleSheet('color: #a15c00;')
        return lab

    # ------------------------------------------------------------------
    #  选 / 存
    # ------------------------------------------------------------------

    def chosen(self):
        """选中的档位 id；选的是"自定义"那一档 → `None`（不写任何东西）。"""
        for pid, btn in self._buttons.items():
            if btn.isChecked():
                return pid
        return None

    def _on_ok(self):
        """
        存 → 成功才关窗。

        **失败不许静默关掉**：存不进去（文件坏了 / 没权限）时窗口留着，
        把路径与"删掉它就回到默认"一起说出来 —— 否则用户以为改好了，
        下次被裁掉东西才发现没生效。
        """
        pid = self.chosen()
        if pid is None:                     # 自定义：这一档不改任何东西
            self.accept()
            return 'custom'
        try:
            rule = self._save(pid)
        except user_prefs.CorruptSettings as e:
            self._report(f'设置文件坏了，这次没有改：\n{e.path}\n\n'
                         f'删掉这个文件就会回到默认（工具会重新建一份）。')
            return 'corrupt'
        except OSError as e:
            self._report(f'没写进去（{e}）—— 这次没有改。')
            return 'error'
        self._rule = dict(rule)
        self.saved.emit(self._rule)
        self.accept()
        return 'saved'

    def _report(self, text: str) -> None:
        QMessageBox.warning(self, '留多久', text)


__all__ = ['FOOTNOTES', 'NO_PROJECT_HINT', 'RetentionDialog', 'current_line',
           'preset_detail']
