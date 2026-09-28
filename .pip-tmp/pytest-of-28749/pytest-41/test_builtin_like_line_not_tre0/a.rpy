define e = Character("Eileen")

# 靠左站，双人对话时主角在左
transform leftish:
    xpos 0.2
    xanchor 0.0

transform shake_hard(t=0.05, n=6):
    "剧烈抖动，挨打/惊吓用"
    block:
        linear t xoffset 8
        linear t xoffset -8
    repeat n

transform no_doc:
    alpha 0.0
    linear 0.6 alpha 1.0
