"""
tools/check_panel_widgets.py — 用**真控件**验卡片字段写得进去。
================================================================

为什么需要这个工具（而不是一条普通测试）
----------------------------------------
2026-09-28 的事故——卡片上「说话人 / 表情 / 位置」三个格子全空，
只有「台词」有值。根因是 tkinter 版的写值判据写成 `hasattr(w, 'set')`，
而 **`tk.Entry` 没有 `set()` 方法**（读写只能通过 textvariable），
于是单行字段**静默**写不进去。

**为什么测试没拦住**：无头环境里 tkinter 是桩，测试用的是"记录型控件桩"，
而桩给假 Entry 加了 `set()` —— **桩比真控件更聪明，把 bug 藏起来了**。

2026-10-01（tkinter 全部删除后）
--------------------------------
好消息是 Qt 控件**没有这个坑**（`QLineEdit` / `QComboBox` /
`QPlainTextEdit` 各有各的 setter，不存在"看着能写其实不能"）。
但"值到底进没进控件"这件事**仍然需要拿真控件验**：

    python tools/check_panel_widgets.py

现在它跑 **offscreen Qt**（不需要显示器、可以在 CI 里跑），
逐字段打印 `OK` / `!!`，并把结论落到退出码上（有 `!!` 就返回 1）。

用法：
    python tools/check_panel_widgets.py [某个 .rpy]
    STORYGRAPH_REAL_PROJECT=<项目根> 也可以只给一个路径
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# offscreen：不弹窗、不需要显示器。字体目录不定的话中文会渲染成方框。
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

from PySide6.QtWidgets import QApplication                       # noqa: E402

from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS  # noqa: E402
from storygraph.ui_qt.main_window import BeatCardEditor          # noqa: E402


def _default_script() -> Path:
    """默认样本：真实项目的 chapter1_3（可用环境变量或命令行覆盖）。"""
    real = Path(os.environ.get(
        'STORYGRAPH_REAL_PROJECT', r'D:\千连万理\RenPyTest\sr_general'))
    return real / 'game' / 'chapter' / 'chapter1_3.rpy'


class _Engine:
    """最小 engine 桩：本工具只验"值进没进控件"，不需要真资源表。"""

    project_root = None

    def get_registry(self):
        return None


def dump(panel: BeatCardEditor, title: str) -> list:
    """打印卡片上每个字段的写入结果，返回"空值"字段名列表。"""
    print(f'=== {title} ===')
    blanks = []
    for name in list(PRIMARY_FIELDS) + list(ADVANCED_FIELDS) + ['condition']:
        if name not in panel._widgets:
            continue
        widget = panel._widgets[name]
        value = panel._get_value(name)
        want = panel._loaded_fields.get(name, '')
        # `!!` 的判据是"该有值却没有" —— 语料里 legitimately 为空的字段
        # （比如没有音乐）不该报错，否则这个工具天天在喊狼来了。
        flag = '  '
        if want and not value:
            flag = '!! '
            blanks.append(name)
        print(f'  {flag}{name:11s} [{type(widget).__name__:14s}] '
              f'载入={want!r} 读回={value!r}')
    print()
    return blanks


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    script = Path(argv[0]) if argv else _default_script()
    if not script.exists():
        print(f'[跳过] 找不到样本脚本：{script}')
        print('       给一个 .rpy 路径，或设 STORYGRAPH_REAL_PROJECT。')
        return 0

    app = QApplication.instance() or QApplication([])
    panel = BeatCardEditor()

    from storygraph.app.editor import EditorSession
    session = EditorSession(registry_provider=lambda: None,
                            project_root=script.parents[2]
                            if len(script.parents) > 2 else None)
    n = session.load(script)
    print(f'样本：{script}（{n} 拍）\n')

    all_blanks = []
    # 抽查：第一拍、中间一拍、最后一拍（真实数据里字段分布差异很大）
    for idx, title in ((0, '第 1 拍'), (n // 2, f'第 {n // 2 + 1} 拍'),
                       (n - 1, f'最后一拍（第 {n} 拍）')):
        panel.load_fields(session.fields(idx), session.cards(idx))
        all_blanks += dump(panel, f'{title}：真控件里的值')

    print('—' * 60)
    if all_blanks:
        print(f'!! 有 {len(all_blanks)} 个字段该有值却读回空：'
              f'{sorted(set(all_blanks))}')
        print('   这就是"卡片上某些格子是空的"那类事故的形态。')
        return 1
    print('OK：所有该有值的字段都真的写进了控件。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
