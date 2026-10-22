"""
storygraph.ui_qt.prefs.py
=========================
**用户偏好**的小仓库（`QSettings`）：跨文件、跨会话都成立的那些开关与视图状态。

放这里的判据
------------
「换成另一个项目 / 另一章，这个选择还成立吗？」成立 → 这里；不成立 → 别处：

  · 底色（`asset_picker.SETTING_DARK`）、左栏折叠了哪些 label → **偏好**；
  · 图上的坐标、窗口分栏尺寸、上次打开哪一章 → **文档 / 会话**，
    走 `storygraph_layout.json` 与 `session_state`。

把两者混在一份数据里，会出现"我只是想换个项目打开，图上位置全没了"。

为什么不直接用 `asset_picker` 里那份工厂
----------------------------------------
`asset_picker.settings_factory` 是它**自己**的注入点（测试换临时 ini 文件，
避免写进真实注册表）。卡片页读一个偏好不该为了一个工厂去 import 选资产的
对话框 —— 那会把一堆资产扫描逻辑拖进卡片页。所以这里另有一份**同名的注入点**：
org / app 两处一致（Qt 按它定位注册表根），工厂各自可替换、互不牵连。

`load_json` / `save_json` 把"读不出来"一律当默认值：偏好坏了最坏是"这次
没有折叠状态"，**绝不能**因为它把窗口开不起来。
"""
from __future__ import annotations

import json

from PySide6.QtCore import QSettings

from storygraph.core.safeguard import swallow

#: 与 `asset_picker` 的 SETTINGS_ORG / SETTINGS_APP **必须一致**：
#: 同一个 Qt 应用就是同一份偏好（注册表 `HKCU\\Software\\storygraph\\editor`）。
SETTINGS_ORG = 'storygraph'
SETTINGS_APP = 'editor'


def settings_factory():
    """默认存储：系统偏好（注册表 / ini 由 Qt 决定）。

    测试把它换成 `QSettings(<临时 ini>, IniFormat)` —— 换的是**这个方法**，
    不是 `QSettings` 本身（见模块 docstring）。
    """
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def settings():
    return settings_factory()


def load_json(key: str, default=None):
    """读一个 JSON 值；没存过 / 存坏了 / 读不出来都返回 `default`。"""
    try:
        raw = settings().value(key, '')
    except Exception as e:                        # noqa: BLE001 - 偏好读不到不算错
        swallow('ui_qt.prefs.load', e)
        return default
    text = str(raw or '').strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError) as e:
        swallow('ui_qt.prefs.load_json', e)
        return default


def save_json(key: str, value) -> bool:
    """存一个 JSON 值；存不进去返回 False（调用方**不必**因此报错）。"""
    try:
        settings().setValue(key, json.dumps(value, ensure_ascii=False))
        return True
    except Exception as e:                        # noqa: BLE001 - 偏好写不进去不拦操作
        swallow('ui_qt.prefs.save_json', e)
        return False


__all__ = ['SETTINGS_ORG', 'SETTINGS_APP', 'settings', 'settings_factory',
           'load_json', 'save_json']
