# 控制流文件，但不叫 _control、也不在 chapter/ 下
label act1_flow:
    call act1_scene1
    jump act2_flow

label act2_flow:
    call act2_scene1
    return
