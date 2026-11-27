"""Matinee's backend, one module per part; server.py starts it."""
# all of them, in order: each loads the next at its end (see core's), so any of them can be imported first
from . import core  # noqa: F401
