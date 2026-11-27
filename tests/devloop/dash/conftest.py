"""Put the shared dashboard fixtures on the path for this subpackage."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dash_fixture import dash, srv  # noqa: F401
