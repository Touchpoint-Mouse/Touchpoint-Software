"""
Songbird Protocol Package

A Python implementation of the Songbird communication protocol supporting
both UDP and UART transport layers with reliable and unreliable delivery modes.
"""

from .istream import IStream
from .songbird_core import SongbirdCore, Packet, ProcessMode, ReliableMode
from .songbird_uart import SongbirdUART
from .songbird_udp import SongbirdUDP

__version__ = "1.0.0"
__all__ = [
    "IStream",
    "SongbirdCore",
    "Packet",
    "ProcessMode",
    "ReliableMode",
    "SongbirdUART",
    "SongbirdUDP",
]
