label act2_scene1:
    scene bg crossroad
    menu:
        "向左":
            $ chose_left = True
            jump act2_left
        "向右":
            jump act2_right

label act2_left:
    "你选择了左边。"
    return

label act2_right:
    "你选择了右边。"
    return
