# 由剧情编辑器自动生成的控制流（请勿手动修改跳转部分）

label a_control:
    call a
    menu:
        "Yes":
            jump b_control
        "No":
            jump c_control

label b_control:
    call b
    return

label c_control:
    call c
    return
