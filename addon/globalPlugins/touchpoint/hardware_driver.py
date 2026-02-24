import time
import threading

from songbird import SongbirdUART
from .utils import logMessage
from .dependencies import np
from .config import TouchpointConfig

class HardwareDriver:
    def __init__(self, plugin):
        self.plugin = plugin
        
        # Get centralized configuration (singleton - only created once)
        self.config = TouchpointConfig.get_instance()
        hw_config = self.config.hardware
        
        # Header definitions from config
        self.H_PING = hw_config['headers']['ping']
        self.H_ELEVATION = hw_config['headers']['elevation']
        self.H_ELEVATION_SPEED = hw_config['headers']['elevation_speed']
        self.H_VIBRATION = hw_config['headers']['vibration']
        
        # Serial configuration from config
        self.SERIAL_PORT = hw_config['serial']['port']
        self.SERIAL_BAUD_RATE = hw_config['serial']['baud_rate']
        
        # UART connection for hardware
        self.uart = SongbirdUART("Touchpoint NVDA Addon")
        self.uart_core = self.uart.get_protocol()
        self.hardware_connected = False
        
        # Hardware health checking thread
        self.health_check_thread = None
        self.health_check_running = False
        self.health_check_sleep = 0.5
        
        # Current elevation state
        self.elevation = 0.0
        # Sum of relative elevation offsets (for tracking cumulative relative changes)
        self.relative_elevation_offset = 0.0
        # Highest priority global elevation command (None if no command active)
        # Format: (elevation_value, priority_level)
        self.global_elevation_command = None
        # Maximum elevation (units) - read-only from config
        self.max_elevation = hw_config['elevation']['max_elevation']
        # Maximum elevation speed (units per second) - can be changed dynamically
        self.max_elevation_speed = hw_config['elevation']['max_elevation_speed']
        
        # Display resolution (equivalent dots per display region)
        self.resolution = hw_config['display']['resolution']
        # Aspect ratio of texture pixels (width/height)
        self.aspect_ratio = hw_config['display']['aspect_ratio']
        
        # Mesh dimensions (calculated from resolution and aspect ratio)
        self.mesh_dims = self.config.get_mesh_dimensions()
    
    def initialize(self, health_check=True):
        """Initialize the hardware driver and establish communication."""
        if not self.uart.begin(self.SERIAL_PORT, self.SERIAL_BAUD_RATE, silent=True):
            self.hardware_connected = False
        else:
            # Wait for device ping
            self.hardware_connected = self._wait_for_ping()
            if not self.hardware_connected:
                logMessage("Hardware did not respond to ping")
            else:
                # Send max elevation speed to hardware
                self.set_max_elevation_speed(self.max_elevation_speed)
        
        # Update emulator GUI hardware status if available
        if self.plugin.emulator_gui:
            self.plugin.emulator_gui.set_hardware_status(self.hardware_connected)
            
        # Start health check thread if not started
        if health_check and not self.health_check_running:
            self.health_check_running = True
            self.health_check_thread = threading.Thread(target=self._health_check_loop, daemon=True)
            self.health_check_thread.start()
        
        return self.hardware_connected
    
    def terminate(self):
        """Terminate the hardware driver and close communication."""
        # Stop health check thread
        self.health_check_running = False
        # Close UART
        self.hardware_connected = False
        if self.uart:
            self.uart.close()
    
    def _wait_for_ping(self):
        """Wait for ping response from microcontroller."""
        time.sleep(self.health_check_sleep)
        self.uart_core.flush()
        
        # Send ping
        self.uart_core.send_packet(self.uart_core.create_packet(self.H_PING))
        
        logMessage("Waiting for ping from microcontroller...")
        
        response = None
        timeout_count = 0
        while not response and timeout_count < 10:
            response = self.uart_core.wait_for_header(self.H_PING, 1000)
            self.uart_core.flush()
            timeout_count += 1
        
        return response is not None
    
    def _health_check_loop(self):
        """Background thread to periodically check hardware connection."""
        while self.health_check_running:
            time.sleep(self.health_check_sleep)
            
            if not self.uart.is_open():
                # Attempt to reopen port
                self.initialize(health_check=False)
        
    def send_vibration(self, amplitude, frequency, duration):
        """Send a vibration command to the device."""
        if self.hardware_connected:
            # Send to hardware
            pkt = self.uart_core.create_packet(self.H_VIBRATION)
            pkt.write_float(amplitude)
            pkt.write_float(frequency)
            pkt.write_int16(duration)
            self.uart_core.send_packet(pkt)
                    
        # Update emulator GUI
        if self.plugin.emulator_gui:
            self.plugin.emulator_gui.set_vibration(amplitude, frequency, duration)
            
    def set_max_elevation_speed(self, speed):
        """Set the maximum elevation speed for the device."""
        self.max_elevation_speed = speed
        
        if self.hardware_connected:
            # Send to hardware
            pkt = self.uart_core.create_packet(self.H_ELEVATION_SPEED)
            pkt.write_float(speed)
            # Make a guaranteed send
            self.uart_core.send_packet(pkt, True)
        
        # Update emulator GUI if available
        if self.plugin.emulator_gui:
            self.plugin.emulator_gui.set_elevation_speed(speed)
                   
    def set_global_elevation(self, elevation, priority=0):
        """Send an elevation command to the device.
        
        Args:
            elevation: Elevation value to send (0.0-1.0)
            priority: Priority level of the command (higher values override lower ones)
        """
        # Update global elevation command if higher priority
        if self.global_elevation_command is None or priority >= self.global_elevation_command[1]:
            self.global_elevation_command = (elevation, priority)
        else:
            # Lower priority command ignored
            return   
            
    def add_elevation_offset(self, offset):
        """Add an elevation offset to the current elevation."""
        self.relative_elevation_offset += offset
    
    def get_current_elevation(self):
        """Get the current elevation value."""
        return self.elevation
    
    def get_max_elevation(self):
        """Get the maximum elevation constraint from config (read-only)."""
        return self.max_elevation
        
    def cycle_state(self):
        """Cycle the hardware state machine. Should be called periodically."""
        # Determine effective elevation command
        elevation = 0
        if self.global_elevation_command is not None:
            # Use global elevation command
            self.elevation = self.global_elevation_command[0]
            elevation = self.elevation
            
        # Add relative elevation offset
        elevation += self.relative_elevation_offset
        # Clamp resulting elevation to valid range
        elevation = max(0, min(self.max_elevation, elevation))
        
        # Reset relative elevation offset and global elevation command
        self.relative_elevation_offset = 0.0
        self.global_elevation_command = None
        
        # Send current elevation to hardware
        if self.hardware_connected:
            pkt = self.uart_core.create_packet(self.H_ELEVATION)
            pkt.write_float(elevation)
            self.uart_core.send_packet(pkt)
        
        # Update emulator GUI
        if self.plugin.emulator_gui:
            self.plugin.emulator_gui.set_elevation(elevation)
        pass