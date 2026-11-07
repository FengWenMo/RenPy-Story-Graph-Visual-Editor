"""
storygraph.ext.plugins
======================
**插件**：不改工具代码，往项目里丢一个文件就多出一种能力。

用户 2026-11-07："我们也要做支持插件这样的做法……必须能兼容一切自定义功能的
改进"。

插件放在 `<项目>/extensions/` 下，两种写法都认：

    extensions/我的过场.json
        {"extensions": [ {"id": "my_wipe", "kind": "call", ...} ]}
        （也可以直接是一个列表）

    extensions/我的过场.py
        EXTENSIONS = [ {...}, {...} ]          # 纯数据
        # 或者
        def extensions():                       # 要算一下才拿得到的
            return [ {...} ]

⚠ **`.py` 插件会执行项目里的代码** —— 这是插件本来的代价。所以：

  · 只在**项目目录**里找（不扫用户主目录、不装系统级插件）；
  · 单个插件坏了 → 记进 `problems`，其它照常加载（不连坐）；
  · 插件**不能**注册 `kind` 以外的东西（`parse_extension` 已经把未知键拒了）。

插件同样受两个开关约束（`enabled` / `batchable`），和项目自己的扩展点没有区别。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.ext.schema import ExtError, parse_extension

PLUGIN_SUFFIXES = ('.json', '.py')


def _from_json(path: Path):
    data = json.loads(path.read_text(encoding='utf-8') or '{}')
    raw = data.get('extensions') if isinstance(data, dict) else data
    if raw is None:
        raw = []
    if not isinstance(raw, (list, tuple)):
        raise ExtError(f'{path.name}: extensions 必须是列表')
    return list(raw)


def _load_py(path: Path):
    """把一个 `.py` 插件当模块加载（名字带路径哈希，避免撞名）。"""
    name = f'storygraph_plugin_{abs(hash(str(path.resolve()))) & 0xffffffff:x}'
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ExtError(f'{path.name}: 加载不了')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loaded = False
    try:
        spec.loader.exec_module(module)
        loaded = True
    finally:
        # 插件里的代码可能抛**任何**东西（它在别人的项目里）。这里不猜类型，
        # 只保证失败后不留一个半初始化的模块在 `sys.modules` 里。
        if not loaded:
            sys.modules.pop(name, None)
    raw = getattr(module, 'EXTENSIONS', None)
    if raw is None and callable(getattr(module, 'extensions', None)):
        raw = module.extensions()
    if raw is None:
        raise ExtError(f'{path.name}: 既没有 EXTENSIONS 也没有 extensions()')
    if not isinstance(raw, (list, tuple)):
        raise ExtError(f'{path.name}: EXTENSIONS 必须是列表')
    return list(raw)


def discover(project_root) -> tuple:
    """
    `<项目>/extensions/` → `([ExtensionPoint], problems)`。

    `source` 写成 `plugin:<文件名>`，界面上就能说清"这条是谁加的"。
    目录不存在 → `([], [])`（**静默**：没有插件是常态，不是错误）。
    """
    root = Path(project_root) if project_root else None
    if root is None:
        return [], []
    folder = root / 'extensions'
    if not folder.is_dir():
        return [], []
    points, problems = [], []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in PLUGIN_SUFFIXES:
            continue
        source = f'plugin:{path.name}'
        try:
            raw_list = (_load_py(path) if path.suffix.lower() == '.py'
                        else _from_json(path))
        except Exception as e:                  # noqa: BLE001 - 插件代码可以抛任何东西
            # 这里**必须**宽：`.py` 插件里是别人的代码，抛自定义异常都可能。
            # 一个插件炸了只记一笔（进 `problems`，界面上会显示），
            # 不能让整个工具打不开。
            problems.append(f'{path.name} 加载失败：{e}')
            continue
        for raw in raw_list:
            try:
                points.append(parse_extension(raw, source=source))
            except ExtError as e:
                problems.append(f'{path.name}: {e}')
    return points, problems


def write_example(project_root, name: str = '示例插件.json') -> Path:
    """在 `extensions/` 下写一份**注释齐全的模板**（供界面上的「新建插件」用）。"""
    root = Path(project_root)
    folder = root / 'extensions'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if path.exists():
        return path
    template = {
        'extensions': [
            {
                'id': 'my_wipe',
                'kind': 'call',
                'label': '擦除过场',
                'docs': '项目自己的包装函数；这里说清它的参数形状',
                'enabled': True,
                'fields': [
                    {'key': '方向', 'label': '方向', 'kind': 'text',
                     'help': '位置参数 0'},
                    {'key': '秒数', 'label': '秒数', 'kind': 'number',
                     'help': '位置参数 1'},
                ],
            },
        ],
    }
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8')
    return path
