# 由剧情编辑器自动生成的控制流（请勿手动修改跳转部分）

label chapter1_1_control:
    call chapter1_1 from _call_chapter1_1
    jump chapter1_2_control

label chapter1_2_control:
    call chapter1_2 from _call_chapter1_2
    return
