"""
tests/test_character_linker.py
==============================
CharacterScriptLinker 测试。

构造方式：真跑一遍 renpy_parser，拿到 ParseResult（比手写假数据更接近真实）。
"""

import pytest

from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder, TranslationUnitGenerator
from storygraph.parsing.character_script_linker import CharacterScriptLinker


SCRIPT = (
    'define e = Character("Eileen", color="#c8ffc8", image="eileen")\n'
    'define m = Character("Mary")\n'
    'define silent = Character("Nobody")\n'
    '\n'
    'label start:\n'
    '    e "First line"\n'
    '    e "Second line"\n'
    '    m "Mary line"\n'
    '    narrator_var "Ghost line"\n'
    '    jump ending\n'
    '\n'
    'label ending:\n'
    '    e "Last line"\n'
    '    return\n'
)


@pytest.fixture
def linker(write_rpy):
    fp = write_rpy(SCRIPT)
    result = ParseResultBuilder().build(RenPyParser().parse(fp))
    TranslationUnitGenerator().generate(result)
    lnk = CharacterScriptLinker(result)
    lnk.link()
    return lnk


class TestLinking:
    def test_dialogues_indexed_by_variable(self, linker):
        assert len(linker.character_dialogues['e']) == 3
        assert len(linker.character_dialogues['m']) == 1

    def test_labels_indexed(self, linker):
        assert linker.labels_of_character('e') == ['ending', 'start']
        assert linker.labels_of_character('m') == ['start']

    def test_reference_locations(self, linker):
        refs = linker.references_of_character('e')
        assert len(refs) == 3
        assert all(':' in r for r in refs)

    def test_dialogues_of_character_returns_objects(self, linker):
        dialogues = linker.dialogues_of_character('e')
        assert dialogues[0].text == 'First line'

    def test_unknown_character_returns_empty(self, linker):
        assert linker.labels_of_character('zzz') == []
        assert linker.dialogues_of_character('zzz') == []


class TestDiagnostics:
    def test_undefined_characters_detected(self, linker):
        undefined = linker.find_undefined_characters()
        names = [u['variable'] for u in undefined]
        assert 'narrator_var' in names

    def test_undefined_characters_carry_locations(self, linker):
        entry = next(u for u in linker.find_undefined_characters()
                     if u['variable'] == 'narrator_var')
        assert entry['dialogue_count'] == 1
        assert entry['locations']

    def test_undefined_characters_sorted_desc(self, linker):
        undefined = linker.find_undefined_characters()
        counts = [u['dialogue_count'] for u in undefined]
        assert counts == sorted(counts, reverse=True)

    def test_unused_characters_detected(self, linker):
        unused = [u['variable'] for u in linker.find_unused_characters()]
        assert 'silent' in unused
        assert 'e' not in unused

    def test_unused_characters_carry_source(self, linker):
        entry = next(u for u in linker.find_unused_characters()
                     if u['variable'] == 'silent')
        assert entry['source_file']
        assert entry['source_line'] > 0


class TestTranslationStatus:
    def test_no_translations_yields_zero(self, linker):
        status = linker.translation_status()
        assert status['total_units'] == 5
        assert status['translated_units'] == 0
        assert status['completion'] == 0.0

    def test_partial_translation_counted(self, linker):
        units = linker.parse_result.translation_units
        translations = {units[0].identifier: {
            'original': 'x', 'translated': '译', 'language': 'en'}}
        lnk = CharacterScriptLinker(linker.parse_result, translations)
        lnk.link()
        status = lnk.translation_status()
        assert status['translated_units'] == 1
        assert status['per_language']['en']['total'] == 1
        assert abs(status['completion'] - 1 / 5) < 1e-9

    def test_empty_translation_not_counted(self, linker):
        units = linker.parse_result.translation_units
        translations = {units[0].identifier: {
            'original': 'x', 'translated': '', 'language': 'en'}}
        lnk = CharacterScriptLinker(linker.parse_result, translations)
        lnk.link()
        status = lnk.translation_status()
        assert status['per_language']['en']['translated'] == 0

    def test_no_units_yields_zero_completion(self, write_rpy):
        fp = write_rpy('define e = Character("E")\n')
        result = ParseResultBuilder().build(RenPyParser().parse(fp))
        lnk = CharacterScriptLinker(result)
        lnk.link()
        status = lnk.translation_status()
        assert status['total_units'] == 0
        assert status['completion'] == 0.0


class TestExportRows:
    def test_rows_exported(self, linker):
        rows = linker.export_to_character_rows()
        ids = {r['ID'] for r in rows}
        assert ids == {'e', 'm', 'silent'}

    def test_row_fields(self, linker):
        rows = {r['ID']: r for r in linker.export_to_character_rows()}
        assert rows['e']['Name'] == 'Eileen'
        assert rows['e']['Color'] == '#c8ffc8'
        assert rows['e']['ImageTag'] == 'eileen'

    def test_rows_sorted_by_dialogue_count(self, linker):
        rows = linker.export_to_character_rows()
        counts = [r['_dialogue_count'] for r in rows]
        assert counts == sorted(counts, reverse=True)

    def test_internal_fields_prefixed(self, linker):
        rows = linker.export_to_character_rows()
        for r in rows:
            assert not {k for k in r if k.startswith('_')} - {
                '_dialogue_count', '_source_file', '_source_line',
                '_in_auto_block'}


class TestRankingAndSummary:
    def test_ranking_sorted(self, linker):
        ranking = linker.character_activity_ranking()
        assert ranking[0]['variable_name'] == 'e'
        assert ranking[0]['dialogue_count'] == 3
        assert ranking[0]['label_count'] == 2

    def test_summary_shape(self, linker):
        s = linker.summary()
        assert s['total_characters'] == 3
        assert s['used_characters'] == 3      # e / m / narrator_var
        assert s['undefined_characters'] == 1
        assert s['unused_characters'] == 1
        assert 'translation_status' in s


class TestDiffWithRegistry:
    class _FakeRegistry:
        def __init__(self, characters):
            self.characters = characters

    def test_missing_in_excel(self, linker):
        reg = self._FakeRegistry({'e': {'Notes': ''}})
        diff = linker.diff_with_registry(reg)
        assert {v for v, _ in diff['only_in_script']} == {'m', 'silent'}
        assert diff['both'] == [('e', 'script+excel')]

    def test_missing_in_script(self, linker):
        reg = self._FakeRegistry({'excel_only': {'Notes': 'note'}})
        diff = linker.diff_with_registry(reg)
        assert diff['only_in_excel'] == [('excel_only', 'note')]

    def test_none_registry(self, linker):
        diff = linker.diff_with_registry(None)
        assert set(dict(diff['only_in_script'])) == {'e', 'm', 'silent'}
        assert diff['only_in_excel'] == []
