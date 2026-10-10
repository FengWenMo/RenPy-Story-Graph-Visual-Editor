    # ==== storygraph fork 模拟：引擎自带 weather6 ====
    try:
        if l.text.split()[:1] == ["weather6"]:
            l.advance()
            return renpy.ast.Pass(loc)
    except Exception:
        pass
