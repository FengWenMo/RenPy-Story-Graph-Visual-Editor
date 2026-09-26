"""
tests/conftest.py
=================
pytest 全局夹具。

职责：
  · 把项目根目录加入 sys.path（源码是扁平导入，不是包结构）
  · 在无 GUI 环境下为 tkinter 提供桩，保证 commands / exporter / panel 可导入
  · 提供写 .rpy 临时文件的工厂 fixture
"""

import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ------------------------------------------------------------------
#  tkinter 桩
# ------------------------------------------------------------------

def _install_tkinter_stub():
    """无头环境下用桩替换 tkinter，避免 ImportError。"""
    if 'tkinter' in sys.modules:
        return

    calls = {'info': [], 'error': [], 'warning': [], 'yesno': []}

    def _record(kind):
        def _fn(title, message, *args, **kwargs):
            calls[kind].append((title, message))
            return None
        return _fn

    tk = types.ModuleType('tkinter')
    tk.Tk = object
    tk.Toplevel = object
    tk.Frame = object
    tk.Canvas = object
    tk.StringVar = object
    tk.END = 'end'
    tk.LEFT = 'left'

    messagebox = types.ModuleType('tkinter.messagebox')
    messagebox.showinfo = _record('info')
    messagebox.showerror = _record('error')
    messagebox.showwarning = _record('warning')
    messagebox.askyesno = _record('yesno')
    messagebox._calls = calls

    filedialog = types.ModuleType('tkinter.filedialog')
    filedialog.askopenfilename = lambda *a, **k: ''
    filedialog.asksaveasfilename = lambda *a, **k: ''
    filedialog.askdirectory = lambda *a, **k: ''

    ttk = types.ModuleType('tkinter.ttk')
    ttk.Frame = object
    ttk.Label = object
    ttk.Button = object
    ttk.Entry = object

    scrolledtext = types.ModuleType('tkinter.scrolledtext')
    scrolledtext.ScrolledText = object

    font = types.ModuleType('tkinter.font')
    font.Font = object

    simpledialog = types.ModuleType('tkinter.simpledialog')
    simpledialog.askstring = lambda *a, **k: None
    simpledialog.askinteger = lambda *a, **k: None

    tk.messagebox = messagebox
    tk.filedialog = filedialog
    tk.ttk = ttk
    tk.scrolledtext = scrolledtext
    tk.font = font
    tk.simpledialog = simpledialog

    sys.modules['tkinter'] = tk
    sys.modules['tkinter.messagebox'] = messagebox
    sys.modules['tkinter.filedialog'] = filedialog
    sys.modules['tkinter.ttk'] = ttk
    sys.modules['tkinter.scrolledtext'] = scrolledtext
    sys.modules['tkinter.font'] = font
    sys.modules['tkinter.simpledialog'] = simpledialog


_install_tkinter_stub()


# ------------------------------------------------------------------
#  夹具
# ------------------------------------------------------------------

@pytest.fixture
def write_rpy(tmp_path):
    """写 .rpy 文件，返回 Path。"""
    counter = {'n': 0}

    def _write(content: str, name: str = None):
        if name is None:
            counter['n'] += 1
            name = f'script{counter["n"]}.rpy'
        fp = tmp_path / name
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding='utf-8')
        return fp

    return _write


@pytest.fixture
def clear_messagebox():
    """清空 messagebox 桩记录，返回记录字典。"""
    mb = sys.modules['tkinter.messagebox']
    calls = mb._calls
    for lst in calls.values():
        lst.clear()
    return calls


@pytest.fixture
def asset_env(tmp_path):
    """
    构造一个最小资产项目布局：

    project/
      game/
        images/bg/*.png
        audio/*.ogg
        script.rpy
      asset_tables/

    返回 dict(project_root, asset_dir, game_dir)。
    """
    from asset_registry import write_asset_excel

    project = tmp_path / 'project'
    game = project / 'game'
    images = game / 'images' / 'bg'
    audio = game / 'audio'
    asset_dir = tmp_path / 'asset_tables'

    images.mkdir(parents=True)
    audio.mkdir(parents=True)
    asset_dir.mkdir(parents=True)

    for name in ('bg_classroom.png', 'bg_street.png'):
        (images / name).write_bytes(b'\x89PNG\r\n\x1a\n')
    (audio / 'bgm_theme.ogg').write_bytes(b'OggS')
    (audio / 'sfx_click.ogg').write_bytes(b'OggS')

    (game / 'script.rpy').write_text(
        'define e = Character("Eileen", color="#c8ffc8")\n'
        'label start:\n'
        '    scene bg classroom\n'
        '    play music bgm_theme\n'
        '    show bg street at leftish\n'
        '    e "Hello."\n'
        '    jump ending\n'
        'label ending:\n'
        '    return\n',
        encoding='utf-8',
    )

    # 预置一张图片表，避免 AssetEngine 自动全盘扫描
    write_asset_excel(
        {
            'images': [
                {'ID': 'bg_classroom', 'Path': 'game/images/bg/bg_classroom.png',
                 'Tag': 'bg', 'Attributes': 'bg_classroom',
                 'Category': 'background', 'Notes': ''},
                {'ID': 'bg_street', 'Path': 'game/images/bg/bg_street.png',
                 'Tag': 'bg', 'Attributes': 'bg_street',
                 'Category': 'background', 'Notes': ''},
            ],
            'audio': {'music': [
                {'ID': 'bgm_theme', 'Path': 'game/audio/bgm_theme.ogg',
                 'Loop': True, 'Volume': 1.0, 'Notes': ''},
            ], 'sound': [
                {'ID': 'sfx_click', 'Path': 'game/audio/sfx_click.ogg',
                 'Notes': ''},
            ]},
        },
        asset_dir,
    )

    return {
        'project_root': project,
        'asset_dir': asset_dir,
        'game_dir': game,
    }
