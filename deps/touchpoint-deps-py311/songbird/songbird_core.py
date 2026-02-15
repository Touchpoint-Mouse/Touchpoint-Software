"""
SongbirdCore Protocol Implementation

Core protocol handling for the Songbird communication system.
Supports both STREAM and PACKET modes with RELIABLE and UNRELIABLE delivery.
"""

import struct
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Dict, List, Tuple
from collections import deque
import logging
from cobs import cobs
from .istream import IStream


class ProcessMode(Enum):
    """Processing mode for the protocol."""
    STREAM = "stream"
    PACKET = "packet"


class ReliableMode(Enum):
    """Reliability mode for packet delivery."""
    UNRELIABLE = "unreliable"
    RELIABLE = "reliable"


@dataclass
class Remote:
    """Represents a remote endpoint."""
    ip: str = ""
    port: int = 0

    def __hash__(self):
        return hash((self.ip, self.port))

    def __eq__(self, other):
        if not isinstance(other, Remote):
            return False
        return self.ip == other.ip and self.port == other.port


@dataclass
class RemoteExpected:
    """Remote endpoint with expected sequence number."""
    remote: Remote
    seq_num: int

    def __hash__(self):
        return hash((self.remote, self.seq_num))

    def __eq__(self, other):
        if not isinstance(other, RemoteExpected):
            return False
        return self.remote == other.remote and self.seq_num == other.seq_num


@dataclass
class RemoteOrder:
    """Tracks expected sequence number and missing packet timer for a remote."""
    expected_seq_num: int = 0
    missing_timer_active: bool = False
    missing_timer_start: float = 0.0


@dataclass
class OutgoingInfo:
    """Tracks outgoing guaranteed packets."""
    packet: 'Packet' = None
    remote: Remote = field(default_factory=Remote)
    send_time: float = 0.0
    retransmit_count: int = 0


class Packet:
    """Represents a protocol packet."""

    def __init__(self, header: int, payload: bytes = b""):
        """
        Create a packet.
        
        Args:
            header: Packet header byte
            payload: Optional payload bytes
        """
        self.header = header
        self.sequence_num = 0
        self.guaranteed_flag = False
        self.payload = bytearray(payload)
        self.read_pos = 0
        self.remote_ip = ""
        self.remote_port = 0

    def to_bytes(self, mode: ProcessMode, reliable_mode: ReliableMode) -> bytes:
        """
        Convert packet to bytes for transmission.
        
        Args:
            mode: Processing mode (STREAM or PACKET)
            reliable_mode: Reliability mode (RELIABLE or UNRELIABLE)
            
        Returns:
            Packet as bytes (COBS encoded with 0x00 delimiter in STREAM mode)
        """
        out = bytearray()

        if reliable_mode == ReliableMode.RELIABLE:
            # RELIABLE mode: no seq/guaranteed bytes
            # STREAM: [header][payload] (COBS encoded)
            # PACKET: [header][payload]
            out.append(self.header)
        else:
            # UNRELIABLE mode: includes seq/guaranteed bytes
            # STREAM: [header][seq][guaranteed][payload] (COBS encoded)
            # PACKET: [header][seq][guaranteed][payload]
            out.append(self.header)
            out.append(self.sequence_num & 0xFF)
            out.append(1 if self.guaranteed_flag else 0)

        out.extend(self.payload)
        
        # Apply COBS encoding in STREAM mode
        if mode == ProcessMode.STREAM:
            encoded = cobs.encode(bytes(out))
            return encoded + b'\x00'  # Add delimiter
        else:
            return bytes(out)

    def set_sequence_num(self, seq_num: int) -> None:
        """Set the sequence number."""
        self.sequence_num = seq_num & 0xFF

    def set_guaranteed(self, guaranteed: bool = True) -> None:
        """Set the guaranteed delivery flag."""
        self.guaranteed_flag = guaranteed

    def is_guaranteed(self) -> bool:
        """Check if guaranteed delivery is enabled."""
        return self.guaranteed_flag

    def get_header(self) -> int:
        """Get the packet header."""
        return self.header

    def get_sequence_num(self) -> int:
        """Get the sequence number."""
        return self.sequence_num

    def get_payload(self) -> bytes:
        """Get the payload as bytes."""
        return bytes(self.payload)

    def get_payload_length(self) -> int:
        """Get the payload length."""
        return len(self.payload)

    def get_remaining_bytes(self) -> int:
        """Get the number of unread bytes in the payload."""
        return len(self.payload) - self.read_pos

    def set_remote(self, ip: str, port: int) -> None:
        """Set the remote endpoint."""
        self.remote_ip = ip
        self.remote_port = port

    def get_remote(self) -> Remote:
        """Get the remote endpoint."""
        return Remote(self.remote_ip, self.remote_port)

    def get_remote_ip(self) -> str:
        """Get the remote IP address."""
        return self.remote_ip

    def get_remote_port(self) -> int:
        """Get the remote port."""
        return self.remote_port

    # Writing functions
    def write_bytes(self, buffer: bytes) -> None:
        """Write bytes to the payload."""
        self.payload.extend(buffer)

    def write_byte(self, value: int) -> None:
        """Write a single byte to the payload."""
        self.payload.append(value & 0xFF)

    def write_int16(self, value: int) -> None:
        """Write a 16-bit integer to the payload (big-endian)."""
        self.payload.extend(struct.pack('>h', value))

    def write_float(self, value: float) -> None:
        """Write a 32-bit float to the payload (big-endian)."""
        self.payload.extend(struct.pack('>f', value))

    def write_string(self, value: str) -> None:
        """Write a string with length prefix (uint16_t length + UTF-8 bytes)."""
        encoded = value.encode('utf-8')
        length = len(encoded)
        # Write length as uint16_t (big-endian)
        self.payload.extend(struct.pack('>H', length))
        # Write string bytes
        self.payload.extend(encoded)

    def write_protobuf(self, data: bytes) -> None:
        """Write a length-prefixed byte array (for protobuf messages)."""
        length = len(data)
        # Write length as uint16_t (big-endian)
        self.payload.extend(struct.pack('>H', length))
        # Write protobuf bytes
        self.payload.extend(data)

    # Reading functions
    def read_byte(self) -> int:
        """Read a single byte from the payload."""
        if self.read_pos >= len(self.payload):
            return 0
        value = self.payload[self.read_pos]
        self.read_pos += 1
        return value

    def peek_byte(self) -> int:
        """Peek at the next byte without consuming it."""
        if self.read_pos >= len(self.payload):
            return 0
        return self.payload[self.read_pos]

    def read_bytes(self, length: int) -> bytes:
        """Read multiple bytes from the payload."""
        available = len(self.payload) - self.read_pos
        to_read = min(length, available)
        result = bytes(self.payload[self.read_pos:self.read_pos + to_read])
        self.read_pos += to_read
        # Pad with zeros if requested more than available
        if to_read < length:
            result += b'\x00' * (length - to_read)
        return result

    def read_float(self) -> float:
        """Read a 32-bit float from the payload (big-endian)."""
        data = self.read_bytes(4)
        return struct.unpack('>f', data)[0]

    def read_int16(self) -> int:
        """Read a 16-bit integer from the payload (big-endian)."""
        data = self.read_bytes(2)
        return struct.unpack('>h', data)[0]

    def read_string(self) -> str:
        """Read a length-prefixed string from the payload."""
        # Read length (uint16_t, big-endian)
        length_data = self.read_bytes(2)
        length = struct.unpack('>H', length_data)[0]
        
        # Read string bytes
        if length == 0:
            return ""
        
        string_data = self.read_bytes(length)
        return string_data.decode('utf-8', errors='replace')

    def read_protobuf(self) -> bytes:
        """Read a length-prefixed byte array (for protobuf messages)."""
        # Read length (uint16_t, big-endian)
        length_data = self.read_bytes(2)
        length = struct.unpack('>H', length_data)[0]
        
        # Read protobuf bytes
        if length == 0:
            return b""
        
        return self.read_bytes(length)


class SongbirdCore:
    """Core protocol handler for Songbird communication."""

    def __init__(self, name: str, mode: ProcessMode = ProcessMode.PACKET, 
                 reliable_mode: ReliableMode = ReliableMode.UNRELIABLE):
        """
        Initialize the protocol core.
        
        Args:
            name: Name identifier for this instance
            mode: Processing mode (STREAM or PACKET)
            reliable_mode: Reliability mode (RELIABLE or UNRELIABLE)
        """
        self.name = name
        self.process_mode = mode
        self.reliable_mode = reliable_mode
        self.stream: Optional[IStream] = None
        self.read_buffer = bytearray()
        
        # Packet mode specific
        self.incoming_packets: Dict[RemoteExpected, Packet] = {}
        self.next_seq_num = 0
        self.remote_orders: Dict[Remote, RemoteOrder] = {}
        self.outgoing_guaranteed: Dict[int, OutgoingInfo] = {}
        
        # Timeouts
        self.missing_packet_timeout_ms = 100
        self.retransmission_timeout_ms = 1000
        self.max_retransmit_attempts = 5
        self.last_data_time_ms = 0
        
        # Stream mode specific
        self.new_packet = True
        self.allow_out_of_order = True
        
        # Handlers
        self.read_handler: Optional[Callable[[Packet], None]] = None
        self.header_handlers: Dict[int, Callable[[Packet], None]] = {}
        self.remote_handlers: Dict[Remote, Callable[[Packet], None]] = {}
        
        # Wait maps
        self.header_map: Dict[int, Packet] = {}
        self.remote_map: Dict[Remote, Packet] = {}
        
        # Waiters
        self.header_waiters: Dict[int, List[threading.Event]] = {}
        self.remote_waiters: Dict[Remote, List[threading.Event]] = {}
        self.waiter_packets: Dict[threading.Event, Optional[Packet]] = {}
        
        # Thread safety
        self.data_lock = threading.RLock()
        self.wait_lock = threading.Lock()
        
        # Timer thread
        self.timer_stop = threading.Event()
        self.timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self.timer_thread.start()

    def __del__(self):
        """Cleanup on deletion."""
        self.timer_stop.set()
        if hasattr(self, 'timer_thread'):
            self.timer_thread.join(timeout=1.0)

    def _timer_loop(self):
        """Background thread for timeout monitoring."""
        while not self.timer_stop.wait(min(self.missing_packet_timeout_ms, 
                                           self.retransmission_timeout_ms) / 1000.0):
            now = time.time()
            expired_remotes = []
            retransmit_packets = []
            
            with self.data_lock:
                # Check missing packet timeouts
                for remote, order in list(self.remote_orders.items()):
                    if order.missing_timer_active and order.missing_timer_start > 0:
                        elapsed_ms = (now - order.missing_timer_start) * 1000
                        if elapsed_ms >= self.missing_packet_timeout_ms:
                            expired_remotes.append(remote)
                
                # Check retransmission timeouts
                for seq_num, info in list(self.outgoing_guaranteed.items()):
                    elapsed_ms = (now - info.send_time) * 1000
                    if elapsed_ms >= self.retransmission_timeout_ms:
                        retransmit_packets.append(seq_num)
            
            # Handle expired timeouts outside lock
            for remote in expired_remotes:
                self._on_missing_timeout(remote)
            
            for seq_num in retransmit_packets:
                self._on_retransmission_timeout(seq_num)

    def attach_stream(self, stream: IStream) -> None:
        """Attach a stream for communication."""
        self.stream = stream

    def set_read_handler(self, handler: Callable[[Packet], None]) -> None:
        """Set global read handler for all packets."""
        with self.data_lock:
            self.read_handler = handler

    def set_header_handler(self, header: int, handler: Callable[[Packet], None]) -> None:
        """Set handler for packets with specific header."""
        if header == 0x00:
            logging.error("Header 0x00 is reserved for ACKs and cannot be used")
            return
        with self.data_lock:
            self.header_handlers[header] = handler

    def clear_header_handler(self, header: int) -> None:
        """Clear handler for specific header."""
        with self.data_lock:
            self.header_handlers.pop(header, None)
            self.header_map.pop(header, None)

    def set_remote_handler(self, remote_ip: str, remote_port: int, 
                          handler: Callable[[Packet], None]) -> None:
        """Set handler for packets from specific remote."""
        with self.data_lock:
            remote = Remote(remote_ip, remote_port)
            self.remote_handlers[remote] = handler

    def clear_remote_handler(self, remote_ip: str, remote_port: int) -> None:
        """Clear handler for specific remote."""
        with self.data_lock:
            remote = Remote(remote_ip, remote_port)
            self.remote_handlers.pop(remote, None)
            self.remote_map.pop(remote, None)

    def wait_for_header(self, header: int, timeout_ms: int = 1000) -> Optional[Packet]:
        """
        Wait for a packet with specific header.
        
        Args:
            header: Header to wait for
            timeout_ms: Timeout in milliseconds
            
        Returns:
            Packet if received, None on timeout
        """
        # Check if already available
        with self.data_lock:
            if header in self.header_map:
                pkt = self.header_map.pop(header)
                return pkt
        
        # Register waiter
        event = threading.Event()
        with self.wait_lock:
            if header not in self.header_waiters:
                self.header_waiters[header] = []
            self.header_waiters[header].append(event)
            self.waiter_packets[event] = None
        
        # Wait for signal
        got = event.wait(timeout_ms / 1000.0)
        
        # Unregister waiter
        with self.wait_lock:
            if header in self.header_waiters:
                self.header_waiters[header].remove(event)
                if not self.header_waiters[header]:
                    del self.header_waiters[header]
            pkt = self.waiter_packets.pop(event, None)
        
        if not got:
            return None
        
        with self.data_lock:
            if header in self.header_map:
                return self.header_map.pop(header)
        return pkt

    def wait_for_remote(self, remote_ip: str, remote_port: int, 
                       timeout_ms: int = 1000) -> Optional[Packet]:
        """
        Wait for a packet from specific remote.
        
        Args:
            remote_ip: Remote IP address
            remote_port: Remote port
            timeout_ms: Timeout in milliseconds
            
        Returns:
            Packet if received, None on timeout
        """
        remote = Remote(remote_ip, remote_port)
        
        # Check if already available
        with self.data_lock:
            if remote in self.remote_map:
                pkt = self.remote_map.pop(remote)
                return pkt
        
        # Register waiter
        event = threading.Event()
        with self.wait_lock:
            if remote not in self.remote_waiters:
                self.remote_waiters[remote] = []
            self.remote_waiters[remote].append(event)
            self.waiter_packets[event] = None
        
        # Wait for signal
        got = event.wait(timeout_ms / 1000.0)
        
        # Unregister waiter
        with self.wait_lock:
            if remote in self.remote_waiters:
                self.remote_waiters[remote].remove(event)
                if not self.remote_waiters[remote]:
                    del self.remote_waiters[remote]
            pkt = self.waiter_packets.pop(event, None)
        
        if not got:
            return None
        
        with self.data_lock:
            if remote in self.remote_map:
                return self.remote_map.pop(remote)
        return pkt

    def create_packet(self, header: int) -> Packet:
        """Create a new packet with the given header."""
        if header == 0x00:
            logging.error("Header 0x00 is reserved for ACKs and cannot be used")
            return Packet(0x01)
        return Packet(header)

    def set_missing_packet_timeout(self, ms: int) -> None:
        """Set missing packet timeout in milliseconds."""
        with self.data_lock:
            self.missing_packet_timeout_ms = ms

    def set_retransmission_timeout(self, ms: int) -> None:
        """Set retransmission timeout in milliseconds."""
        with self.data_lock:
            self.retransmission_timeout_ms = ms

    def set_max_retransmit_attempts(self, attempts: int) -> None:
        """Set maximum retransmit attempts."""
        with self.data_lock:
            self.max_retransmit_attempts = attempts

    def set_allow_out_of_order(self, allow: bool) -> None:
        """Set whether to allow out-of-order packets."""
        self.allow_out_of_order = allow

    def send_packet(self, packet: Packet, guarantee_delivery: bool = False, 
                   seq_num: Optional[int] = None) -> None:
        """
        Send a packet.
        
        Args:
            packet: Packet to send
            guarantee_delivery: Whether to guarantee delivery
            seq_num: Optional specific sequence number
        """
        if not self.stream or not self.stream.is_open():
            logging.error("Stream not attached or not open, cannot send packet")
            return
        
        # Assign sequence number
        if seq_num is None:
            seq_num = self.next_seq_num
            self.next_seq_num = (self.next_seq_num + 1) & 0xFF
        
        packet.set_sequence_num(seq_num)
        
        if guarantee_delivery:
            packet.set_guaranteed(True)
        
        # Convert to bytes and send
        data = packet.to_bytes(self.process_mode, self.reliable_mode)
        remote = packet.get_remote()
        
        if self.stream.supports_remote_write() and remote.port != 0:
            self.stream.write_to_remote(data, remote.ip, remote.port)
        else:
            self.stream.write(data)
        
        # Track guaranteed packets
        if guarantee_delivery and self.reliable_mode == ReliableMode.UNRELIABLE:
            remote = packet.get_remote()
            # Get default remote if not set
            if self.stream.supports_remote_write() and remote.port == 0:
                default_remote = self.stream.get_default_remote()
                if default_remote:
                    remote = Remote(default_remote[0], default_remote[1])
                    packet.set_remote(remote.ip, remote.port)
            
            info = OutgoingInfo(
                packet=packet,
                remote=remote,
                send_time=time.time(),
                retransmit_count=0
            )
            
            with self.data_lock:
                self.outgoing_guaranteed[seq_num] = info

    def parse_data(self, data: bytes, remote_ip: str = "", remote_port: int = 0) -> None:
        """
        Parse incoming data.
        
        Args:
            data: Received data bytes
            remote_ip: Source IP address (for packet mode)
            remote_port: Source port (for packet mode)
        """
        if self.process_mode == ProcessMode.PACKET:
            pkt = self._packet_from_data(data)
            if not pkt:
                return
            
            pkt.set_remote(remote_ip, remote_port)
            
            # Check for ACK
            if self._check_for_ack(pkt):
                return
            
            dispatch = []
            if self.allow_out_of_order or self.reliable_mode == ReliableMode.RELIABLE:
                dispatch.append(pkt)
                if self.reliable_mode == ReliableMode.UNRELIABLE:
                    if (pkt.is_guaranteed()):
                        self._update_remote_order(pkt)
                    with self.data_lock:
                        for expected_pkt in list(self.incoming_packets.values()):
                            dispatch.append(expected_pkt)
                        self.incoming_packets.clear()
            elif self.reliable_mode == ReliableMode.UNRELIABLE:
                self._update_remote_order(pkt)
                with self.data_lock:
                    remote_expected = RemoteExpected(pkt.get_remote(), pkt.get_sequence_num())
                    self.incoming_packets[remote_expected] = pkt
                dispatch = self._reorder_packets()
            
            for p in dispatch:
                self._call_handlers(p)
                
        elif self.process_mode == ProcessMode.STREAM:
            # Accumulate data and look for 0x00 delimiters (COBS packets)
            self._append_to_read_buffer(data)
            
            while True:
                pkt = self._packet_from_stream_cobs()
                if not pkt:
                    current_time_ms = time.time() * 1000
                    if current_time_ms - self.last_data_time_ms > self.missing_packet_timeout_ms:
                        self.flush()
                    break
                
                self.last_data_time_ms = time.time() * 1000
                pkt.set_remote(remote_ip, remote_port)
                
                if self._check_for_ack(pkt):
                    continue
                
                if self.reliable_mode == ReliableMode.UNRELIABLE:
                    self._update_remote_order(pkt)
                
                self._call_handlers(pkt)

    def _packet_from_data(self, data: bytes) -> Optional[Packet]:
        """Parse packet from raw data (packet mode)."""
        if self.reliable_mode == ReliableMode.RELIABLE:
            # RELIABLE: [header][payload]
            if len(data) < 1:
                return None
            header = data[0]
            payload = data[1:] if len(data) > 1 else b""
            return Packet(header, payload)
        else:
            # UNRELIABLE: [header][seq][guaranteed][payload]
            if len(data) < 3:
                return None
            header = data[0]
            seq_num = data[1]
            guaranteed = data[2]
            payload = data[3:] if len(data) > 3 else b""
            
            pkt = Packet(header, payload)
            pkt.set_sequence_num(seq_num)
            if guaranteed:
                pkt.set_guaranteed()
            return pkt

    def _packet_from_stream(self) -> Optional[Packet]:
        """Parse packet from stream buffer."""
        with self.data_lock:
            if self.reliable_mode == ReliableMode.RELIABLE:
                # RELIABLE: [header][length][payload]
                if self.new_packet:
                    if len(self.read_buffer) < 2:
                        return None
                    self.new_packet = False
                
                payload_len = self.read_buffer[1]
                if len(self.read_buffer) < 2 + payload_len:
                    return None
                
                self.new_packet = True
                header = self.read_buffer[0]
                payload = bytes(self.read_buffer[2:2 + payload_len])
                pkt = Packet(header, payload)
                del self.read_buffer[:2 + payload_len]
                return pkt
            else:
                # UNRELIABLE: [header][length][seq][guaranteed][payload]
                if self.new_packet:
                    if len(self.read_buffer) < 4:
                        return None
                    self.new_packet = False
                
                payload_len = self.read_buffer[1]
                if len(self.read_buffer) < 4 + payload_len:
                    return None
                
                self.new_packet = True
                header = self.read_buffer[0]
                seq_num = self.read_buffer[2]
                guaranteed = self.read_buffer[3]
                payload = bytes(self.read_buffer[4:4 + payload_len])
                
                pkt = Packet(header, payload)
                pkt.set_sequence_num(seq_num)
                if guaranteed:
                    pkt.set_guaranteed()
                
                del self.read_buffer[:4 + payload_len]
                return pkt

    def _packet_from_stream_cobs(self) -> Optional[Packet]:
        """Parse COBS-encoded packet from stream buffer."""
        with self.data_lock:
            # Look for 0x00 delimiter
            try:
                delimiter_idx = self.read_buffer.index(0x00)
            except ValueError:
                # No complete packet yet
                return None
            
            # Extract and decode COBS packet
            if delimiter_idx == 0:
                # Empty packet, skip delimiter
                del self.read_buffer[0]
                return None
            
            cobs_data = bytes(self.read_buffer[:delimiter_idx])
            del self.read_buffer[:delimiter_idx + 1]  # Remove packet + delimiter
            
            try:
                decoded = cobs.decode(cobs_data)
            except cobs.DecodeError:
                logging.error("COBS decode error, skipping packet")
                return None
            
            if len(decoded) < 1:
                return None
            
            # Parse decoded packet
            if self.reliable_mode == ReliableMode.RELIABLE:
                # RELIABLE: [header][payload]
                header = decoded[0]
                payload = decoded[1:] if len(decoded) > 1 else b""
                return Packet(header, payload)
            else:
                # UNRELIABLE: [header][seq][guaranteed][payload]
                if len(decoded) < 3:
                    return None
                header = decoded[0]
                seq_num = decoded[1]
                guaranteed = decoded[2]
                payload = decoded[3:] if len(decoded) > 3 else b""
                
                pkt = Packet(header, payload)
                pkt.set_sequence_num(seq_num)
                if guaranteed:
                    pkt.set_guaranteed()
                return pkt

    def _call_handlers(self, pkt: Packet) -> None:
        """Call registered handlers for a packet."""
        header = pkt.get_header()
        remote = pkt.get_remote()
        
        # Get handlers under lock
        with self.data_lock:
            header_handler = self.header_handlers.get(header)
            self.header_map[header] = pkt
            
            remote_handler = self.remote_handlers.get(remote)
            self.remote_map[remote] = pkt
            
            global_handler = self.read_handler
        
        # Notify waiters
        with self.wait_lock:
            if header in self.header_waiters and self.header_waiters[header]:
                event = self.header_waiters[header][0]
                self.waiter_packets[event] = pkt
                event.set()
            
            if remote in self.remote_waiters and self.remote_waiters[remote]:
                event = self.remote_waiters[remote][0]
                self.waiter_packets[event] = pkt
                event.set()
        
        # Call handlers outside lock
        if header_handler:
            header_handler(pkt)
        if remote_handler:
            remote_handler(pkt)
        if global_handler:
            global_handler(pkt)

    def _reorder_packets(self) -> List[Packet]:
        """Reorder packets based on sequence numbers."""
        with self.data_lock:
            dispatch = []
            for remote, order in list(self.remote_orders.items()):
                dispatch.extend(self._reorder_remote(remote, order))
            return dispatch

    def _reorder_remote(self, remote: Remote, order: RemoteOrder) -> List[Packet]:
        """Reorder packets for a specific remote."""
        dispatch = []
        while True:
            key = RemoteExpected(remote, order.expected_seq_num)
            if key in self.incoming_packets:
                dispatch.append(self.incoming_packets.pop(key))
                order.expected_seq_num = (order.expected_seq_num + 1) & 0xFF
                if order.missing_timer_active:
                    order.missing_timer_active = False
                    order.missing_timer_start = 0.0
                continue
            
            if not order.missing_timer_active:
                order.missing_timer_active = True
                order.missing_timer_start = time.time()
            break
        
        return dispatch

    def _on_missing_timeout(self, remote: Remote) -> None:
        """Handle missing packet timeout."""
        dispatch = []
        with self.data_lock:
            if remote not in self.remote_orders:
                return
            
            order = self.remote_orders[remote]
            
            # Find nearest forward sequence number
            found = False
            best_dist = 256
            best_seq = 0
            
            for key in self.incoming_packets.keys():
                if key.remote != remote:
                    continue
                
                seq = key.seq_num
                # Calculate forward distance with wraparound
                dist = (seq - order.expected_seq_num) & 0xFF
                
                if not found or dist < best_dist:
                    found = True
                    best_dist = dist
                    best_seq = seq
            
            if found:
                order.expected_seq_num = best_seq
                order.missing_timer_active = False
                dispatch = self._reorder_remote(remote, order)
            else:
                # No packets for this remote, remove it
                del self.remote_orders[remote]
                self.remote_map.pop(remote, None)
        
        for p in dispatch:
            self._call_handlers(p)

    def _update_remote_order(self, pkt: Packet) -> None:
        """Update remote order tracking."""
        with self.data_lock:
            remote = pkt.get_remote()
            seq_num = pkt.get_sequence_num()
            
            if remote not in self.remote_orders:
                self.remote_orders[remote] = RemoteOrder()
                if not self.allow_out_of_order:
                    self.remote_orders[remote].expected_seq_num = seq_num
                else:
                    self.remote_orders[remote].expected_seq_num = (seq_num + 1) & 0xFF
            else:
                if self.allow_out_of_order:
                    self.remote_orders[remote].expected_seq_num = (seq_num + 1) & 0xFF
            
            if self.allow_out_of_order:
                self.remote_orders[remote].expected_seq_num = (seq_num + 1) & 0xFF
                self.remote_orders[remote].missing_timer_active = True
                self.remote_orders[remote].missing_timer_start = time.time()

    def _is_repeat_packet(self, pkt: Packet) -> bool:
        """Check if packet is a repeat."""
        if not pkt.is_guaranteed():
            return False
        
        seq_num = pkt.get_sequence_num()
        remote = pkt.get_remote()
        
        with self.data_lock:
            if remote in self.remote_orders:
                expected_seq = self.remote_orders[remote].expected_seq_num
                # Check if sequence is in the past (with wraparound)
                diff = (seq_num - expected_seq) & 0xFF
                if diff > 128:  # In the past
                    return True
        return False

    def _check_for_ack(self, pkt: Packet) -> bool:
        """Check and handle ACK packets."""
        if self.reliable_mode != ReliableMode.UNRELIABLE:
            return False
        
        # Check if this is an ACK packet
        if pkt.get_header() == 0x00:
            ack_seq = pkt.get_sequence_num()
            self._remove_acknowledged_packet(ack_seq)
            return True
        
        # Send ACK if guaranteed
        if pkt.is_guaranteed():
            seq_num = pkt.get_sequence_num()
            remote_ip = pkt.get_remote_ip()
            remote_port = pkt.get_remote_port()
            
            ack_pkt = Packet(0x00)
            ack_pkt.set_remote(remote_ip, remote_port)
            self.send_packet(ack_pkt, guarantee_delivery=False, seq_num=seq_num)
        
        return self._is_repeat_packet(pkt)

    def _remove_acknowledged_packet(self, seq_num: int) -> None:
        """Remove acknowledged packet from retransmit queue."""
        with self.data_lock:
            self.outgoing_guaranteed.pop(seq_num, None)

    def _on_retransmission_timeout(self, seq_num: int) -> None:
        """Handle retransmission timeout."""
        need_resend = False
        info = None
        
        with self.data_lock:
            if seq_num in self.outgoing_guaranteed:
                info = self.outgoing_guaranteed[seq_num]
                
                if self.max_retransmit_attempts > 0 and info.retransmit_count >= self.max_retransmit_attempts:
                    del self.outgoing_guaranteed[seq_num]
                else:
                    need_resend = True
                    info.retransmit_count += 1
                    info.send_time = time.time()
        
        if need_resend and info:
            self.send_packet(info.packet, guarantee_delivery=False, seq_num=info.packet.get_sequence_num())

    def flush(self) -> None:
        """Flush all buffers."""
        with self.data_lock:
            self.read_buffer.clear()
            self.incoming_packets.clear()
            self.header_map.clear()
            self.new_packet = True

    def get_read_buffer_size(self) -> int:
        """Get read buffer size."""
        with self.data_lock:
            return len(self.read_buffer)

    def get_num_incoming_packets(self) -> int:
        """Get number of buffered incoming packets."""
        with self.data_lock:
            return len(self.incoming_packets)

    def _append_to_read_buffer(self, data: bytes) -> None:
        """Append data to read buffer."""
        with self.data_lock:
            self.read_buffer.extend(data)
