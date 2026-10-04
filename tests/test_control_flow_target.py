"""
tests/test_control_flow_target.py
=================================
「导出控制流」的 **落盘路径** 不许被别的参数串位。

缘起（2026-10-04 的真实事故，2026-10-05 追查）：

    仓库根目录里躺着一个 178 字节、名叫 `False` 的文件，内容是控制流产物。

    真凶是**按钮**那条 Qt 接线：

        self._export_flow_btn.clicked.connect(self.export_control_flow)

    `clicked` 会发一个 `bool`（`clicked(bool checked = False)`），
    它成了槽的**第一个位置参数** → `target=False`；而槽里的判据是
    `if target is None:` —— `False is None` 是 False → "没给路径就弹另存为"
    那条分支**被绕过** → `False` 当路径送下去 → `str(False)` = `'False'`
    → 工作目录里多出那个文件（`Path(False).is_file()` 还会抛 TypeError）。

    ⚠ **不要把原因写成"`wrapper` 是第二个位置参数、它落进了 `target`"**
    （这个文件的早期版本就是这么写的）：两个菜单项写的是
    `export_control_flow(wrapper=True/False)`，**关键字传参按名字绑定**，
    `wrapper` 排第几位都不可能落进 `target`。实测那两个菜单项一直正常
    （`tools/probe_flow_export_bool.py` 的 A 段）。
    把 `wrapper` 改成关键字专用只是**加固**，档的不是这条事故。

这里钉三层：
  ① **判据层**（`session_graph`）：不是路径就不许碰磁盘 —— 这一层挡住
     "写垃圾文件"，与调用方怎么写无关；
  ② **签名层**（`window_graph_ops`）：`wrapper` 只许关键字传 —— 这一层
     让"位置串位"这件事**根本写不出来**（`target` 仍是位置参数，正常用法不受影响）；
  ③ **兜底层**（`ui_qt.qt_args.drop_qt_bool`）：槽的入口把 Qt 发来的 `bool`
     当成"没给"（自愈 + 留痕）—— 防的是"任何一条路径把 bool 送进来"，
     包括以后新加的 entry point。判据在
     `tests/test_qt_signal_wiring.py::TestQtBoolIsNeverAnArgument`。

三层都要：只有 ① 的话，UI 依旧静默导出到错误的地方（只是不写盘了）；
只有 ② 的话，别的调用方照样能把 `False`/`None` 塞进来；
只有 ③ 的话，静态上仍然允许写出会串位的调用。
"""

import inspect

import pytest

from storygraph.app.editor import EditorSession
from storygraph.graph.models import Graph


def _graph(n=3):
    g = Graph()
    ids = [g.add_node(f'ch{i}', 100.0 * i, 50.0 * i)
           for i in range(n)]
    g.add_edge(ids[0], ids[1], etype='call', condition='love >= 2')
    g.add_edge(ids[1], ids[2], etype='jump', option_text='去屋顶')
    return g, ids


# ---------------------------------------------------------------------------
#  ① 判据层：不是路径 → 不许写盘
# ---------------------------------------------------------------------------

class TestTargetMustBeAPath:

    @pytest.mark.parametrize('bad', [False, True, 0, 3, None, 1.5, object()])
    def test_non_path_target_is_refused(self, bad, tmp_path, monkeypatch):
        """曾经的形态：`target=False` → 写出一个名叫 `False` 的文件。"""
        monkeypatch.chdir(tmp_path)          # 垃圾文件真会落在 cwd
        res = EditorSession().export_control_flow(_graph()[0], bad)
        assert res['ok'] is False, f'{bad!r} 竟然被当成路径用了：{res}'
        assert not (tmp_path / str(bad)).exists(), (
            f'不该产生文件 `{bad}`：{sorted(p.name for p in tmp_path.iterdir())}')
        # 目录里一个文件都不许多出来
        assert not list(tmp_path.iterdir()), (
            f'有副产物：{sorted(p.name for p in tmp_path.iterdir())}')

    def test_bool_false_does_not_create_file_named_false(self, tmp_path,
                                                         monkeypatch):
        """把**最原始的事故**单独钉一条：名字就叫 `False` 的那个文件。"""
        monkeypatch.chdir(tmp_path)
        EditorSession().export_control_flow(_graph()[0], False)
        assert not (tmp_path / 'False').exists()

    def test_real_path_still_works(self, tmp_path):
        """门禁不能挡住正常用法（假阳性比漏报更贵）。"""
        out = tmp_path / 'flow.rpy'
        res = EditorSession().export_control_flow(_graph()[0], out)
        assert res['ok'], res
        assert 'label ch0_control:' in out.read_text(encoding='utf-8')
        assert res['count'] == 3

    def test_wrapper_keyword_reaches_the_exporter(self, tmp_path):
        """`wrapper` 只能走关键字 —— 顺手确认它没被吞掉。"""
        out = tmp_path / 'flow.rpy'
        res = EditorSession().export_control_flow(_graph()[0], out,
                                                 wrapper=False)
        assert res['ok'], res
        # 原样导出时 **不补** `_control` 后缀
        text = out.read_text(encoding='utf-8')
        assert 'label ch0:' in text, text


# ---------------------------------------------------------------------------
#  ② 签名层：`wrapper` 只许关键字传
# ---------------------------------------------------------------------------

class TestWrapperIsKeywordOnly:

    def _window_method(self):
        try:
            from storygraph.ui_qt.window_graph_ops import GraphOpsMixin
        except Exception as e:                       # noqa: BLE001
            pytest.skip(f'没有 Qt 环境，跳过签名检查：{e}')
        return GraphOpsMixin.export_control_flow

    def test_wrapper_cannot_be_passed_positionally(self):
        fn = self._window_method()
        kw_only = {p.name for p in inspect.signature(fn).parameters.values()
                   if p.kind is inspect.Parameter.KEYWORD_ONLY}
        assert 'wrapper' in kw_only, (
            '`wrapper` 必须只许关键字传 —— 位置传会落进 `target`，'
            f'然后 `str(False)` 被当路径写盘（当前签名：{inspect.signature(fn)}）')

    def test_target_is_still_positional(self):
        """别把正常用法一起焊死：`export_control_flow(path)` 要能用。"""
        fn = self._window_method()
        p = inspect.signature(fn).parameters['target']
        assert p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
