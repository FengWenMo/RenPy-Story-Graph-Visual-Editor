"""
「AI 助手 · 填 API key」对话框（用户 2026-10-11 的要求）
=========================================================================

> 用户原话："由于工具是本地的，所以最好的做法是让别人跟其余用户自己填 api key，
>  最好打开之后能有一个地方点开，然后让用户输入 api key，
>  然后再让 ai 自行开始工作这样子"

## 这个窗口只做三件事（别的不做）

  ① 让用户**选服务方 + 粘自己的 key**（key 用密码框，**不回显明文**）；
  ② 告诉他**存在哪儿**（用户级目录，不在项目里）、**能随时改/清**；
  ③ 保存成功/失败都**说清楚**（不静默）。

⚠ 界面红线（沿用本仓契约）：
  · **Qt 不渲染 Markdown** → 文案里不写 `**`；
  · **能点就必须有反应** → 没填 key 时保存按钮的状态要跟着变；
  · 已经存过 key 时，只显示**脱敏**形式（`sk-1…cdef`）—— 截图贴出去也不怕。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from storygraph.core import ai_settings as AI
from storygraph.core.safeguard import swallow


class AiKeyDialog(QDialog):
    """填 / 改 / 清 API key。**只写用户级配置文件**（绝不进项目）。"""

    def __init__(self, parent=None, *, path=None):
        super().__init__(parent)
        self.setWindowTitle('AI 助手 · 填 API key')
        self._path = path                       # 判据可注入临时路径
        self._cfg = AI.load(path)
        root = QVBoxLayout(self)

        head = QLabel('这个 key 只存在你这台机器上（不写进项目、不上传）。'
                      '填一次就行，随时可以回来改。')
        head.setWordWrap(True)
        root.addWidget(head)

        form = QFormLayout()
        self.provider = QComboBox()
        for name, spec in AI.PROVIDERS.items():
            self.provider.addItem(spec['label'], name)
        cur = self._cfg.get('provider') or 'deepseek'
        idx = self.provider.findData(cur)
        if idx >= 0:
            self.provider.setCurrentIndex(idx)
        form.addRow('用哪家', self.provider)

        self.model = QLineEdit(self._cfg.get('model') or '')
        self.model.setPlaceholderText('留空就用默认（例：deepseek-chat）')
        form.addRow('模型', self.model)

        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)          # 不回显明文
        self.key.setPlaceholderText('把你的 key 粘在这里（只存在本机）')
        form.addRow('API key', self.key)

        self.base_url = QLineEdit(self._cfg.get('base_url') or '')
        self.base_url.setPlaceholderText('留空就用默认；自建/代理才需要填')
        form.addRow('接口地址', self.base_url)
        root.addLayout(form)

        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        row = QWidget()
        rl = QVBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.clear_btn = QPushButton('清掉已存的 key')
        self.clear_btn.clicked.connect(self._clear)
        rl.addWidget(self.clear_btn)
        root.addWidget(row)

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        self.save_btn = box.button(QDialogButtonBox.Save)
        self.save_btn.setText('存到本机')
        self.save_btn.setProperty('variant', 'primary')
        box.button(QDialogButtonBox.Close).setText('关闭')
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        root.addWidget(box)

        self.key.textChanged.connect(self._refresh)
        self.provider.currentIndexChanged.connect(self._refresh)
        self._refresh()

    # ---------------- 内部 ----------------
    def _refresh(self, *_a):
        """按钮状态 + 状态行：**能点就必须有反应**（没填就灰化并说明）。"""
        has_key = bool(self.key.text().strip())
        existing = bool(self._cfg.get('api_key'))
        self.save_btn.setEnabled(has_key)
        self.clear_btn.setEnabled(existing)
        where = str(self._path or AI.config_path())
        if existing:
            self.status.setText(f'现在已存：{AI.masked(self._cfg.get("api_key"))}'
                                f'（{where}）—— 重新填会覆盖它。')
        else:
            self.status.setText(f'还没存过。保存位置：{where}'
                                + ('' if has_key else '（先粘一个 key 再点保存）'))

    def _save(self):
        got = AI.save(self.provider.currentData(), self.key.text(),
                      model=self.model.text(), base_url=self.base_url.text(),
                      path=self._path)
        self.key.clear()                         # 存完立刻清掉明文框
        self._cfg = AI.load(self._path)
        # ⚠ 顺序有讲究（判据抓到）：先 `_refresh()` 把按钮状态/默认文案刷好，
        #   **再**写这次的结果 —— 反过来会被 `_refresh()` 覆盖掉，
        #   用户看不到"到底成没成"。
        self._refresh()
        if got.get('ok'):
            self.status.setText(f'已存到 {got["path"]} —— '
                                f'{AI.masked(self._cfg.get("api_key"))}。'
                                f'关掉这个窗口就能用了。')
        else:
            self.status.setText(f'没存成：{got.get("reason")}')

    def _clear(self):
        try:
            import json
            fp = self._path or AI.config_path()
            fp.write_text(json.dumps({}), encoding='utf-8')
            self._cfg = AI.load(self._path)
            self._refresh()
            self.status.setText('已清掉本机存的 key（配置里其他字段也一并清空了）。')
        except OSError as e:                      # noqa: BLE001 - 清不掉要说
            swallow('ui_qt.dialog_ai.clear', e)
            self._refresh()
            self.status.setText(f'没清成：{e}')


__all__ = ['AiKeyDialog']
