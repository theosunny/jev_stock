# -*- coding: utf-8 -*-
"""数据目录解析：$STOCK_DATA_DIR -> scripts/.data_dir 文件 -> 脚本所在目录"""
import os

def data_dir():
    d = os.environ.get("STOCK_DATA_DIR")
    if d:
        return d
    here = os.path.dirname(os.path.abspath(__file__))
    marker = os.path.join(here, ".data_dir")
    if os.path.exists(marker):
        v = open(marker, encoding="utf-8").read().strip()
        if v:
            return v
    return here
