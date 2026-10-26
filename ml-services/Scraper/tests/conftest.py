import sys
from pathlib import Path

# lets `pytest` find the packages in src/ even before the project is installed
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
