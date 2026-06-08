#!/usr/bin/env python3
# @author FondaWu
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
from check_and_publish import main
main("xiaohongshu")
