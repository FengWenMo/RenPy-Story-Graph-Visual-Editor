# 由剧情编辑器自动生成的控制流（请勿手动修改跳转部分）
default love = 0

label start_control:
    $ love = 0
    call start
    menu:
        "选项A":
            jump route_a_control
        "选项B" if love > 1:
            jump route_b_control

label route_a_control:
    $ love += 1
    call route_a
    jump ending_control

label route_b_control:
    call route_b
    jump ending_control

label ending_control:
    call ending
    return
