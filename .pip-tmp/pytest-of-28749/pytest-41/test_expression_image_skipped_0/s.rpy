image black = "#000000"
image bg bg1_1 = "images/bg/bg1.1.png"

layeredimage ye:
    image_format "images/characters/ye/{image}.png"
    group expressions:
        attribute happy default:
            'happy'
        attribute hi:
            'hi'
    group poses when not base_handdown:
        attribute base_handdown:
            'bd'

label start:
    scene black
    show bg bg1_1
    show ye base_handdown happy
    show screen about(initial_selected=0)
    show expression Text("hi") as t
    return
