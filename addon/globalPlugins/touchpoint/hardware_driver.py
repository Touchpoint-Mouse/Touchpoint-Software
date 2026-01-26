import time
import threading

from songbird import SongbirdUART
from .utils import logMessage
from .dependencies import np

class HardwareDriver:
    # Header definitions
    H_PING = 0xFF
    H_ELEVATION = 0x10
    H_ELEVATION_SPEED = 0x11
    H_VIBRATION = 0x20
    
    # Serial configuration
    SERIAL_PORT = "COM6"
    SERIAL_BAUD_RATE = 460800
    
    # Depth map window size (pixels around cursor)
    DEPTH_MAP_WINDOW_SIZE = 50
    
    def __init__(self):
        # UART connection for hardware
        self.uart = SongbirdUART("Touchpoint NVDA Addon")
        self.uart_core = self.uart.get_protocol()
        self.hardware_connected = False
        
        # Emulator GUI (will be set by plugin)
        self.emulator_gui = None
        self.emulator_gui_lock = threading.Lock()  # Lock for emulator_gui access
        
        # Current elevation state
        self.elevation = 0.0
        self.elevation_lock = threading.Lock()  # Lock for elevation state
        
        # Maximum elevation speed (units per second, where 1 unit = full range)
        self.max_elevation_speed = 2.0
        self.speed_lock = threading.Lock()  # Lock for speed access
    
    def initialize(self):
        """Initialize the hardware driver and establish communication."""
        if not self.uart.begin(self.SERIAL_PORT, self.SERIAL_BAUD_RATE):
            self.hardware_connected = False
            logMessage("Hardware not connected")
        else:
            # Wait for device ping
            self.hardware_connected = self._wait_for_ping()
            if not self.hardware_connected:
                logMessage("Hardware did not respond to ping")
        
        # Update emulator GUI if available
        with self.emulator_gui_lock:
            if self.emulator_gui:
                self.emulator_gui.set_hardware_status(self.hardware_connected)
                self.emulator_gui.set_elevation_speed(self.max_elevation_speed)
        
        return self.hardware_connected
    
    def set_emulator_gui(self, emulator_gui):
        """Set the emulator GUI reference.
        
        Args:
            emulator_gui: TouchpointEmulatorGUI instance
        """
        with self.emulator_gui_lock:
            self.emulator_gui = emulator_gui
            if emulator_gui:
                emulator_gui.set_hardware_status(self.hardware_connected)
                emulator_gui.set_elevation_speed(self.max_elevation_speed)
        
    def set_max_elevation_speed(self, speed):
        """Set the maximum elevation speed for the device."""
        with self.speed_lock:
            self.max_elevation_speed = speed
        
        if self.hardware_connected:
            # Send to hardware
            try:
                pkt = self.uart_core.create_packet(self.H_ELEVATION_SPEED)
                pkt.write_float(speed)
                # Make a guaranteed send
                self.uart_core.send_packet(pkt, True)
            except Exception as e:
                logMessage(f"Error sending elevation speed to hardware: {e}")
        
        # Update emulator GUI
        with self.emulator_gui_lock:
            if self.emulator_gui:
                self.emulator_gui.set_elevation_speed(speed)
    
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
            try:
                with self.speed_lock:
                    speed = self.max_elevation_speed
                pkt = self.uart_core.create_packet(self.H_ELEVATION_SPEED)
                pkt.write_float(speed)
                self.uart_core.send_packet(pkt, True)
            except Exception as e:
                logMessage(f"Error sending elevation speed to hardware: {e}")
            
            return True
        else:
            return False
        
    def send_vibration(self, amplitude, frequency, duration):
        """Send a vibration command to the device."""
        if self.hardware_connected:
            # Send to hardware
            try:
                pkt = self.uart_core.create_packet(self.H_VIBRATION)
                pkt.write_float(amplitude)
                pkt.write_float(frequency)
                pkt.write_int16(duration)
                self.uart_core.send_packet(pkt)
            except Exception as e:
                logMessage(f"Error sending vibration to hardware: {e}")
        
        # Update emulator GUI
        with self.emulator_gui_lock:
            if self.emulator_gui:
                self.emulator_gui.set_vibration(amplitude, frequency, duration)
                   
    def send_elevation(self, elevation):
        """Send an elevation command to the device."""
        # Update current elevation state
        with self.elevation_lock:
            self.elevation = elevation
        
        if self.hardware_connected:
            # Send to hardware
            try:
                pkt = self.uart_core.create_packet(self.H_ELEVATION)
                pkt.write_float(elevation)
                self.uart_core.send_packet(pkt)
            except Exception as e:
                logMessage(f"Error sending elevation to hardware: {e}")
        
        # Update emulator GUI
        with self.emulator_gui_lock:
            if self.emulator_gui:
                self.emulator_gui.set_elevation(elevation)
    
    def update_depth_map(self, region, depth_map, mouse_pos):
        """Update the depth map display in emulator.
        
        Args:
            region: Screen region (location object with left, top, width, height)
            depth_map: Numpy array with normalized depth values (0-1)
            mouse_pos: Tuple of (x, y) mouse position in screen coordinates
        """
        with self.emulator_gui_lock:
            if not self.emulator_gui:
                return
            
            if depth_map is None or region is None:
                # Clear depth map in emulator
                self.emulator_gui.update_depth_map(None)
                return
            
            try:
                # Calculate window around mouse in depth map coordinates
                # Convert mouse position to relative coordinates in depth map
                rel_x = int((mouse_pos[0] - region.left) * depth_map.shape[1] / region.width)
                rel_y = int((mouse_pos[1] - region.top) * depth_map.shape[0] / region.height)
                
                # Clamp coordinates
                rel_x = max(0, min(depth_map.shape[1] - 1, rel_x))
                rel_y = max(0, min(depth_map.shape[0] - 1, rel_y))
                
                # Calculate window bounds in depth map coordinates
                half_window = self.DEPTH_MAP_WINDOW_SIZE // 2
                
                # Add padding of half_window to each side of depth map
                padded_depth_map = np.pad(depth_map, ((half_window, half_window), (half_window, half_window)), constant_values=0)
                x_start = rel_x
                x_end = rel_x + 2*half_window
                y_start = rel_y
                y_end = rel_y + 2*half_window
                
                # Extract window
                window = padded_depth_map[y_start:y_end, x_start:x_end]
                
                # Update emulator
                self.emulator_gui.update_depth_map(window)
            except Exception as e:
                logMessage(f"[ERROR] Failed to update depth map: {e}")
        
    def add_elevation_offset(self, offset):
        """Add an elevation offset to the current elevation."""
        with self.elevation_lock:
            new_elevation = self.elevation + offset
        self.send_elevation(new_elevation)
    
        
    def terminate(self):
        """Terminate the hardware driver and close communication."""
        # Close UART
        self.hardware_connected = False
        if self.uart:
            try:
                self.uart.close()
            except:
                pass