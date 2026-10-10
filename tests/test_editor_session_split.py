"""
P2-6 拆分守卫：mixin / `EditorSession` 的**职责边界**
===================================================

`EditorSession` 原本是 1900 行的上帝对象（74 个方法），混装了四块职责。
2026-10-05 已拆走两块，都做成 mixin（**搬家不是改契约**，所以前端一行没改）：

  · `session_graph.GraphSessionMixin` —— 布局 / 章节导入 / 结构分析 / 剧情变量；
  · `session_assets.AssetRegistryMixin` —— 资源表读取 / 候选 / 概览 / 缩略图路径。

这个文件守四件事（都是"拆完之后**漂回去/拆坏**"的形态）：

  1. **每一块只住在自己的 mixin 里**。有人图省事在 `EditorSession` 里又加一个
     同名方法 → Python 会**静默**用子类那份覆盖 mixin，行为变了而
     `test_editor_session_behavior` 未必覆盖得到那条路径。
  2. **每个 mixin 的 `REQUIRES` 在合成后的类上都存在**。`AssetRegistryMixin`
     要宿主提供 `registry()` / `project_root` / `_transforms_decision` 等 ——
     不写全的话，拆错了只会在**运行时**炸。
  3. **模块作用域名字必须齐全**。漏带一个常量只会让**用到它的那条路径**
     在运行时炸 —— 实测漏了 `_BODY_LINE_RE`，而全量单测要跑 90 秒、
     一次只暴露一处。用 `symtable` 静态查一次。
  4. **不许留下孤立的装饰器**。这条是实测踩出来的：按 `ast` 的 `node.lineno`
     从 `def` 起搬，`@staticmethod`（在它**上面**）会留在原处 ——
     结果**后面那个方法被静默变成静态方法**（`s.load(a)` 报
     "missing 1 required positional argument"）。改的是签名，不是内容，
     所以快照和行为测试都未必抓得住。

为什么单开一个文件而不是塞进 `test_package_layering`：那个文件管的是**包与包**
之间的依赖方向，这里是**类与类**之间的职责边界，判据完全不同。
"""

from __future__ import annotations

import ast
import builtins
import symtable
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / 'storygraph'
GRAPH_MOD = PKG / 'app' / 'session_graph.py'
ASSET_MOD = PKG / 'app' / 'session_assets.py'
EXPORT_MOD = PKG / 'app' / 'session_export.py'
COLLAB_MOD = PKG / 'app' / 'session_collab.py'
TABLE_MOD = PKG / 'app' / 'session_table.py'
EDITOR_MOD = PKG / 'app' / 'editor.py'

#: 每块职责：`(mixin 模块, mixin 类名, 它独占的方法名)`。
#: **按名字列表，不按行号** —— 行号会随任何一次编辑漂移，名字才是契约。
#: 新拆一块时**只加这一处**，下面 8 条通用守卫自动覆盖它。
BLOCKS = {
    'session_table': (
        'storygraph.app.session_table', 'TableSessionMixin', (
            'load', 'fields', 'cards', '_effective_positions', 'list_entry',
            'inherited_hint', 'stage_digest_text', 'speaking_card_index',
            'apply', '_apply_stage', 'set_speaker', 'remove_sprite',
            'snapshot', 'restore', 'save',
            'can_undo', 'undo', 'has_undo', 'mark_loaded', 'loaded_index',
            'insert_beat', 'delete_beat', 'restore_last_removed',
            'has_removed',
        ),
    ),
    'session_graph': (
        'storygraph.app.session_graph', 'GraphSessionMixin', (
            'save_layout', 'load_layout', 'export_control_flow',
            'missing_chapter_files',
            'node_variables', 'set_node_variables', 'check_variable_syntax',
            'import_chapters', 'import_plain_text', 'character_rows',
            'apply_character_decisions', '_reload_character_rows',
            'graph_files', 'drop_chapters',
            'analyze_graph_problems', '_split_analysis_warnings',
            'analyze_graph',
        ),
    ),
    'session_assets': (
        'storygraph.app.session_assets', 'AssetRegistryMixin', (
            'load_assets', 'characters', 'participants', 'assets_registry',
            'asset_choices', '_image_spec_of', '_describe_asset',
            'asset_linker', 'asset_overview', 'path_of_image',
            'register_missing_assets',
        ),
    ),
    'session_export': (
        'storygraph.app.session_export', 'ExportMixin', (
            'export_ir', 'frames', 'transform_index', 'screen_size',
            'should_write_transforms', '_issue_message',
            'default_output_path', 'export', '_patch_note', 'health',
        ),
    ),
    'session_collab': (
        'storygraph.app.session_collab', 'CollabMixin', (
            'collab_status', 'collab_pull', 'collab_push', 'collab_init',
        ),
    ),
}

#: **刻意留在 `EditorSession` 自己的**成员 —— 被五块共用的基建。
#: 任何一块把它搬进去，都会让另外四块反向依赖那一块
#: （`_renumber` 就被表格块与剧情图块同时用）。
#: 这个清单是"拆分收口"的判据：它的内容**不许**再变多。
CORE_MEMBERS = ('__init__', 'registry', 'source', 'refresh_symbols', 'has',
                '_renumber', '__len__', 'forget_undo', 'compile')

#: 搬成模块级函数、在 `editor.py` 里留了 re-export 的名字。
GRAPH_FUNCS = ('files_look_like_control_flow', 'graph_from_layout_dict')

#: 允许多个 mixin 同名的成员（"冲突检查"里跳过）。
#: `REQUIRES` 是**每个 mixin 都该有的契约声明**，同名正是设计。
_SHARED_MARKERS = frozenset({'REQUIRES'})


def _import(dotted: str, name: str):
    import importlib
    return getattr(importlib.import_module(dotted), name)


def _reachable(obj, name: str) -> bool:
    """
    `obj` 上有这个成员吗 —— **方法或 property 都算**。

    为什么不能只判 `callable`（第一版就是这么错的）：`loaded_index` 是
    `@property`，`getattr(类, 'loaded_index')` 拿到的是 property 对象，
    `callable` 为 False，于是它被误判成"没搬过去"。
    判据是"在不在 + 能不能拿"，不是"能不能调用"。
    """
    import inspect
    try:
        static = inspect.getattr_static(obj, name)
    except AttributeError:
        return False
    return isinstance(static, property) or callable(static)


class TestOwnership:
    """每块职责**只**住在自己的 mixin 里。"""

    @pytest.mark.parametrize('key', sorted(BLOCKS))
    def test_mixin_owns_its_methods(self, key):
        mod, cls_name, methods = BLOCKS[key]
        cls = _import(mod, cls_name)
        missing = [m for m in methods if not _reachable(cls, m)]
        assert not missing, f'{cls_name} 少了这些方法：{missing}'

    @pytest.mark.parametrize('key', sorted(BLOCKS))
    def test_session_does_not_shadow_them(self, key):
        """
        `EditorSession` 的 `__dict__` 里**不许**有这些同名方法。

        这条才是真正防"漂回去"的：同名方法会让子类那份**静默**赢，
        而 mixin 里那份变成死代码。
        """
        from storygraph.app.editor import EditorSession
        _mod, cls_name, methods = BLOCKS[key]
        shadowed = sorted(set(vars(EditorSession)) & set(methods))
        assert not shadowed, (
            f'这些方法又被写回 EditorSession 了（会静默覆盖 '
            f'{cls_name} 的那份）：{shadowed}')

    @pytest.mark.parametrize('key', sorted(BLOCKS))
    def test_requires_are_satisfied_by_the_composed_class(self, key):
        """
        每个 mixin 声明的 `REQUIRES` 必须在合成后的对象上真的存在。

        `AssetRegistryMixin` 要宿主给 `registry()` / `refresh_symbols()` /
        `source()` / `project_root` / `_loaded_registry` … ——
        这些都是**实测**（`self.*` 静态扫）出来的，不是凭印象写的。
        少写一个，拆错时只会在运行时炸。

        **在实例上查**（不是类上）：`project_root` / `_loaded_registry` /
        `_linker` 是 `EditorSession.__init__` 里的**实例属性**，
        `hasattr(类, ...)` 对它们恒为 False —— 第一版就在这儿误报过。
        """
        from storygraph.app.editor import EditorSession
        mod, cls_name, _methods = BLOCKS[key]
        mixin = _import(mod, cls_name)
        requires = list(getattr(mixin, 'REQUIRES', ()) or ())
        assert requires, f'{cls_name} 没有声明 REQUIRES（那就没法守了）'
        session = EditorSession()
        missing = [r for r in requires if not hasattr(session, r)]
        assert not missing, (
            f'{cls_name}.REQUIRES 里这些成员在 EditorSession 实例上取不到：'
            f'{missing}')

    def test_mixins_are_in_mro(self):
        from storygraph.app.editor import EditorSession
        mro = [c.__name__ for c in EditorSession.__mro__]
        for key in BLOCKS:
            _mod, cls_name, _m = BLOCKS[key]
            assert cls_name in mro, f'{cls_name} 不在 MRO 里：{mro}'

    def test_mixins_do_not_collide_with_each_other(self):
        """
        两个 mixin **不许**有同名成员。

        有的话谁赢取决于 MRO 顺序 —— 那是最难查的一类 bug
        （行为随基类书写顺序变化，而顺序看起来只是风格）。

        例外只有 `REQUIRES`：那是**每个 mixin 都该有的契约声明**，
        同名正是设计（值各不相同，各说各需要什么）。
        """
        seen: dict = {}
        dup = []
        for key in sorted(BLOCKS):
            mod, cls_name, _m = BLOCKS[key]
            cls = _import(mod, cls_name)
            for name in vars(cls):
                if name.startswith('__') or name in _SHARED_MARKERS:
                    continue
                if name in seen:
                    dup.append((name, seen[name], cls_name))
                seen[name] = cls_name
        assert not dup, f'两个 mixin 成员重名（谁赢取决于 MRO）：{dup}'

    def test_session_still_reaches_every_method(self):
        """搬家之后**调用方**必须一行都不用改。"""
        from storygraph.app.editor import EditorSession
        s = EditorSession()
        missing = []
        for key in BLOCKS:
            _mod, _cls, methods = BLOCKS[key]
            missing += [m for m in methods if not _reachable(s, m)]
        assert not missing, f'EditorSession 上取不到：{missing}'

    def test_module_functions_are_reexported_from_editor(self):
        """
        `from storygraph.app.editor import graph_from_layout_dict` 继续可用。

        实测 `ui_qt/main_window.py:2102` 就是这个写法（图级撤销/重做要用）——
        搬家时没留 re-export 的话，那条路会在**运行时**才炸，
        而 `ui_qt` 的调用点没有行为测试兜着。
        """
        from storygraph.app.editor import (files_look_like_control_flow,
                                           graph_from_layout_dict)
        from storygraph.app import session_graph as G
        assert graph_from_layout_dict is G.graph_from_layout_dict
        assert files_look_like_control_flow is G.files_look_like_control_flow

    def test_split_actually_shrank_the_session(self):
        """
        `EditorSession` 自己的方法数应当**只剩基建**（9 个）。

        这条防的是"拆了个寂寞"：方法复制到 mixin、原处没删，
        两边都留着 —— 上面那几条能过（子类那份赢），但职责没搬走。
        """
        from storygraph.app.editor import EditorSession
        own = sum(1 for k, v in vars(EditorSession).items() if callable(v))
        assert own <= 12, (
            f'EditorSession 自己还有 {own} 个方法，看起来没真搬走')

    def test_core_members_are_exactly_the_shared_infrastructure(self):
        """
        核心**恰好**留着那 9 个基建成员 —— 不多不少。

        "不多"：多了说明有新东西没搬去该去的地方；
        "不少"：少了说明有一块把基建搬进了自己家（那会让别的块反向依赖它）。

        `CORE_MEMBERS` 是 P2-6 收口的判据：**它的内容不许再变多**。
        """
        from storygraph.app.editor import EditorSession
        own = {k for k, v in vars(EditorSession).items() if callable(v)}
        extra = sorted(own - set(CORE_MEMBERS) - {'__init__'})
        missing = sorted(set(CORE_MEMBERS) - own)
        assert not missing, f'核心该有却没了：{missing}'
        assert not extra, (
            f'核心多了这些方法（该搬去对应的 mixin）：{extra}')


class TestNoOrphanDecorators:
    """
    类体里**不许有"后面不跟 def"的装饰器行**。

    为什么单独立一条（这是实测踩出来的真实 bug）：机械搬迁按 `ast` 的
    `node.lineno` 从 `def` 起搬，而 `@staticmethod` 在它**上面**一行，
    于是装饰器被留在原处，**后面那个方法被静默变成了静态方法**：

        s.load(a)   →   TypeError: load() missing 1 required positional argument

    注意它改的是**签名**而不是内容 —— 快照记不到、行为测试也未必碰上，
    所以很容易一路带进主干。判据很便宜：扫一遍源码，装饰器后面
    （跳过空行）必须紧跟 `def` / `class` / 另一个装饰器。
    """

    #: 允许"装饰器后面不跟 def"的例外（`(文件, 行内容)`）。目前空。
    ALLOWED = frozenset()

    @pytest.mark.parametrize('path', [EDITOR_MOD, GRAPH_MOD, ASSET_MOD,
                                      EXPORT_MOD, COLLAB_MOD, TABLE_MOD])
    def test_no_orphan_decorators(self, path):
        lines = path.read_text(encoding='utf-8').splitlines()
        bad = []
        for i, ln in enumerate(lines):
            s = ln.strip()
            if not s.startswith('@') or s.startswith('@dataclass'):
                continue                       # `@dataclass` 在模块级，合法
            if i > 0 and lines[i - 1].strip().startswith('@'):
                continue                       # 连续装饰器，前者合法
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            nxt = lines[j].lstrip() if j < len(lines) else ''
            if not (nxt.startswith('def ') or nxt.startswith('class ')
                    or nxt.startswith('@')):
                bad.append((i + 1, s, nxt[:40]))
        assert not bad, (
            f'{path.name} 里有孤立装饰器（后面的东西被它改了语义）：\n'
            + '\n'.join(f'  第 {n} 行 {dec!r} 后面是 {nxt!r}'
                        for n, dec, nxt in bad))


class TestMovedModuleNames:
    """
    搬过去的代码引用的**模块作用域名字**必须齐全。

    判据用 `symtable`（不是 `ast.Name`）：后者会把函数局部变量全算进来，
    报一堆假名字（第一版报了 100 多个）。
    """

    @staticmethod
    def _module_names(path: Path) -> set:
        st = symtable.symtable(path.read_text(encoding='utf-8'),
                               str(path), 'exec')
        out = set()
        for sym in st.get_symbols():
            if sym.is_assigned() or sym.is_imported() or sym.is_namespace():
                out.add(sym.get_name())
        return out

    @staticmethod
    def _referenced_globals(path: Path) -> set:
        """
        所有"只能来自模块作用域"的名字。

        `enclosing` 必须带上**外层函数作用域**：闭包（`ids_for` 用外层
        `by_name`）在 symtable 里是自由变量，不看外层就会被误判成全局。
        """
        st = symtable.symtable(path.read_text(encoding='utf-8'),
                               str(path), 'exec')
        used = set()

        def walk(table, enclosing: frozenset):
            local = {s.get_name() for s in table.get_symbols()
                     if s.is_assigned() or s.is_parameter()
                     or s.is_imported() or s.is_namespace()}
            visible = enclosing | local
            for sym in table.get_symbols():
                if sym.is_referenced() and sym.get_name() not in visible:
                    used.add(sym.get_name())
            for child in table.get_children():
                walk(child, frozenset(visible))

        walk(st, frozenset())
        return used

    def test_no_undefined_module_names(self):
        """
        实测这一条抓到过 `_BODY_LINE_RE` 漏带 —— 那条路径是
        `import_chapters` 判"章节图还是控制流图"，**测试跑一轮 90 秒
        一次只暴露一处**，所以值得静态查。
        """
        for path in (GRAPH_MOD, ASSET_MOD, EXPORT_MOD, COLLAB_MOD, TABLE_MOD):
            defined = self._module_names(path) | set(dir(builtins))
            unknown = sorted(n for n in self._referenced_globals(path)
                             if n not in defined)
            assert not unknown, (
                f'{path.name} 引用了模块作用域里查不到的名字（搬迁漏带）：'
                f'{unknown}')

    def test_editor_has_no_dangling_references_either(self):
        """剩在 `editor.py` 里的代码也不许引用已经搬走的名字。"""
        defined = self._module_names(EDITOR_MOD) | set(dir(builtins))
        unknown = sorted(n for n in self._referenced_globals(EDITOR_MOD)
                         if n not in defined)
        assert not unknown, (
            f'editor.py 引用了查不到的名字：{unknown}')
