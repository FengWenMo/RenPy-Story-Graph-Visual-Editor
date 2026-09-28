# 由剧情编辑器自动生成的控制流（请勿手动修改跳转部分）
default love = 0
default money = 0

label start_control:
    $ love = 1
    call start
    jump end_control

label end_control:
    $ money += 2
    call end
    return
