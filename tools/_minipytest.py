"""迷你 pytest：本机没装 pytest 时逐条跑用例（只支持 tmp_path fixture）。

用法：
    python tools/_minipytest.py tests/test_x.py::test_y
    python tools/_minipytest.py tests/test_x.py::TestC.test_y
    python tools/_minipytest.py tests/test_x.py          # 跑全文件

为什么要有它：这台机器 `pip install pytest` 装不上（无网），
但 11 条回归必须逐条验证。它**不替代**真 pytest —— 只支持
`tmp_path` 这一个 fixture，遇到别的 fixture 直接报错，绝不假装通过。
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
import tempfile
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fake_pytest():
    """一个够用的假 pytest：`mark.skipif` 直接返回原函数（不跳过）。"""
    mod = types.ModuleType('pytest')

    class _Mark:
        def __getattr__(self, _name):
            def deco(*_a, **_k):
                if len(_a) == 1 and callable(_a[0]) and not _k:
                    return _a[0]

                def wrap(fn):
                    return fn
                return wrap
            return deco

    class _Raises:
        def __init__(self, exc, **_k):
            self.exc = exc

        def __enter__(self):
            return self

        def __exit__(self, et, ev, tb):
            if et is None:
                raise AssertionError(f'期望抛出 {self.exc}，但没有')
            return issubclass(et, self.exc)

    mod.mark = _Mark()
    mod.raises = _Raises
    mod.fixture = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
    mod.skip = lambda *a, **k: None
    return mod


def _fake_openpyxl():
    """造一个能 import 的假 openpyxl（**只为了让模块级 import 不炸**）。

    ⚠ 它不实现任何 excel 行为：跑到真正的读写会 AttributeError。
    只用于"这条用例根本不碰 excel，但被测模块在顶层 import 了它"的场景
    （如 `test_show_clause_words` 借 `excel_export` 做对照）。
    """
    def pkg(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m

    class _Any:
        def __init__(self, *a, **k):
            pass

        def __call__(self, *a, **k):
            return self

        def __getattr__(self, _n):
            return self

    class _AnyMeta(type):
        def __getattr__(cls, _n):
            return _Any

    class _Obj(metaclass=_AnyMeta):
        pass

    root = pkg('openpyxl', Workbook=_Any())
    drawing = pkg('openpyxl.drawing')
    pkg('openpyxl.drawing.image', Image=_Any())
    pkg('openpyxl.styles', Alignment=_Any(), Font=_Any(), PatternFill=_Any())
    worksheet = pkg('openpyxl.worksheet')
    pkg('openpyxl.worksheet.datavalidation', DataValidation=_Any())
    pkg('openpyxl.utils', get_column_letter=lambda i: 'A')
    pkg('openpyxl.comments', Comment=_Any())
    root.drawing, root.styles = drawing, worksheet


def _load(path: Path):
    sys.modules.setdefault('pytest', _fake_pytest())
    spec = importlib.util.spec_from_file_location(f'_mp_{path.stem}', path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(mod)
    return mod


_CACHE: dict = {}
_CONFTEST: dict = {}


def _load_conftest(folder: Path):
    """把同目录 `conftest.py` 里的 fixture 函数收进来（只按名字调，不解析依赖图）。"""
    global _CACHE
    cf = folder / 'conftest.py'
    if not cf.is_file() or _CONFTEST:
        return
    mod = _load(cf)
    for name, obj in vars(mod).items():
        if callable(obj) and not name.startswith('_'):
            _CONFTEST[name] = obj
    _CACHE = {}


def _resolve(name: str):
    if name in _CACHE:
        return _CACHE[name]
    if name == 'tmp_path':
        val = Path(tempfile.mkdtemp(prefix='mp_'))
        _CACHE[name] = val
        return val
    fn = _CONFTEST.get(name)
    if fn is None:
        raise RuntimeError(f'不支持的 fixture：{name}')
    kwargs = {}
    for p in inspect.signature(fn).parameters.values():
        if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY):
            kwargs[p.name] = _resolve(p.name)
    val = fn(**kwargs)
    _CACHE[name] = val
    return val


def _call(fn):
    params = [p for p in inspect.signature(fn).parameters.values()
              if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
    return fn(**{p.name: _resolve(p.name) for p in params})


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    if '--fake-excel' in argv:
        argv = [a for a in argv if a != '--fake-excel']
        try:
            import openpyxl                  # noqa: F401
        except ImportError:
            _fake_openpyxl()
            print('[minipytest] 已注入假 openpyxl（只够 import，不能真读写）')
    target = argv[0]
    path_s, _, sel = target.partition('::')
    path = (ROOT / path_s).resolve()
    _load_conftest(path.parent)
    mod = _load(path)

    cases = []
    if not sel:
        for name, obj in list(vars(mod).items()):
            if name.startswith('test_') and callable(obj):
                cases.append((name, None, obj))
            elif name.startswith('Test') and inspect.isclass(obj):
                inst = obj()
                for mn in vars(obj):
                    if mn.startswith('test_'):
                        # ⚠ 取**绑定方法**（`getattr(inst, mn)`），
                        # 直接用 `vars(obj)[mn]` 会把 `self` 当 fixture。
                        cases.append((f'{name}.{mn}', inst, getattr(inst, mn)))
    else:
        cls_name, _, fn_name = sel.rpartition('.')
        if cls_name:
            inst = getattr(mod, cls_name)()
            cases.append((sel, inst, getattr(inst, fn_name)))
        else:
            cases.append((sel, None, getattr(mod, sel)))

    failed = 0
    for case in cases:
        name = case[0]
        try:
            if len(case) == 3:
                _call(case[2])
            else:
                _call(case[1])
            print(f'ok   {name}')
        except Exception:                       # noqa: BLE001
            failed += 1
            print(f'FAIL {name}')
            traceback.print_exc()
    print(f'\n{len(cases) - failed}/{len(cases)} 通过')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
