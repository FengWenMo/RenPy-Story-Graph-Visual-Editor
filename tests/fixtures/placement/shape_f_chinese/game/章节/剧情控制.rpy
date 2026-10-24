# 中文名的控制流文件
label 第一章_控制:
    call 第一章 from _call_第一章
    jump 第二章_控制

label 第二章_控制:
    call 第二章 from _call_第二章
    return
