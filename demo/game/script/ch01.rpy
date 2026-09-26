define e = Character("爱莲", color="#c8ffc8", image="eileen",
                      voice_tag="eileen", what_prefix="（", what_suffix="）")
define m = Character("玛丽", color="#ffc8c8", image="mary")

# 靠左站，双人对话时主角在左
transform leftish:
    xpos 0.2
    xanchor 0.0

# 挨打/受惊时用的剧烈抖动
transform shake_hard(t=0.05, n=6):
    "剧烈抖动，挨打/惊吓演出用"
    block:
        linear t xoffset 8
        linear t xoffset -8
    repeat n

# 柔和淡入，用于回忆切入
transform fade_in_soft:
    alpha 0.0
    linear 0.6 alpha 1.0

label ch01:
    scene bg classroom
    play music night fadein 1.0
    show eileen happy at leftish
    # 表情A6，距离近
    e "早上好，昨晚睡得好吗？"
    voice v_eileen_01
    play sound door
    m "还行，就是雨下了一整晚。"
    hide eileen
    show eileen sad at leftish
    with dissolve
    play music rain fadeout 2.0
    show mary smile at right
    m "别想了，今天我们还有正事。"
    stop music
    return
