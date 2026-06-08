#!/usr/bin/env python3
# @author FondaWu
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
import submit_for_review as _common


def submit_draft(draft_path: str) -> str:
    return _common.submit_draft("zhihu", draft_path)


if __name__ == "__main__":
    _common.main("zhihu")
