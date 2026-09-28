# 由剧情编辑器自动生成的控制流（请勿手动修改跳转部分）
default x = 0

label a_control:
    call a
    if x > 1:
        jump b_control
    elif x <= 1:
        jump c_control

label b_control:
    call b
    return

label c_control:
    call c
    return
