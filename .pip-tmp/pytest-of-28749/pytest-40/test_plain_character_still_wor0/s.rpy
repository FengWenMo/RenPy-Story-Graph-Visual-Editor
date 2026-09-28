default jc_name = "「蒋忱」"
default mm_name = "妈妈"
define narrator = Character(ctc="ctc", ctc_position="fixed")
define jc = DynamicCharacter('jc_name', image='jc',
    what_prefix='「', what_suffix='」', ctc="ctc")
define mm = DynamicCharacter("mm_name", what_prefix='"')
define en = Character("Eileen & Nat", what_prefix='"')
define d2 = Character("动态", dynamic=True)

label start:
    jc "你好。"
