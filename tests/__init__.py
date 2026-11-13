import os
import sys
from pathlib import Path

os.environ["LANGUAGE"] = "C"
os.environ.pop("CUSTICON_LOCALEDIR", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
