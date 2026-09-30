"""Perceiving and acting on a surface, independently of what the surface is."""

from .model import Bounds, Node, Observation, Surface, TableCell, WebHints
from .view import render

__all__ = [
    "Bounds",
    "Node",
    "Observation",
    "Surface",
    "TableCell",
    "WebHints",
    "render",
]
