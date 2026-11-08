# -*- coding: utf-8 -*-
"""
地狱测试：**扩展模块 × init python / 自定义语句 / 映射**（2026-11-08 第三轮）。

用户原话：

> 再次核验，写地狱测试攻击这个扩展模块，包括但不限于：有 init python 块直接
> 自定义的操作，扩展模块与自定义模块写法融合，扩展模块与映射模块写法融合……
> 均从真实控件中走，不得直接写 json！要是做不到就说明功能上不够健壮

所以这一组**不自己拼 JSON**：扩展点一律在「扩展」窗口里点出来（`_new_point`
→ 填表 → `cb_op` / `ed_match` / `ed_text` → `_save`），自定义语句一律在
「自定义语句」窗口里教（`_new_statement` → `name` / `sample` →
`_autofill_from_sample` → `_save_statement`），映射一律在「资源映射」窗口里
填（`_editor.add_row` / `set_cell` → `prepare_save` → `confirm_save`）。
断言落在**导出后的产物**与工具自己的读回结果上。

三组攻击面：

  A. **init python 里直接定义的操作**（`def fade_bgm(...)`）：扩展既不能碰
     那个块，也要能补出"调用它"的行；
  B. **自定义语句**：教过的写法能被扩展追加子句，追加之后工具**还认得它**；
     反方向 —— 扩展里声明的写法，自定义窗要看得见、且不许在那里改形状；
  C. **映射**：扩展补出来的 `fadein 1.0` / `with dissolve` 不能让映射那侧的
     翻译与代号扫描失效。

这一轮真抓到的缺陷（都已修，见 `app/batch_edit.py` 的注释）：

  ① 行尾有注释时，追加落进了注释里 —— 音乐永远不淡入，而导出报"改了 1 行"；
     第二次又说"已经是目标内容"，于是永远补不上；
  ② 正则打中 `python:` 块体里的行时，追加 / 单行替换会把 Python 改坏
     （`MUSIC = "x" fadein 1.0`），剧本加载即崩；
  ③ 插入 `init python:` / `define` 这类**只能写在文件最外层**的块头，会被
     缩进着塞进 label（语法错），而且导出一声不吭；
  ④ `append` 与 `span` 同时出现时，落盘会把中间几行**删掉**（计划阶段不报）；
  ⑤ 注释里的 `fadein` 会让幂等判据误判成"已经加过"，永远不补。
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
#  项目 / 窗口 / 导出 —— 公共脚手架
# ---------------------------------------------------------------------------

_INIT_SRC = '''init python:
    def fade_bgm(path, sec=1.0):
        renpy.music.play(path, fadein=sec)

    # 这条注释里写着 play music "bgm/x.mp3"，它不是语句
    MUSIC_LOG = [
        'play music "bgm/a.mp3"',
    ]

label c1:
    play music "bgm/a.mp3"
    "一"
    stop music
    "二"
    return
'''

_FADE_IN = ' fadein 1.0'


def _project(tmp_path, src, name='p'):
    root = tmp_path / name
    ch = root / 'game' / 'c1.rpy'
    ch.parent.mkdir(parents=True)
    ch.write_text(src, encoding='utf-8')
    return root, ch


def _ext(root):
    from storygraph.ui_qt.dialog_extensions import ExtensionDialog
    return ExtensionDialog(None, project_root=str(root))


def _start_point(dlg, ident, kind='batch'):
    """＝ 点「＋ 新建」、写名字、选种类（全是界面上的动作）。"""
    dlg._new_point()
    dlg.ed_id.setText(ident)
    dlg.ed_label.setText(ident)
    dlg.cb_kind.setCurrentIndex(dlg.cb_kind.findData(kind))


def _declare_append(dlg, ident, pattern, append, when=''):
    _start_point(dlg, ident)
    dlg.ck_batch.setChecked(True)
    dlg.cb_op.setCurrentIndex(dlg.cb_op.findData('append'))
    assert dlg._match_row.isVisibleTo(dlg), '追加模式要露出「匹配哪一行」'
    dlg.ed_match.setText(pattern)
    dlg.ed_text.setPlainText(append)
    if when:
        dlg.cb_when.setCurrentIndex(dlg.cb_when.findData(when))
    assert dlg._save(), dlg.status.text()


def _declare_insert(dlg, ident, text, placement='after_last_show', when=''):
    _start_point(dlg, ident)
    dlg.ck_batch.setChecked(True)
    dlg.cb_op.setCurrentIndex(dlg.cb_op.findData('insert'))
    dlg.cb_placement.setCurrentIndex(dlg.cb_placement.findData(placement))
    if when:
        dlg.cb_when.setCurrentIndex(dlg.cb_when.findData(when))
    dlg.ed_text.setPlainText(text)
    assert dlg._save(), dlg.status.text()


def _fade_rule(root, pattern=r'^\s*play music\b', append=_FADE_IN):
    dlg = _ext(root)
    try:
        _declare_append(dlg, 'music.fadein', pattern, append)
    finally:
        dlg.close()


def _export(root, ch):
    from storygraph.app.editor import EditorSession
    s = EditorSession(project_root=root)
    s.load(str(ch))
    return s.export(str(ch))


def _warnings(res) -> str:
    return ' '.join(str(w) for w in (res.get('warnings') or ()))


def _session(root, ch):
    from storygraph.app.editor import EditorSession
    s = EditorSession(project_root=root)
    s.load(str(ch))
    return s


def _init_python_part(text: str) -> str:
    """文件里 `label c1:` 之前的部分（init python 块所在的那一段）。"""
    head, sep, _tail = text.partition('label c1:')
    assert sep, text
    return head


# ---------------------------------------------------------------------------
#  A. init python 里直接定义的操作
# ---------------------------------------------------------------------------

class TestInitPythonIsNeverTouched:
    def test_the_append_leaves_init_python_byte_for_byte(self, qapp, tmp_path):
        root, ch = _project(tmp_path, _INIT_SRC)
        _fade_rule(root)
        res = _export(root, ch)
        assert res['ok'], res
        text = ch.read_text(encoding='utf-8')
        # init python 那一段（含注释与字符串里的 play music）一个字节都没动
        assert _init_python_part(text) == _init_python_part(_INIT_SRC), text
        # 只有 label 里那条真的播放被补了一次
        assert text.count('play music "bgm/a.mp3" fadein 1.0') == 1, text

    def test_a_regex_that_matches_inside_init_python_changes_nothing(
            self, qapp, tmp_path):
        """`init python` 不在拍区间里 —— 打中它的正则不该被当成"要改的行"。"""
        root, ch = _project(tmp_path, _INIT_SRC)
        _fade_rule(root, pattern=r'^\s*MUSIC_LOG\s*=')
        res = _export(root, ch)
        assert res['ok'], res
        assert ch.read_text(encoding='utf-8') == _INIT_SRC
        assert '扩展批处理改了' not in str(res.get('note') or ''), res

    def test_an_unanchored_regex_only_hits_the_real_statement(self, qapp,
                                                              tmp_path):
        """`play music` 这种松散正则会命中注释/字符串 —— 但只补真语句那一行。"""
        root, ch = _project(tmp_path, _INIT_SRC)
        _fade_rule(root, pattern=r'play music')
        res = _export(root, ch)
        assert res['ok'], res
        text = ch.read_text(encoding='utf-8')
        assert _init_python_part(text) == _init_python_part(_INIT_SRC), text
        assert text.count('fadein 1.0') == 1, text
        assert 'play music "bgm/a.mp3" fadein 1.0' in text

    def test_an_inserted_call_can_use_a_function_from_init_python(
            self, qapp, tmp_path):
        """场景：操作定义在 init python 里，扩展只负责"调用它"。"""
        src = ('init python:\n'
               '    def fade_bgm(path, sec=1.0):\n'
               '        renpy.music.play(path, fadein=sec)\n'
               '\n'
               'label c1:\n'
               '    show lan happy\n'
               '    "一"\n'
               '    show lan sad\n'
               '    "二"\n'
               '    return\n')
        root, ch = _project(tmp_path, src)
        dlg = _ext(root)
        try:
            _declare_insert(dlg, 'music.call',
                            '$ fade_bgm("bgm/a.mp3")')
        finally:
            dlg.close()
        res = _export(root, ch)
        assert res['ok'], res
        text = ch.read_text(encoding='utf-8')
        assert text.count('$ fade_bgm("bgm/a.mp3")') == 2, text
        assert text.count('\n    $ fade_bgm') == 2, text      # label 体缩进
        assert 'def fade_bgm' in _init_python_part(text)
        # 再导一次不翻倍
        _export(root, ch)
        assert ch.read_text(encoding='utf-8') == text

    def test_a_file_that_is_only_init_python_is_left_alone(self, qapp,
                                                           tmp_path):
        """纯 `init python` 文件（没有 label）不参与拍，一个字节都不碰。"""
        root, ch = _project(tmp_path, _INIT_SRC)
        pure = root / 'game' / 'pure.rpy'
        pure_src = ('init python:\n'
                    '    def helper():\n'
                    '        return 1\n')
        pure.write_text(pure_src, encoding='utf-8')
        _fade_rule(root)
        res = _export(root, ch)
        assert res['ok'], res
        assert pure.read_text(encoding='utf-8') == pure_src


# ---------------------------------------------------------------------------
#  B1. 行尾注释 / python 块 —— 补进去必须是"能跑的代码"
# ---------------------------------------------------------------------------

_COMMENT_SRC = ('label c1:\n'
                '    play music "bgm/a.mp3"   # 战斗曲\n'
                '    "一"\n'
                '    stop music  # 收\n'
                '    return\n')


class TestTheAppendLandsBeforeTheComment:
    def test_the_comment_stays_a_comment(self, qapp, tmp_path):
        """从前追加落进注释里：音乐永远不淡入，导出却说"改了 1 行"。"""
        root, ch = _project(tmp_path, _COMMENT_SRC)
        dlg = _ext(root)
        try:
            _declare_append(dlg, 'music.fadein', r'^\s*play music\b', _FADE_IN)
            _declare_append(dlg, 'music.fadeout', r'^\s*stop music\b',
                            ' fadeout 1.0')
        finally:
            dlg.close()
        res = _export(root, ch)
        assert res['ok'], res
        text = ch.read_text(encoding='utf-8')
        line = next(x for x in text.splitlines() if 'bgm/a.mp3' in x)
        assert 'fadein 1.0' in line and '# 战斗曲' in line, line
        assert line.index('fadein 1.0') < line.index('#'), line
        # 注释前的空格原样保留（不动人的排版）
        assert 'fadein 1.0   # 战斗曲' in line, line
        # stop music 那条的注释同理
        stop = next(x for x in text.splitlines() if x.strip().startswith('stop'))
        assert stop.strip() == 'stop music fadeout 1.0  # 收', stop

    def test_a_second_export_does_not_add_it_again(self, qapp, tmp_path):
        root, ch = _project(tmp_path, _COMMENT_SRC)
        _fade_rule(root)
        _export(root, ch)
        once = ch.read_text(encoding='utf-8')
        res = _export(root, ch)
        assert ch.read_text(encoding='utf-8') == once
        assert '已经是目标内容' in _warnings(res), res

    def test_a_hash_inside_the_filename_is_not_a_comment(self, qapp, tmp_path):
        src = ('label c1:\n'
               '    play music "bgm/#1.mp3"\n'
               '    "一"\n'
               '    return\n')
        root, ch = _project(tmp_path, src)
        _fade_rule(root)
        res = _export(root, ch)
        assert res['ok'], res
        text = ch.read_text(encoding='utf-8')
        assert 'play music "bgm/#1.mp3" fadein 1.0' in text, text

    def test_a_comment_that_mentions_the_keyword_does_not_block_it(
            self, qapp, tmp_path):
        """注释里写着 `# 这里要 fadein` 时，旧判据会误判"已经加过"。"""
        src = ('label c1:\n'
               '    play music "bgm/a.mp3"  # 这里要 fadein\n'
               '    "一"\n'
               '    return\n')
        root, ch = _project(tmp_path, src)
        _fade_rule(root)
        res = _export(root, ch)
        assert res['ok'], res
        text = ch.read_text(encoding='utf-8')
        line = next(x for x in text.splitlines() if 'bgm/a.mp3' in x)
        code = line.split('#', 1)[0]
        assert 'fadein 1.0' in code, line

    def test_the_preview_shows_the_finished_line(self, qapp, tmp_path):
        """预演里那一行不能是空白 —— 人得看见"会补成什么样"。"""
        root, ch = _project(tmp_path, _COMMENT_SRC)
        dlg = _ext(root)
        try:
            _declare_append(dlg, 'music.fadein', r'^\s*play music\b', _FADE_IN)
            plan = dlg._preview_batch()
        finally:
            dlg.close()
        assert plan is not None
        shown = dlg.preview.toPlainText()
        assert 'append' in shown, shown
        assert 'fadein 1.0   # 战斗曲' in shown, shown


_PY_SRC = ('label c1:\n'
           '    python:\n'
           '        MUSIC = "bgm/a.mp3"\n'
           '    play music "bgm/a.mp3"\n'
           '    "一"\n'
           '    return\n')


class TestPythonBlockIsProtected:
    def test_a_line_inside_a_python_block_is_not_appended(self, qapp, tmp_path):
        root, ch = _project(tmp_path, _PY_SRC)
        _fade_rule(root, pattern=r'^\s*MUSIC\s*=')
        res = _export(root, ch)
        assert res['ok'], res
        assert ch.read_text(encoding='utf-8') == _PY_SRC
        said = _warnings(res)
        assert 'python 块' in said, res

    def test_the_guard_does_not_take_the_rest_of_the_chapter_down(self, qapp,
                                                                  tmp_path):
        """同一份文件里：python 那行跳过，真语句照补。"""
        root, ch = _project(tmp_path, _PY_SRC)
        _fade_rule(root, pattern=r'(?i)^\s*(?:MUSIC|play music)\b')
        res = _export(root, ch)
        assert res['ok'], res
        text = ch.read_text(encoding='utf-8')
        assert 'MUSIC = "bgm/a.mp3" fadein' not in text, text
        assert 'play music "bgm/a.mp3" fadein 1.0' in text, text
        assert 'python 块' in _warnings(res), res

    def test_a_comment_line_is_never_appended_to(self, qapp, tmp_path):
        src = ('label c1:\n'
               '    # 音乐要淡入\n'
               '    play music "bgm/a.mp3"\n'
               '    "一"\n'
               '    return\n')
        root, ch = _project(tmp_path, src)
        _fade_rule(root, pattern=r'#\s*音乐')
        res = _export(root, ch)
        assert res['ok'], res
        assert ch.read_text(encoding='utf-8') == src
        assert '注释行' in _warnings(res), res

    def test_inserting_an_init_python_block_into_a_label_is_refused(
            self, qapp, tmp_path):
        """块头只能写文件最外层；塞进 label 是语法错，宁可报"规则不合法"。"""
        root, ch = _project(tmp_path, _PY_SRC)
        dlg = _ext(root)
        try:
            _declare_insert(dlg, 'bad.block',
                            'init python:\n    persistent.x = 1')
        finally:
            dlg.close()
        res = _export(root, ch)
        assert res['ok'], res
        assert ch.read_text(encoding='utf-8') == _PY_SRC
        said = _warnings(res)
        assert '不合法' in said and '最外层' in said, res


# ---------------------------------------------------------------------------
#  B2. 自定义语句 × 扩展
# ---------------------------------------------------------------------------

_CUSTOM_SRC = ('label c1:\n'
               '    myfade 1.0\n'
               '    "一"\n'
               '    myfade 2.0\n'
               '    "二"\n'
               '    return\n')


def _teach_myfade(root):
    """在「自定义语句」窗口里教一条 `myfade 1.0`（真控件）。"""
    from storygraph.ui_qt.dialog_learn import CustomStatementDialog
    dlg = CustomStatementDialog(None, project_root=str(root))
    try:
        dlg._new_statement()
        dlg.name.setText('myfade')
        dlg.sample.setText('myfade 1.0')
        dlg._autofill_from_sample()
        assert dlg._save_statement(), dlg._status.text()
    finally:
        dlg.close()
    return json.loads((root / 'storygraph.rules.json').read_text('utf-8'))


def _declare_statement(root, ident, *, key='秒数', label='秒数'):
    """在扩展窗口里声明一条**写法**（kind=statement），带一格字段。"""
    dlg = _ext(root)
    try:
        _start_point(dlg, ident, kind='statement')
        dlg._add_field()
        row = dlg.tbl.rowCount() - 1
        dlg.tbl.item(row, 0).setText(key)          # 列名
        dlg.tbl.item(row, 4).setText(label)        # 标签
        assert dlg._save(), dlg.status.text()
    finally:
        dlg.close()


class TestCustomStatementFusion:
    def test_a_clause_appended_to_a_custom_statement_stays_recognized(
            self, qapp, tmp_path):
        """教过的写法被扩展追加 ` with dissolve` 之后，**还认得它是这条语句**。"""
        root, ch = _project(tmp_path, _CUSTOM_SRC)
        data = _teach_myfade(root)
        assert any(s['name'] == 'myfade' for s in data['statements']), data

        dlg = _ext(root)
        try:
            _declare_append(dlg, 'myfade.clause', r'^\s*myfade\b',
                            ' with dissolve')
        finally:
            dlg.close()
        res = _export(root, ch)
        assert res['ok'], res
        text = ch.read_text(encoding='utf-8')
        assert text.count('myfade 1.0 with dissolve') == 1, text
        assert text.count('myfade 2.0 with dissolve') == 1, text
        # 读回来：仍然是被声明过的那条写法（T1），不是"不认识的写法"
        s = _session(root, ch)
        named = [b for b in s.beats if getattr(b, 'spec_name', '') == 'myfade']
        assert len(named) == 2, [(b.seq, b.spec_name) for b in s.beats]
        assert all('with dissolve' in b.opaque for b in named)
        # 再导一次：逐字节不变（幂等，且不把子句丢掉）
        _export(root, ch)
        assert ch.read_text(encoding='utf-8') == text

    def test_the_extension_statement_is_visible_in_the_custom_window(
            self, qapp, tmp_path):
        root, _ch = _project(tmp_path, _CUSTOM_SRC)
        _declare_statement(root, 'myfade2')
        from storygraph.ui_qt.dialog_learn import CustomStatementDialog
        dlg = CustomStatementDialog(None, project_root=str(root))
        try:
            names = [dlg.known.item(i, 0).text()
                     for i in range(dlg.known.rowCount())]
            assert 'myfade2' in names, names
        finally:
            dlg.close()

    def test_the_custom_window_refuses_to_rewrite_an_extension_statement(
            self, qapp, tmp_path):
        """在会被盖住的文件里保存 = 改了没反应；窗口必须点名拒绝。"""
        root, _ch = _project(tmp_path, _CUSTOM_SRC)
        _declare_statement(root, 'myfade2')
        from storygraph.ui_qt.dialog_learn import CustomStatementDialog
        dlg = CustomStatementDialog(None, project_root=str(root))
        try:
            names = [dlg.known.item(i, 0).text()
                     for i in range(dlg.known.rowCount())]
            dlg.known.selectRow(names.index('myfade2'))
            dlg._edit_selected()
            assert dlg.name.text() == 'myfade2'
            ok = dlg._save_statement()
            assert ok is False
            assert 'storygraph.ext.json' in dlg._status.text(), dlg._status.text()
        finally:
            dlg.close()

    def test_an_extension_statement_reaches_the_card(self, qapp, tmp_path):
        """扩展里声明的写法 → 注册表 → 卡片头（这条拍是什么）。"""
        root, ch = _project(tmp_path,
                            'label c1:\n    myfade2 1.0\n    "一"\n    return\n')
        _declare_statement(root, 'myfade2')
        from storygraph.story import statement_registry as SR
        from storygraph.ui_qt.window_card import _statement_of
        reg = SR.StatementRegistry.for_project(str(root))
        spec = reg.match('myfade2')
        assert spec is not None, reg.names()
        # 扩展点只存 `pattern`（形状）；列由 `spec_columns` 机械推出来 ——
        # 卡片 / 表格拿到的就是这一份（而不是另写一套）。
        cols = SR.spec_columns(reg, 'myfade2')
        assert [c['name'] for c in cols] == ['秒数'], (cols, spec.pattern)
        s = _session(root, ch)
        beat = next(b for b in s.beats if getattr(b, 'spec_name', ''))
        assert _statement_of(beat, reg)['name'] == 'myfade2'


# ---------------------------------------------------------------------------
#  C. 映射 × 扩展
# ---------------------------------------------------------------------------

_MUSIC_SRC = ('label c1:\n'
              '    play music "bgm/a.mp3"\n'
              '    "一"\n'
              '    return\n')


def _map_music_via_window(root):
    """在「资源映射」窗口里加一条中文叫法（真控件），并保存到项目根。"""
    from storygraph.ui_qt.mapping_dialog import MappingDialog
    dlg = MappingDialog(None, project_root=str(root))
    try:
        dlg._editor.load({'schema': 'storygraph.mapping/1'})
        dlg._editor.set_section('vocabulary')
        row = dlg._editor.add_row()
        assert row >= 0
        for column, value in ((1, 'music'), (2, 'bgm/a.mp3'),
                              (3, '战斗'), (4, '18')):
            assert dlg._editor.set_cell(row, column, value) == ''
        assert dlg.prepare_save(), dlg._status.text()
        assert dlg.confirm_save(), dlg._status.text()
    finally:
        dlg.close()
    return root / 'storygraph.mapping.json'


class TestMappingFusion:
    def test_the_mapped_music_name_survives_the_fade(self, qapp, tmp_path):
        """扩展补 ` fadein 1.0` → 卡片音乐格 → 映射翻译成 `[音乐] 18 战斗`。"""
        root, ch = _project(tmp_path, _MUSIC_SRC)
        _map_music_via_window(root)
        _fade_rule(root)
        res = _export(root, ch)
        assert res['ok'], res
        s = _session(root, ch)
        cell = next((b.bgm for b in s.beats if b.bgm), '')
        assert cell == '[音乐] bgm/a.mp3 fadein 1.0', cell
        from storygraph.app.asset_catalog import AssetCatalog
        vocab = AssetCatalog(project_root=str(root)).vocabulary()
        shown = vocab.label_in_text('music', cell)
        assert shown == '[音乐] 18 战斗 fadein 1.0', shown

    def test_sprite_codes_still_read_a_show_line_with_the_clause(
            self, qapp, tmp_path):
        """扩展给 `show` 补 ` with dissolve` 之后，映射的代号扫描照旧认。"""
        src = ('label c1:\n'
               '    # 表情B7\n'
               '    show lan happy\n'
               '    "一"\n'
               '    return\n')
        root, ch = _project(tmp_path, src)
        dlg = _ext(root)
        try:
            _declare_append(dlg, 'show.clause', r'^\s*show\s+lan\b',
                            ' with dissolve')
        finally:
            dlg.close()
        res = _export(root, ch)
        assert res['ok'], res
        text = ch.read_text(encoding='utf-8')
        assert 'show lan happy with dissolve' in text, text
        from storygraph.assets.sprite_codes import scan_script_text
        hits = scan_script_text(
            text, name='c1', known={'lan': {'expr': {'happy'}, 'outfit': set()}})
        assert len(hits) == 1, hits
        assert (hits[0].char, hits[0].letter, hits[0].number) == ('lan', 'B', '7')
        assert hits[0].expr == 'happy', hits[0]

    def test_the_export_does_not_rewrite_the_mapping_file(self, qapp, tmp_path):
        root, ch = _project(tmp_path, _MUSIC_SRC)
        path = _map_music_via_window(root)
        before = path.read_bytes()
        _fade_rule(root)
        _export(root, ch)
        assert path.read_bytes() == before
