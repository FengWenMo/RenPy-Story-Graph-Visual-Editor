# 由剧本表自动生成，请勿在此文件手工改动结构

# —— 自定义 transform（来自 transforms.xlsx）——
# 靠左站，双人对话时主角在左
transform leftish:
    xpos 0.2
    xanchor 0.0


label ch01:
    # 表情A6，距离近
    scene bg classroom
    show eileen happy at leftish
    play music night fadein 1.0
    e "早上好，昨晚睡得好吗？"
    play sound door
    voice v_eileen_01
    m "还行，就是雨下了一整晚。"
    hide eileen
    show eileen sad at leftish
    show mary smile at right
    with dissolve
    play music rain fadeout 2.0
    m "别想了，今天我们还有正事。"
    stop music
    return
