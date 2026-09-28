"""
storygraph.excel —— Excel 底层设施（安全写单元格）。

只放"与业务无关"的 Excel 读写原语；表结构、列语义属于 story / assets 子包。
"""
from storygraph.excel.cells import append_text_row, set_row, set_text

__all__ = ['set_text', 'append_text_row', 'set_row']
