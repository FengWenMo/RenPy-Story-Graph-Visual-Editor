define e = Character("Eileen", color="#c8ffc8", image="eileen")
define m = Character("Mary")
define silent = Character("Nobody")

label start:
    e "First line"
    e "Second line"
    m "Mary line"
    narrator_var "Ghost line"
    jump ending

label ending:
    e "Last line"
    return
