"""
SongbirdUART Implementation

UART/Serial communication layer for the Songbird protocol.
Uses pyserial for serial port communication.
"""

import threading
import serial
import logging
import time
from typing import Optional

from .istream import IStream
from .songbird_core import SongbirdCore, ProcessMode


class SongbirdUART(IStream):
    """UART/Serial implementation of the Songbird protocol."""

    def __init__(self, name: str):
        """
        Initialize UART stream.
        
        Args:
            name: Name identifier for this instance
        """
        self.name = name
        self.serial_port: Optional[serial.Serial] = None
        self.protocol = SongbirdCore(name, ProcessMode.STREAM)
        self.protocol.attach_stream(self)
        self.protocol.set_missing_packet_timeout(10)
        
        self.async_active = False
        self.read_thread: Optional[threading.Thread] = None

    def __del__(self):
        """Cleanup on deletion."""
        self.close()

    def begin(self, port: str, baud_rate: int) -> bool:
        """
        Initialize and open the serial port.
        
        Args:
            port: Serial port name (e.g., 'COM3' on Windows, '/dev/ttyUSB0' on Linux)
            baud_rate: Baud rate for serial communication
            
        Returns:
            True if successful, False otherwise
        """
        # Clean up any existing connection first
        self.close()
        
        try:
            self.serial_port = serial.Serial(
                port=port,
                baudrate=baud_rate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,  # Non-blocking read with small timeout
                write_timeout=1.0
            )
            
            # Start async read thread
            self.async_active = True
            self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
            self.read_thread.start()
            
            return True
        except (serial.SerialException, OSError) as e:
            logging.error(f"Error opening serial port: {e}")
            return False

    def write(self, buffer: bytes) -> None:
        """
        Write data to the serial port.
        
        Args:
            buffer: Bytes to write
        """
        if not self.is_open():
            return
        
        try:
            self.serial_port.write(buffer)
        except serial.SerialException as e:
            logging.error(f"Error writing to serial port: {e}")
            # Mark connection as inactive so is_open() returns False
            self.async_active = False

    def close(self) -> None:
        """Close the serial port and stop read thread."""
        self.async_active = False
        
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except serial.SerialException:
                pass

    def is_open(self) -> bool:
        """
        Check if the serial port is open.
        
        Returns:
            True if open, False otherwise
        """
        return self.serial_port is not None and self.serial_port.is_open and self.async_active

    def get_protocol(self) -> SongbirdCore:
        """
        Get the protocol handler.
        
        Returns:
            SongbirdCore instance
        """
        return self.protocol

    def _read_loop(self) -> None:
        """Background thread for reading serial data."""
        while self.is_open():
            try:
                if self.serial_port.in_waiting > 0:
                    data = self.serial_port.read(self.serial_port.in_waiting)
                    if data:
                        self.protocol.parse_data(data)
                else:
                    # No data waiting, small sleep to avoid busy-waiting
                    time.sleep(0.01)
            except serial.SerialException as e:
                logging.error(f"Serial read error: {e}")
                self.async_active = False
                break
            except Exception as e:
                logging.error(f"Unexpected error in read loop: {e}")
                self.async_active = False
                break
