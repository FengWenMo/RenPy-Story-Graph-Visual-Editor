# 它测什么：菜单选项带 `if` 条件 —— Ren'Py 官方文档里的标准写法，也是真人最常写的一种。
# 2026-10-23 之前这里是**已知缺口**：补丁会报"1 处新增内容没能落进产物"，退回全量重生成。
# 现在逐字节相同（单拍渲染在菜单里会多包一层 `if`，补丁侧已按菜单结构摆平，见
# tests/test_patch_menu_condition.py）。这条同时钉住"别再退回去"。
# 来源：写法取自 Ren'Py 文档，台词全部替换。
define e = Character("台词A", image="eileen")

label ch_cond:
    e "台词B。"
    menu:
        "台词C":
            jump ch_roof
        "台词D" if persistent.flag:
            e "台词E。"
    return

label ch_roof:
    e "台词F。"
    return
