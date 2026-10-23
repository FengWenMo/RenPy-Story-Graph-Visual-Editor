# 它测什么：ATL transform 块体 + 带参 transform + with 语句 —— 块体必须逐字回去
# （ATL 是 Ren'Py 特有语言，工具不解释它，只搬运）。
# 来源：结构取自真机（transform / block+repeat / show ... at / with），台词全部替换。
transform leftish:
    xpos 0.2
    xanchor 0.0

transform shake_hard(t=0.05, n=6):
    block:
        linear t xoffset 8
        linear t xoffset -8
    repeat n

label ch_atl:
    scene bg rooftop
    show mary smile at leftish
    with dissolve
    m "台词A。"
    show mary smile at shake_hard(0.05, 3)
    m "台词B。"
    return
