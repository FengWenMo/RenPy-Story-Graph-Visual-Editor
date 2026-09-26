# main.py
import tkinter as tk
from models import Graph
from graph_view import GraphView

if __name__ == "__main__":
    root = tk.Tk()
    graph = Graph()
    app = GraphView(root, graph)
    root.mainloop()