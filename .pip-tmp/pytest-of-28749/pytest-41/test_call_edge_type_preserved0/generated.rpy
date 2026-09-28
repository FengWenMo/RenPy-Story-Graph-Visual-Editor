# 由剧情编辑器自动生成的控制流（请勿手动修改跳转部分）

label a_control:
    call a
    call b_control

label b_control:
    call b
    return
