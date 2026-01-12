"""
SongbirdUDP Implementation

UDP communication layer for the Songbird protocol.
Supports unicast, multicast, and broadcast modes.
"""

import socket
import threading
import logging
import struct
from typing import Optional, Tuple

from .istream import IStream
from .songbird_core import SongbirdCore, ProcessMode


class SongbirdUDP(IStream):
    """UDP implementation of the Songbird protocol."""

    ASYNC_READ_BUF = 2048

    def __init__(self, name: str):
        """
        Initialize UDP stream.
        
        Args:
            name: Name identifier for this instance
        """
        self.name = name
        self.socket: Optional[socket.socket] = None
        self.protocol = SongbirdCore(name, ProcessMode.PACKET)
        self.protocol.attach_stream(self)
        self.protocol.set_missing_packet_timeout(100)
        self.protocol.set_retransmission_timeout(100)
        
        self.default_remote_ip = ""
        self.default_remote_port = 0
        self.local_port = 0
        
        self.broadcast_mode = False
        self.multicast_mode = False
        self.bind_mode = False
        
        self.async_active = False
        self.read_thread: Optional[threading.Thread] = None
        self.begun = False

    def __del__(self):
        """Cleanup on deletion."""
        self.close()

    def listen(self, listen_port: int) -> bool:
        """
        Start listening on a UDP port.
        
        Args:
            listen_port: Port to listen on (0 for any available port)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if not self._prepare_socket(reuse_address=False):
                return False
            
            if listen_port != 0:
                self.socket.bind(('', listen_port))
            
            self.local_port = listen_port
            
            self.async_active = True
            self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
            self.read_thread.start()
            
            return True
        except OSError as e:
            logging.error(f"UDP listen error: {e}")
            return False

    def listen_multicast(self, addr: str, port: int) -> bool:
        """
        Listen to a multicast group.
        
        Args:
            addr: Multicast group address (e.g., '239.1.1.1')
            port: Multicast port
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if not self._prepare_socket(reuse_address=True):
                return False
            
            # Bind to the multicast port on any address
            self.socket.bind(('', port))
            
            # Join the multicast group
            mreq = struct.pack('4sL', socket.inet_aton(addr), socket.INADDR_ANY)
            self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            
            # Disable multicast loopback
            self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
            
            self.local_port = self.socket.getsockname()[1]
            self.multicast_mode = True
            
            self.async_active = True
            self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
            self.read_thread.start()
            
            return True
        except OSError as e:
            logging.error(f"UDP multicast listen error: {e}")
            return False

    def set_remote(self, addr: str, port: int, bind: bool = False) -> None:
        """
        Set the default remote endpoint.
        
        Args:
            addr: Remote IP address
            port: Remote port
            bind: If True, connect the socket to this endpoint
        """
        self.default_remote_ip = addr
        self.default_remote_port = port
        self.broadcast_mode = False
        self.bind_mode = bind
        
        if bind and self.socket:
            try:
                self.socket.connect((addr, port))
            except OSError as e:
                logging.error(f"Failed to connect socket in set_remote: {e}")

    def set_broadcast_mode(self, mode: bool) -> None:
        """
        Enable or disable broadcast mode.
        
        Args:
            mode: True to enable broadcast, False to disable
        """
        self.broadcast_mode = mode
        
        if self.socket and mode:
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError as e:
                logging.error(f"Failed to set broadcast mode: {e}")

    def get_remote_ip(self) -> str:
        """Get the default remote IP address."""
        return self.default_remote_ip

    def get_remote_port(self) -> int:
        """Get the default remote port."""
        return self.default_remote_port

    def get_local_port(self) -> int:
        """Get the local port."""
        return self.local_port

    def get_protocol(self) -> SongbirdCore:
        """
        Get the protocol handler.
        
        Returns:
            SongbirdCore instance
        """
        return self.protocol

    def is_broadcast(self) -> bool:
        """Check if in broadcast mode."""
        return self.broadcast_mode

    def is_multicast(self) -> bool:
        """Check if in multicast mode."""
        return self.multicast_mode

    def is_bound(self) -> bool:
        """Check if socket is connected to a remote."""
        return self.bind_mode

    def is_open(self) -> bool:
        """
        Check if the socket is open.
        
        Returns:
            True if open, False otherwise
        """
        return self.socket is not None

    def close_socket(self) -> None:
        """Close the socket."""
        self.async_active = False
        if self.socket:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None

    def close(self) -> None:
        """Close the UDP stream and stop read thread."""
        self.async_active = False
        
        if self.read_thread and self.read_thread.is_alive():
            self.read_thread.join(timeout=2.0)
        
        self.close_socket()

    def write(self, buffer: bytes) -> None:
        """
        Write data to the default remote endpoint.
        
        Args:
            buffer: Bytes to write
        """
        if not self.is_open():
            return
        
        try:
            if not self.broadcast_mode:
                if self.bind_mode:
                    self.socket.send(buffer)
                else:
                    self.socket.sendto(buffer, (self.default_remote_ip, self.default_remote_port))
            else:
                # Broadcast to 255.255.255.255
                self.socket.sendto(buffer, ('<broadcast>', self.default_remote_port))
        except OSError as e:
            logging.error(f"UDP send error: {e}")

    def supports_remote_write(self) -> bool:
        """
        Check if this stream supports writing to specific remotes.
        
        Returns:
            True (UDP supports remote write)
        """
        return True

    def write_to_remote(self, buffer: bytes, ip: str, port: int) -> None:
        """
        Write to a specific remote endpoint.
        
        Args:
            buffer: Bytes to write
            ip: Remote IP address
            port: Remote port
        """
        if not self.is_open():
            return
        
        try:
            self.socket.sendto(buffer, (ip, port))
        except OSError as e:
            logging.error(f"UDP send error: {e}")

    def get_default_remote(self) -> Optional[Tuple[str, int]]:
        """
        Get the default remote endpoint.
        
        Returns:
            Tuple of (ip, port) if set, None otherwise
        """
        if self.default_remote_port != 0:
            return (self.default_remote_ip, self.default_remote_port)
        return None

    def _prepare_socket(self, reuse_address: bool = False) -> bool:
        """
        Prepare socket for use.
        
        Args:
            reuse_address: Whether to set SO_REUSEADDR option
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Start read thread if not begun
            if not self.begun:
                self.begun = True
            
            # Close existing socket
            self.close_socket()
            
            # Create new socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.settimeout(0.1)  # Non-blocking with small timeout
            
            if reuse_address:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            return True
        except OSError as e:
            logging.error(f"prepareSocket error: {e}")
            return False

    def _read_loop(self) -> None:
        """Background thread for reading UDP data."""
        while self.async_active and self.socket:
            try:
                data, addr = self.socket.recvfrom(self.ASYNC_READ_BUF)
                if data:
                    remote_ip, remote_port = addr
                    self.protocol.parse_data(data, remote_ip, remote_port)
            except socket.timeout:
                # Timeout is expected for non-blocking reads
                continue
            except OSError as e:
                if self.async_active:
                    logging.error(f"UDP receive error: {e}")
                break
            except Exception as e:
                logging.error(f"Unexpected error in read loop: {e}")
                break
