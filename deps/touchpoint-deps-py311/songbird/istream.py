"""
IStream Interface

Abstract base class defining the interface for communication streams.
"""

from abc import ABC, abstractmethod
from typing import Optional, Tuple


class IStream(ABC):
    """Abstract interface for communication streams."""

    @abstractmethod
    def write(self, buffer: bytes) -> None:
        """
        Write data to the stream.
        
        Args:
            buffer: Bytes to write to the stream
        """
        pass

    @abstractmethod
    def is_open(self) -> bool:
        """
        Check if the stream is open.
        
        Returns:
            True if the stream is open, False otherwise
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Close the stream."""
        pass

    def supports_remote_write(self) -> bool:
        """
        Check if this stream supports dynamic remote addressing.
        
        Returns:
            True if the stream supports writing to specific remotes
        """
        return False

    def write_to_remote(self, buffer: bytes, ip: str, port: int) -> None:
        """
        Write to a specific remote endpoint.
        
        Only supported if supports_remote_write() returns True.
        Default implementation ignores remote and uses normal write.
        
        Args:
            buffer: Bytes to write
            ip: Remote IP address
            port: Remote port number
        """
        self.write(buffer)

    def get_default_remote(self) -> Optional[Tuple[str, int]]:
        """
        Get the default remote for this stream.
        
        Returns:
            Tuple of (ip, port) if a default remote exists, None otherwise
        """
        return None
