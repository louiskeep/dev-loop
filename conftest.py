import pathlib
import sys

# Put the repo root on sys.path so `from hooks import ...` resolves in tests.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
