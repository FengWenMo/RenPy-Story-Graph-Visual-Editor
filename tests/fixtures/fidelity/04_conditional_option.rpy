# 它测什么：菜单选项带 `if` 条件 —— Ren'Py 官方文档里的标准写法，也是真人最常写的一种。
# ⚠ 这是**已知缺口**：补丁会报"1 处新增内容没能落进产物"，退回全量重生成。
#   真机 55 个文件里恰好一条条件选项都没有，所以 55/55 全绿也盖不住这个洞。
#   这条用例如实钉住当前行为；修好之后它会红 —— 那时请改成"逐字节相同"，并删掉缺口条目。
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
