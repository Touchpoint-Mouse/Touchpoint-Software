import time
import threading

from songbird import SongbirdUART, SongbirdUDP
from .utils import logMessage

class HardwareDriver:
    # Header definitions
    H_PING = 0xFF
    H_ELEVATION = 0x10
    H_ELEVATION_SPEED = 0x11
    H_VIBRATION = 0x20
    H_STATUS = 0x30  # Status message to emulator only
    
    # Serial configuration
    SERIAL_PORT = "COM6"
    SERIAL_BAUD_RATE = 460800
    
    # UDP configuration for emulator
    UDP_ADDON_PORT = 7420  # Port addon listens on
    UDP_EMULATOR_PORT = 7421  # Port emulator listens on
    UDP_LOCALHOST = "127.0.0.1"
    
    def __init__(self):
        # UART connection for hardware
        self.uart = SongbirdUART("Touchpoint NVDA Addon")
        self.uart_core = self.uart.get_protocol()
        self.hardware_connected = False
        
        # Checks for new UART connections with hardware
        self.hardware_check_thread = None
        
        # UDP connection for emulator
        self.udp = SongbirdUDP("Touchpoint NVDA Addon UDP")
        self.udp_core = self.udp.get_protocol()
        # Set up handler for receiving emulator pings
        self.udp_core.set_header_handler(self.H_PING, self._handle_emulator_ping)
        self.udp_running = False
        self.emulator_connected = False
        
        # Current elevation state
        self.elevation = 0.0
        
        # Maximum elevation speed (units per second, where 1 unit = full range)
        self.max_elevation_speed = 2.0
    
    def initialize(self):
        """Initialize the hardware driver and establish communication."""
        # Initialize UDP listener for emulator
        self._initialize_udp()
        
        if not self.uart.begin(self.SERIAL_PORT, self.SERIAL_BAUD_RATE):
            self.hardware_connected = False
            logMessage("Hardware not connected, using emulator only if available")
        else:
            # Wait for device ping
            self.hardware_connected = self._wait_for_ping()
            if not self.hardware_connected:
                logMessage("Hardware did not respond to ping")
        
        # Send initial status to emulator
        self._send_status_to_emulator()
        
        return self.hardware_connected or self.emulator_connected
    
    def _initialize_udp(self):
        """Initialize UDP communication for emulator."""
        try:
            # Listen on addon port
            if self.udp.listen(self.UDP_ADDON_PORT):
                # Set default remote to emulator port
                self.udp.set_remote(self.UDP_LOCALHOST, self.UDP_EMULATOR_PORT)
                self.udp_running = True
                logMessage(f"UDP listener started on port {self.UDP_ADDON_PORT}")
            else:
                logMessage("Failed to start UDP listener")
        except Exception as e:
            logMessage(f"UDP initialization error: {e}")
            
    def _handle_emulator_ping(self, packet):
        """Handle ping from emulator."""
        was_connected = self.emulator_connected
        self.emulator_connected = True
        
        if not was_connected:
            logMessage("Emulator connected via UDP")
        
        # Send ping response
        try:
            pkt = self.udp_core.create_packet(self.H_PING)
            self.udp_core.send_packet(pkt)
            
            # Send current status
            self._send_status_to_emulator()
            
            # Send max elevation speed if this is a new connection
            if not was_connected:
                self._send_elevation_speed_to_emulator()
        except Exception as e:
            logMessage(f"Error responding to emulator ping: {e}")
    
    def _send_status_to_emulator(self):
        """Send hardware connection status to emulator."""
        if not self.udp or not self.emulator_connected:
            return
        
        try:
            pkt = self.udp_core.create_packet(self.H_STATUS)
            # Write 1 if hardware connected, 0 if not
            pkt.write_uint8(1 if self.hardware_connected else 0)
            self.udp_core.send_packet(pkt)
        except Exception as e:
            logMessage(f"Error sending status to emulator: {e}")
    
    def _send_elevation_speed_to_emulator(self):
        """Send max elevation speed to emulator."""
        if not self.udp or not self.emulator_connected:
            return
        
        try:
            pkt = self.udp_core.create_packet(self.H_ELEVATION_SPEED)
            pkt.write_float(self.max_elevation_speed)
            self.udp_core.send_packet(pkt)
        except Exception as e:
            logMessage(f"Error sending elevation speed to emulator: {e}")
    
    def _send_elevation_speed_to_hardware(self):
        """Send max elevation speed to hardware."""
        if not self.hardware_connected:
            return
        
        try:
            pkt = self.uart_core.create_packet(self.H_ELEVATION_SPEED)
            pkt.write_float(self.max_elevation_speed)
            # Make a guaranteed send
            self.uart_core.send_packet(pkt, True)
            logMessage(f"Sent max elevation speed to hardware: {self.max_elevation_speed} units/sec")
        except Exception as e:
            logMessage(f"Error sending elevation speed to hardware: {e}")
        
    def set_max_elevation_speed(self, speed):
        """Set the maximum elevation speed for the device."""
        if self.hardware_connected:
            # Sends max elevation speed to hardware
            pkt = self.core.create_packet(self.H_ELEVATION_SPEED)
            pkt.write_float(speed)
            # Make a guaranteed send
            self.core.send_packet(pkt, True)
        
        if self.emulator_connected:
            # Send to emulator
            try:
                pkt = self.udp_core.create_packet(self.H_ELEVATION_SPEED)
                pkt.write_float(speed)
                self.udp_core.send_packet(pkt)
            except Exception as e:
                logMessage(f"Error sending elevation speed to emulator: {e}")
    
    def _wait_for_ping(self):
        """Wait for ping response from microcontroller."""
        time.sleep(1)
        self.uart_core.flush()
        logMessage("Waiting for ping from microcontroller...")
        
        response = None
        timeout_count = 0
        while not response and timeout_count < 10:
            response = self.uart_core.wait_for_header(self.H_PING, 1000)
            self.uart_core.flush()
            timeout_count += 1
        
        if response:
            # Send response back to acknowledge ping
            pkt = self.uart_core.create_packet(self.H_PING)
            self.uart_core.send_packet(pkt)
            logMessage("Ping received from microcontroller")
            
            # Send max elevation speed to hardware
            self._send_elevation_speed_to_hardware()
            
            return True
        else:
            return False
        
    def send_vibration(self, amplitude, frequency, duration):
        """Send a vibration command to the device."""
        if self.hardware_connected:
            # Send to hardware
            pkt = self.uart_core.create_packet(self.H_VIBRATION)
            pkt.write_float(amplitude)
            pkt.write_float(frequency)
            pkt.write_int16(duration)
            self.uart_core.send_packet(pkt)
        
        if self.emulator_connected:
            # Send to emulator
            pkt = self.udp_core.create_packet(self.H_VIBRATION)
            pkt.write_float(amplitude)
            pkt.write_float(frequency)
            pkt.write_int16(duration)
            self.udp_core.send_packet(pkt)
                   
    def send_elevation(self, elevation):
        """Send an elevation command to the device."""
        # Update current elevation state
        self.elevation = elevation
        
        if self.hardware_connected:
            # Send to hardware
            pkt = self.uart_core.create_packet(self.H_ELEVATION)
            pkt.write_float(elevation)
            self.uart_core.send_packet(pkt)
        
        if self.emulator_connected:
            # Send to emulator
            pkt = self.udp_core.create_packet(self.H_ELEVATION)
            pkt.write_float(elevation)
            self.udp_core.send_packet(pkt)
        
    def add_elevation_offset(self, offset):
        """Add an elevation offset to the current elevation."""
        if not self.hardware_connected and not self.emulator_connected:
            return
        new_elevation = self.elevation + offset
        self.send_elevation(new_elevation)
    
        
    def terminate(self):
        """Terminate the hardware driver and close communication."""
        # Close UDP
        self.udp_running = False
        if self.udp:
            try:
                self.udp.close()
            except:
                pass
        
        # Close UART
        self.hardware_connected = False
        if self.uart:
            try:
                self.uart.close()
            except:
                pass