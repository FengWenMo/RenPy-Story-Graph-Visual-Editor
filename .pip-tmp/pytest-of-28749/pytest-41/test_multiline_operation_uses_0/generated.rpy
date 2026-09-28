# 由剧情编辑器自动生成的控制流（请勿手动修改跳转部分）
default x = 0
default y = 0

label a_control:
    python:
        x = 1
        y = 2
    call a
    return
