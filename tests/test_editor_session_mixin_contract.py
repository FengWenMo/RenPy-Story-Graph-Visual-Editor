"""
P2-6 拆分守卫（第二批）：**mixin 之间的隐式耦合** 与 **对外契约**
================================================================

`test_editor_session_split.py` 守的是"每块住在该住的地方"（**归属**）。
这一批守的是**归属之外**那三类同样会在拆分后悄悄坏掉的东西：

  1. **`REQUIRES` 不许撒谎**（双向）。
     现有守卫只查"声明了的，宿主上有没有" —— 那是个**单向**检查：
     代码里真正读到的 `self.X` 是**事实**，手写的 `REQUIRES` 是**声明**，
     两边谁漏了都发现不了。

     实测（2026-10-03 探针 `tools/audit_mixin_contract.py`）：
     `ExportMixin.should_write_transforms` 读了 `self._transforms_decision`，
     **没写进自己的 `REQUIRES`**；而 `AssetRegistryMixin` 声明了它、
     自己却只写不读。于是这个跨块共享状态**两边都没说清**。

  2. **跨块共享状态必须被声明**。两个 mixin 通过一个实例属性耦合
     （一个写、一个读），是拆分之后**最常见**的隐式耦合 ——
     它比"方法搬家"危险得多：方法搬错了会当场 `AttributeError`，
     而共享状态只在"写那块先跑、读那块后跑"时才有值，
     换个调用顺序就静默拿到 `None`。

  3. **re-export 不许漏**（从**调用方**反查）。
     现有守卫只测了两个手挑的名字（`graph_from_layout_dict`）。
     真正的风险是**没被挑中的那些**：`from storygraph.app.editor import X`
     散落在 `ui_qt/` 里，漏一个就只有运行到那条路才炸，
     而 `ui_qt` 没有行为测试兜着。所以这里**扫调用点**而不是列清单。

  4. **字段清单不许与列模型漂移**。`PRIMARY_FIELDS` / `ADVANCED_FIELDS`
     是前端契约（表单建哪些控件、回读哪些值）。`BEAT_FIELDS` 改了列名
     而这里没跟，前端就**静默丢字段**（不报错，值直接没了）。

为什么单开一个文件：判据全是**静态扫源码**（`ast`），
和 `test_editor_session_split.py` 那批"查类成员"的写法不是一回事，
混在一起会让两边都难读。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / 'storygraph'
APP = PKG / 'app'

#: 被拆出去的五个模块（第二批守卫的扫描对象）
BLOCKS = {
    'session_table': 'TableSessionMixin',
    'session_graph': 'GraphSessionMixin',
    'session_assets': 'AssetRegistryMixin',
    'session_export': 'ExportMixin',
    'session_collab': 'CollabMixin',
}

#: 也要扫 re-export 的模块（`editor.py` 是门面，其余是新家）
REEXPORT_MODULES = {'editor'} | set(BLOCKS)


def _class_def(path: Path, cls_name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            return node
    raise AssertionError(f'{path.name} 里找不到 {cls_name}')


def _own_members(cls: ast.ClassDef) -> set:
    out = set()
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(item.name)
        elif isinstance(item, ast.Assign):
            for t in item.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            out.add(item.target.id)
    return out


def _self_attrs(cls: ast.ClassDef) -> tuple:
    """
    类体里出现的 `self.X`，分成**读**（Load）与**写**（Store）。

    为什么必须分开：`self.x = 1` 是**造**状态，不是依赖；
    `self.x`（读）才是"我指望别人给我这个"。把写算进依赖，
    每个 mixin 都会"依赖"一大堆自己的私有字段，判据立刻失效。
    """
    loads, stores = set(), set()
    for node in ast.walk(cls):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == 'self'):
            (loads if isinstance(node.ctx, ast.Load) else stores).add(node.attr)
    return loads, stores


def _requires_literal(cls: ast.ClassDef) -> set:
    for item in cls.body:
        if isinstance(item, ast.Assign):
            for t in item.targets:
                if isinstance(t, ast.Name) and t.id == 'REQUIRES':
                    try:
                        return set(ast.literal_eval(item.value))
                    except ValueError:
                        return set()
    return set()


def _scan_all_blocks() -> dict:
    """一次扫完五个 mixin，返回 `{key: {own, loads, stores, requires}}`。"""
    out = {}
    for key, cls_name in BLOCKS.items():
        cls = _class_def(APP / f'{key}.py', cls_name)
        own = _own_members(cls)
        loads, stores = _self_attrs(cls)
        out[key] = {'own': own, 'loads': loads, 'stores': stores,
                    'requires': _requires_literal(cls)}
    return out


SCAN = _scan_all_blocks()


# ==================================================================
#  1. REQUIRES 不许撒谎（双向）
# ==================================================================
class TestRequiresIsHonest:
    """
    "声明"和"事实"必须对齐 —— 两个方向都要查。

    只查一个方向的话：漏声明的那半边现在恰好能跑（宿主 `EditorSession`
    恰好有那个属性），于是测试一直绿，直到哪天宿主改了初始化顺序，
    才在**运行时**炸。守卫的意义就是让它在**拆的时候**就红。
    """

    @pytest.mark.parametrize('key', sorted(BLOCKS))
    def test_read_dependencies_are_declared(self, key):
        """
        代码里读到的 `self.X`（且不是本 mixin 自己的）**必须**在 `REQUIRES` 里。

        这是现有守卫**没有**的那个方向：现有只查"声明的有没有"，
        不查"用了的有没有声明"。
        """
        info = SCAN[key]
        external = info['loads'] - info['own']
        undeclared = sorted(external - info['requires'])
        assert not undeclared, (
            f'{BLOCKS[key]} 读到了这些宿主成员却没写进 REQUIRES：{undeclared}\n'
            f'（现在能跑只是因为 `EditorSession` 恰好有它们 —— '
            f'宿主一改初始化顺序就会在运行时炸）')

    @pytest.mark.parametrize('key', sorted(BLOCKS))
    def test_no_dependency_is_unreachable(self, key):
        """
        兜底：不管有没有声明，**宿主实例上必须真有**。

        这条是"必炸"判据（上面那条是"契约没写清"判据）。
        两者分开写是因为修法不同：这条红了要改代码，那条红了只改声明。
        """
        from storygraph.app.editor import EditorSession

        info = SCAN[key]
        external = sorted(info['loads'] - info['own'])
        missing = [n for n in external if not hasattr(EditorSession(), n)]
        assert not missing, (
            f'{BLOCKS[key]} 依赖这些成员，但 `EditorSession` 实例上取不到：'
            f'{missing}')


# ==================================================================
#  2. 跨块共享状态
# ==================================================================
class TestCrossBlockSharedState:
    """
    **一个 mixin 写、另一个 mixin 读** 的实例属性 = 隐式耦合。

    为什么单列一类（它比"方法搬家"危险）：
      · 方法搬错 → 调用当场 `AttributeError`，测试立刻红；
      · 共享状态搬错 → 写那块没跑过时读到的只是默认值，
        **不报错**，行为悄悄不对（`should_write_transforms` 拿到 `None`
        时会重新判定一遍，而不是沿用缓存 —— 症状是"偶尔多写一段
        transform 定义"，没人会把这和拆分联系起来）。

    判据：该属性被**至少两个** mixin 触及（一写一读，或两读），
    那么**每一个**读它的 mixin 都要在自己的 `REQUIRES` 里写清楚。
    """

    def _touched_by(self) -> tuple:
        """`{attr: {'read': [mixin…], 'write': [mixin…]}}`。"""
        read, write = {}, {}
        for key in SCAN:
            for attr in SCAN[key]['loads']:
                read.setdefault(attr, []).append(key)
            for attr in SCAN[key]['stores']:
                write.setdefault(attr, []).append(key)
        return read, write

    def test_shared_state_is_declared_by_every_reader(self):
        """
        跨块共享的属性 → **每个**读它的 mixin 都必须声明。

        实测这条会抓到 `_transforms_decision`：
        `AssetRegistryMixin.register_missing_assets` 把它清成 `None`（写），
        `ExportMixin.should_write_transforms` 读它，而 `ExportMixin` 没声明。
        """
        read, write = self._touched_by()
        problems = []
        for attr, readers in sorted(read.items()):
            writers = set(write.get(attr, ()))
            touched = writers | set(readers)
            # 只被一个 mixin 触及 = 私有状态，不是耦合
            if len(touched) < 2:
                continue
            for key in readers:
                if attr not in SCAN[key]['requires']:
                    problems.append(
                        f'{BLOCKS[key]} 读 self.{attr}'
                        f'（写它的是 {[BLOCKS[w] for w in sorted(writers)] or "EditorSession"}）'
                        f'，却没写进 REQUIRES')
        assert not problems, (
            '存在没声明的跨块共享状态：\n  · ' + '\n  · '.join(problems))

    def test_shared_state_is_initialized_by_the_host(self):
        """
        跨块共享的属性**必须在 `EditorSession.__init__` 里初始化**。

        "属性是否存在取决于之前发生过什么"是拆分时最容易漏的东西 ——
        宿主没初始化的话，第一次读就是 `AttributeError`，
        而且只在"先调了 A 没调 B"这种顺序下才出现。
        """
        from storygraph.app.editor import EditorSession

        read, write = self._touched_by()
        session = EditorSession()
        missing = []
        for attr, readers in sorted(read.items()):
            touched = set(write.get(attr, ())) | set(readers)
            if len(touched) < 2:
                continue
            if not hasattr(session, attr):
                missing.append(attr)
        assert not missing, (
            f'这些跨块共享状态在 `EditorSession()` 上取不到（'
            f'`__init__` 里漏了初始化）：{sorted(missing)}')


# ==================================================================
#  3. re-export 不许漏（从调用方反查）
# ==================================================================
class TestReexportsSurviveTheSplit:
    """
    扫**全仓库**的 `from storygraph.app.<模块> import X`，断言 X 真的存在。

    为什么不列清单（现有 `test_module_functions_are_reexported_from_editor`
    就是清单式，只测了 2 个名字）：清单会漏，**漏的那个恰恰没人守**。
    而调用点是事实 —— 从调用方反查，一个都跑不掉。
    """

    @staticmethod
    def _import_sites() -> list:
        """`(文件, 行号, 模块名, [导入的名字…])`。"""
        out = []
        for path in sorted(PKG.rglob('*.py')):
            if '__pycache__' in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding='utf-8'))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                mod = node.module or ''
                if not mod.startswith('storygraph.app.'):
                    continue
                tail = mod.split('.')[-1]
                if tail not in REEXPORT_MODULES:
                    continue
                names = [a.name for a in node.names
                         if a.name != '*' and not a.asname]
                if names:
                    out.append((path, node.lineno, mod, names))
        return out

    def test_there_are_sites_to_check(self):
        """守卫自己别失效：至少要扫到几个调用点，否则是判据写坏了。"""
        sites = self._import_sites()
        assert len(sites) >= 3, (
            f'只扫到 {len(sites)} 个调用点，判据大概率写坏了')

    def test_every_imported_name_exists(self):
        import importlib

        bad = []
        for path, lineno, mod, names in self._import_sites():
            try:
                target = importlib.import_module(mod)
            except Exception as e:                    # noqa: BLE001
                bad.append(f'{path.name}:{lineno} import {mod} 失败：{e}')
                continue
            for n in names:
                if not hasattr(target, n):
                    bad.append(
                        f'{path.name}:{lineno} `from {mod} import {n}` '
                        f'—— {mod} 上没有这个名字（搬迁漏了 re-export）')
        assert not bad, ('有 re-export 漏了：\n  · ' + '\n  · '.join(bad))


# ==================================================================
#  4. 字段清单不许与列模型漂移
# ==================================================================
class TestFieldContract:
    """
    `PRIMARY_FIELDS` / `ADVANCED_FIELDS` 是**前端契约**。

    前端按这两个清单建控件、回读值（`ui_qt/main_window.py:472`）。
    `BEAT_FIELDS` 是列顺序的唯一真源（契约 E2）。两侧一旦漂移，
    症状是"改了表单但值没进去"——**不报错**，因为回读只是
    `for n in PRIMARY_FIELDS: d[n] = ...`，缺的键安静地消失。
    """

    #: 当前**已知**的缺口（2026-10-03 实测）。
    #:
    #: 2026-10-05：**已清空** —— `Notes` 从 `ADVANCED_FIELDS` 里删掉了
    #: （前端不再让用户填一个注定蒸发的字段），`Comment` 顶上它的位置。
    #: 这个集合的终点是空，现在到了。
    KNOWN_MISSING: set = set()

    def test_every_field_is_a_real_column(self):
        """
        前端契约里的每个字段都必须是 `BEAT_FIELDS` 里真有的列。

        **现在的红是 `Notes`，而它是个有意/无意打架的产物**，不是判据写错：

          · `beat_model.from_row` 的注释写着「Notes 已经取消：老表里有内容就
            并进注释，绝不丢字」—— 也就是说 **Notes 这一列是有意取消的**，
            老表里残留的值会被读回来合并进 `Comment`（那条兼容链路是对的）；
          · 可 `ADVANCED_FIELDS` 里**还留着** `Notes`，前端照样建"备注"控件、
            照样把值回读进 `Beat.notes`。

        于是现状是：编剧在卡片里填了备注 → 导出到 Excel → 蒸发，且不报错。
        **二选一，没有中间态**：

          · 要留这个功能 → `BEAT_FIELDS` 加 `Notes` 列，`to_row` 写它；
          · 确认已经取消 → 把 `Notes` 从 `ADVANCED_FIELDS` 里删掉，
            别让用户对着一个注定蒸发的字段认真写字。

        选完把 `KNOWN_MISSING` 里那一行删掉 —— 这个集合的终点必须是空。
        """
        from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS
        from storygraph.story.beat_model import BEAT_FIELDS

        unknown = [n for n in (*PRIMARY_FIELDS, *ADVANCED_FIELDS)
                   if n not in BEAT_FIELDS]
        unexpected = [n for n in unknown if n not in self.KNOWN_MISSING]
        assert not unexpected, (
            f'前端契约里的字段在 `BEAT_FIELDS` 里找不到（改列会静默丢值）：'
            f'{unexpected}')
        assert not unknown, (
            f'`Notes` 仍然既在 `ADVANCED_FIELDS` 里（前端让用户填）'
            f'又不在 `BEAT_FIELDS` 里（导出会蒸发）—— 上面 docstring 里的'
            f'二选一还没做')

    def test_the_two_groups_do_not_overlap(self):
        """同一个字段不能既在主区又在高级区（会被回读两次，后者赢）。"""
        from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS

        dup = sorted(set(PRIMARY_FIELDS) & set(ADVANCED_FIELDS))
        assert not dup, f'主区与高级区字段重复：{dup}'

    def test_legacy_notes_column_is_merged_into_comment(self):
        """
        **别误伤**护栏：老表里残留的 `Notes` 列必须被读回来并进 `Comment`。

        这条是绿的，且**修上面那条时它必须保持绿**。
        因为"取消 Notes 列"这个决定本身没错（老表兼容链路写得很对：
        `from_row` 读 Notes、非空就并进 Comment、绝不丢字）——
        错的是前端还在让用户填。修的时候很容易顺手把 `from_row`
        里那段一起删掉，那就**真**丢老数据了。
        """
        from storygraph.story.beat_model import Beat

        row = {'Seq': 1, 'Label': 'a', 'Kind': 'say', 'Speaker': 'e',
               'Text': '一句话', 'Comment': '原注释', 'Notes': '老表的备注'}
        beat = Beat.from_row(row)
        assert '老表的备注' in beat.comment, (
            f'老表 Notes 没被并进 Comment（会丢历史数据）：{beat.comment!r}')
        assert '原注释' in beat.comment

    def test_notes_survives_excel_roundtrip(self, tmp_path):
        """
        **内容不许丢**（2026-10-05 修完转绿）。

        这条原本断言 `back[i].notes == beats[i].notes`，而那是**错的判据** ——
        它假设 `Notes` 应该是一个能往返的**独立列**。实测后的结论不是这样：

          · `Notes` 已经从 `ADVANCED_FIELDS` 里删掉（前端不再让用户填），
            因为它与主区的 `Comment`（表头「注释」）语义完全重叠，
            而 `Comment` 才是 `BEAT_FIELDS` 里真有的列、导出会写的那个；
          · `from_row` 一直把老表的 `Notes` **并进 `Comment`**（"绝不丢字"）；
          · **这一轮补上了对称的另一半**：`to_row` 也把 `Beat.notes`
            并进注释列 —— 以前它被无声丢掉，于是
            `Beat(notes='这句要重录')` 往返一次内容就蒸发了
            （修之前这条测试是红的，而且红得有道理）。

        所以正确的判据是"**那段文字还在**"，落在 `comment` 里 —— 而不是
        "`notes` 字段等于原值"。字段取消了，文字没丢。
        """
        from storygraph.story.beat_model import Beat
        from storygraph.story.excel_export import export_beats_to_excel
        from storygraph.story.excel_import import import_beats_from_excel

        beats = [
            Beat(seq=1, label='a', kind='say', speaker='e', text='第一句',
                 notes='这句要重录'),
            Beat(seq=2, label='a', kind='say', speaker='e', text='第二句',
                 notes='备用台词', comment='原有注释'),
        ]
        xl = tmp_path / 'notes.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)

        assert len(back) == len(beats), back
        for src, got in zip(beats, back):
            assert src.notes in got.comment, (
                f'备注内容在 Excel 往返里丢了：{src.notes!r} 不在 '
                f'{got.comment!r} 里')
        # 有注释的那条要**两段都在**（不能因为合并把原有注释冲掉）
        assert '原有注释' in back[1].comment, back[1].comment

    def test_field_lists_are_defined_once(self):
        """
        **只许有一份定义**。

        `editor.py` 的注释声称 `session_table.py` 也 re-export 了同一对名字、
        "两边指向同一个对象"。实测 `session_table` 上**没有** `PRIMARY_FIELDS`
        —— 注释与现实不符。这里要求：要么真的只有一份定义（现状，OK），
        要么 re-export 的必须是**同一个对象**（不能是另一份字面量）。
        """
        import storygraph.app.editor as E
        import storygraph.app.session_table as T

        for name in ('PRIMARY_FIELDS', 'ADVANCED_FIELDS'):
            other = getattr(T, name, None)
            if other is None:
                continue                      # session_table 根本没定义 → 单一真源
            assert other is getattr(E, name), (
                f'{name} 在 editor 与 session_table 里是两份不同的对象，'
                f'改一边会漂移')
