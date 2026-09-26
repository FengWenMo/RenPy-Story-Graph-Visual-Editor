"""
tests/test_core.py
==================
core.statement 与 core.uid 的单元测试。

关注点：
  · content_hash 的计算格式与跨模块契约（必须与 TranslationUnitGenerator 一致）
  · Statement 相等 / 哈希语义（source 不参与，payload/condition 参与）
  · from_dict / to_dict 往返（含未知 kind 降级）
  · validate_statement 的必填与空值判定（speaker 允许为空）
  · uid 拼接规则：非 \\w 替换、空 label、空 hash
"""

from core.statement import Statement, StatementKind, validate_statement
from core.uid import sanitize_label, make_translation_uid


# ------------------------------------------------------------------
#  _compute_content_hash 通过 make_* 间接暴露
# ------------------------------------------------------------------

class TestContentHash:
    def test_say_hash_inputs_are_speaker_and_text(self):
        """哈希输入必须是 'say\\0{speaker}\\0{text}'。"""
        import hashlib
        expected = hashlib.md5(
            'say\0e\0Hello'.encode('utf-8')
        ).hexdigest()[:8]
        assert Statement.make_say('e', 'Hello').content_hash == expected

    def test_menu_option_uses_menu_prefix(self):
        """menu_option 的 prefix 是 'menu'（历史兼容，不是笔误）。"""
        import hashlib
        expected = hashlib.md5('menu\0Yes'.encode('utf-8')).hexdigest()[:8]
        assert Statement.make_menu_option('Yes').content_hash == expected

    def test_menu_caption_hash(self):
        import hashlib
        expected = hashlib.md5('menu\0Choose'.encode('utf-8')).hexdigest()[:8]
        assert Statement.make_menu('Choose').content_hash == expected

    def test_non_hashable_kinds_return_empty(self):
        for factory, args in (
            (Statement.make_show, ('bg room',)),
            (Statement.make_scene, ('bg room',)),
            (Statement.make_jump, ('ending',)),
            (Statement.make_call, ('ending',)),
            (Statement.make_return, ()),
            (Statement.make_python, ('x = 1',)),
        ):
            assert factory(*args).content_hash == '', factory

    def test_say_hash_differs_when_speaker_differs(self):
        a = Statement.make_say('e', 'Hello')
        b = Statement.make_say('m', 'Hello')
        assert a.content_hash != b.content_hash

    def test_say_hash_is_stable_across_source(self):
        a = Statement.make_say('e', 'Hello', source=('a.rpy', 1))
        b = Statement.make_say('e', 'Hello', source=('b.rpy', 99))
        assert a.content_hash == b.content_hash

    def test_extend_hash(self):
        import hashlib
        expected = hashlib.md5('extend\0more'.encode('utf-8')).hexdigest()[:8]
        assert Statement.make_extend('more').content_hash == expected


# ------------------------------------------------------------------
#  相等 / 哈希
# ------------------------------------------------------------------

class TestEquality:
    def test_same_content_is_equal(self):
        a = Statement.make_say('e', 'Hello')
        b = Statement.make_say('e', 'Hello')
        assert a == b
        assert hash(a) == hash(b)

    def test_source_not_part_of_equality(self):
        a = Statement.make_say('e', 'Hello', source=('a.rpy', 1))
        b = Statement.make_say('e', 'Hello', source=('b.rpy', 2))
        assert a == b

    def test_condition_part_of_equality(self):
        a = Statement.make_say('e', 'Hello', condition='x > 1')
        b = Statement.make_say('e', 'Hello')
        assert a != b

    def test_different_kind_not_equal(self):
        assert Statement.make_menu_option('Hi') != Statement.make_extend('Hi')

    def test_non_hashable_kinds_still_hashable(self):
        """content_hash 为空时用 sorted payload 兜底。"""
        s = Statement.make_show('bg room')
        assert isinstance(hash(s), int)

    def test_non_hashable_kinds_differ_by_payload(self):
        a = Statement.make_show('bg room')
        b = Statement.make_show('bg hall')
        assert a != b
        assert hash(a) != hash(b)

    def test_hash_stable_within_process(self):
        s = Statement.make_say('e', 'Hello')
        assert hash(s) == hash(Statement.make_say('e', 'Hello'))

    def test_not_implemented_for_foreign_type(self):
        assert Statement.make_say('e', 'Hi').__eq__('string') is NotImplemented


# ------------------------------------------------------------------
#  序列化
# ------------------------------------------------------------------

class TestSerialization:
    def test_roundtrip(self):
        s = Statement.make_say('e', 'Hello', condition='x>1',
                               source=('s.rpy', 3))
        back = Statement.from_dict(s.to_dict())
        assert back == s
        assert back.source == ('s.rpy', 3)
        assert back.content_hash == s.content_hash

    def test_roundtrip_show(self):
        s = Statement.make_show('bg room', at=['left'], as_='r',
                                with_='dissolve', zorder=3,
                                onlayer='master', behind=['e'])
        back = Statement.from_dict(s.to_dict())
        assert back == s
        assert back.payload['at'] == ['left']
        assert back.payload['zorder'] == 3

    def test_unknown_kind_downgraded(self):
        obj = {'kind': 'wat', 'payload': {'foo': 1}}
        s = Statement.from_dict(obj)
        assert s.kind == StatementKind.UNKNOWN
        assert s.payload == {'foo': 1}

    def test_missing_fields_defaulted(self):
        s = Statement.from_dict({})
        assert s.kind == StatementKind.UNKNOWN
        assert s.payload == {}
        assert s.condition == ''
        assert s.source == ('', 0)

    def test_source_normalized_to_tuple(self):
        s = Statement.from_dict({'kind': 'say', 'source': ['a.rpy', 7]})
        assert s.source == ('a.rpy', 7)


# ------------------------------------------------------------------
#  校验
# ------------------------------------------------------------------

class TestValidation:
    def test_valid_statements_have_no_issues(self):
        for s in (Statement.make_say('e', 'Hello'),
                  Statement.make_extend('cont'),
                  Statement.make_show('bg room'),
                  Statement.make_menu('Choose'),
                  Statement.make_menu_option('Yes'),
                  Statement.make_jump('ending'),
                  Statement.make_call('ending'),
                  Statement.make_return(),
                  Statement.make_python('x = 1')):
            assert validate_statement(s) == [], s

    def test_narration_allows_empty_speaker(self):
        assert validate_statement(Statement.make_say('', 'Narration')) == []

    def test_say_with_empty_text_flagged(self):
        issues = validate_statement(Statement.make_say('e', ''))
        assert issues and 'text' in issues[0]

    def test_missing_payload_field_flagged(self):
        s = Statement.make_say('e', 'Hello')
        del s.payload['text']
        issues = validate_statement(s)
        assert any('缺少字段' in i for i in issues)

    def test_unknown_kind_flagged(self):
        s = Statement.make_python('x')
        s.kind = 'nonsense'
        issues = validate_statement(s)
        assert any('未知 kind' in i for i in issues)


# ------------------------------------------------------------------
#  uid
# ------------------------------------------------------------------

class TestUid:
    def test_sanitize_replaces_non_word(self):
        assert sanitize_label('start.ch1') == 'start_ch1'
        assert sanitize_label('a-b c') == 'a_b_c'

    def test_sanitize_strips_surrounding_underscores(self):
        assert sanitize_label('_foo_') == 'foo'

    def test_sanitize_empty_becomes_unnamed(self):
        assert sanitize_label('') == 'unnamed'
        assert sanitize_label('@@@') == 'unnamed'

    def test_sanitize_keeps_unicode(self):
        """\\w 是 Unicode-aware，中文 label 原样保留。"""
        assert sanitize_label('第一章') == '第一章'

    def test_make_translation_uid(self):
        assert make_translation_uid('start', 'a1b2c3d4') == 'start_a1b2c3d4'

    def test_make_translation_uid_empty_hash(self):
        assert make_translation_uid('start', '') == ''

    def test_make_translation_uid_sanitizes_label(self):
        assert make_translation_uid('a.b', 'deadbeef') == 'a_b_deadbeef'
