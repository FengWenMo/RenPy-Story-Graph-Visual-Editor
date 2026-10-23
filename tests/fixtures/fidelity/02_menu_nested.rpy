# 它测什么：嵌套 menu —— 选项体的归属只由缩进决定。补丁一旦把选项挂错分支，
# 产物字节上看不出问题，语义已经错了（竞争格局分析 §C2 说的就是这一类）。
# 来源：结构取自真机（menu / 选项体里再套 menu / jump），台词全部替换。
define m = Character("台词A", color="#ffc8c8", image="mary")

label ch_menu:
    m "台词B。"
    menu:
        "台词C":
            m "台词D。"
            jump ch_roof
        "台词E":
            menu:
                "台词F":
                    m "台词G。"
                "台词H":
                    m "台词I。"
    m "台词J。"
    return

label ch_roof:
    m "台词K。"
    return
