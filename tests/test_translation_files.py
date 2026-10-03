"""
P2-11：**翻译文件解析（现代 `tl` 格式）**
========================================

风险清单那条："`parse_translation_files` 不支持现代 `tl` 格式（历史遗留缺口）"。

Ren'Py 的 `tl/<lang>/` 目录里有两种写法，**同一种语言会同时用**：

旧式（`translate <lang> <id>:` + `# old` / `new`）：

    translate schinese start_1a2b3c4d:
        # e "你好"
        e "你好啊"

新式（**对话 id 直接当正文的裸字符串行**，Ren'Py 自己生成的 tl 文件长这样）：

    translate schinese start_1a2b3c4d:
        "你好啊"

字符串翻译（`translate <lang> strings:`）：

    translate schinese strings:
        old "Start"
        new "开始"

只认旧式的话，**新式文件的译文一条都读不到** —— 表现是"翻译面板空的"，
但它不会报错。
"""

from __future__ import annotations

from storygraph.parsing.renpy_parser import parse_translation_files


def _write(root, lang, name, text):
    d = root / lang
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding='utf-8')


class TestModernTlDialogue:
    """新式：对话 id 后直接跟裸字符串。"""

    def test_bare_string_after_dialogue_id(self, tmp_path):
        _write(tmp_path, 'schinese', 'script.rpy',
               'translate schinese start_1a2b3c4d:\n'
               '    "你好啊"\n')
        got = parse_translation_files(tmp_path)
        assert 'start_1a2b3c4d' in got, got
        assert got['start_1a2b3c4d']['translated'] == '你好啊', got

    def test_bare_string_with_speaker_prefix(self, tmp_path):
        """`e "你好啊"` —— 译文行也可能带说话人前缀。"""
        _write(tmp_path, 'schinese', 'script.rpy',
               'translate schinese start_deadbeef:\n'
               '    e "你好啊"\n')
        got = parse_translation_files(tmp_path)
        assert got.get('start_deadbeef', {}).get('translated') == '你好啊', got

    def test_language_is_recorded(self, tmp_path):
        _write(tmp_path, 'schinese', 'script.rpy',
               'translate schinese start_1a2b3c4d:\n'
               '    "你好啊"\n')
        assert parse_translation_files(tmp_path)['start_1a2b3c4d'][
            'language'] == 'schinese'

    def test_multiple_ids_in_one_file(self, tmp_path):
        _write(tmp_path, 'schinese', 'script.rpy',
               'translate schinese start_aaaa:\n'
               '    "第一句"\n'
               '\n'
               'translate schinese start_bbbb:\n'
               '    "第二句"\n')
        got = parse_translation_files(tmp_path)
        assert got.get('start_aaaa', {}).get('translated') == '第一句', got
        assert got.get('start_bbbb', {}).get('translated') == '第二句', got

    def test_escaped_quotes_in_translation(self, tmp_path):
        _write(tmp_path, 'schinese', 'script.rpy',
               'translate schinese start_cccc:\n'
               '    "他说：\\"走吧\\""\n')
        got = parse_translation_files(tmp_path)
        assert got.get('start_cccc', {}).get('translated') == '他说："走吧"', got


class TestLegacyTlStillWorks:
    """旧式（`# old` / `new`）不能因为支持新式而坏掉。"""

    def test_old_new_pair(self, tmp_path):
        _write(tmp_path, 'schinese', 'script.rpy',
               'translate schinese start_1a2b3c4d:\n'
               '    # e "你好"\n'
               '    e "你好啊"\n')
        got = parse_translation_files(tmp_path)
        assert got.get('start_1a2b3c4d', {}).get('translated') == '你好啊', got

    def test_strings_block(self, tmp_path):
        _write(tmp_path, 'schinese', 'string.rpy',
               'translate schinese strings:\n'
               '    old "Start"\n'
               '    new "开始"\n')
        got = parse_translation_files(tmp_path)
        keys = [k for k in got if k.startswith('schinese.strings_')]
        assert keys, got
        assert got[keys[0]]['translated'] == '开始', got

    def test_python_block_is_not_a_unit(self, tmp_path):
        _write(tmp_path, 'schinese', 'p.rpy',
               'translate schinese python:\n'
               '    old "a"\n'
               '    new "b"\n')
        got = parse_translation_files(tmp_path)
        assert not [k for k in got if k.startswith('schinese.python')], got


class TestMixedAndEdgeCases:
    def test_both_forms_in_one_file(self, tmp_path):
        _write(tmp_path, 'schinese', 'script.rpy',
               'translate schinese start_aaaa:\n'
               '    "新式译文"\n'
               '\n'
               'translate schinese start_bbbb:\n'
               '    # old "原文"\n'
               '    "旧式译文"\n')
        got = parse_translation_files(tmp_path)
        assert got.get('start_aaaa', {}).get('translated') == '新式译文', got
        assert got.get('start_bbbb', {}).get('translated') == '旧式译文', got

    def test_missing_dir_is_empty_not_error(self, tmp_path):
        assert parse_translation_files(tmp_path / 'nope') == {}

    def test_nested_menu_id(self, tmp_path):
        """`translate <lang> <id>_<hash>` 里的 id 可能带下划线。"""
        _write(tmp_path, 'schinese', 'script.rpy',
               'translate schinese menu_choice_abc123:\n'
               '    "选项译文"\n')
        got = parse_translation_files(tmp_path)
        assert got.get('menu_choice_abc123', {}).get('translated') == \
            '选项译文', got
