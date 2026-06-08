#!/usr/bin/env python3
# @author FondaWu
import importlib.util
import sys
from pathlib import Path

# 使用绝对路径 import 避免与 common/submit_for_review.py 同名冲突
_common_path = Path(__file__).parent.parent.parent / "common" / "submit_for_review.py"
_spec = importlib.util.spec_from_file_location("common_submit_for_review", str(_common_path))
_common = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_common)


def submit_draft(draft_path: str) -> str:
    return _common.submit_draft("toutiao", draft_path)


if __name__ == "__main__":
    _common.main("toutiao")
