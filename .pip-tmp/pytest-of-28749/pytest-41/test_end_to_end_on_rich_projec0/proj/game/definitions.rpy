default jc_name = "「蒋忱」"
define narrator = Character(ctc="ctc")
define jc = DynamicCharacter('jc_name', image='jc',
    what_prefix='「', what_suffix='」')
image black = "#000000"
image bg bg1 = "images/bg/bg1.png"

layeredimage ye:
    image_format "images/characters/ye/{image}.png"
    group expressions:
        attribute happy:
            'happy'
        attribute sad:
            'sad'

# 靠左站
transform leftish:
    xpos 0.2
    block:
        linear 0.1 xoffset 4
