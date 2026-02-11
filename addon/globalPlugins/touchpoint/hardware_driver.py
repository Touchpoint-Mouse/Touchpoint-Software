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
    
    def __init__(self, plugin):
        self.plugin = plugin
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
        self.elevation_lock = threading.Lock()  # Lock for elevation state
        
        # Maximum elevation speed (units per second, where 1 unit = full range)
        self.max_elevation_speed = 2.0
        self.speed_lock = threading.Lock()  # Lock for speed access
        
        # Rate limiting for packet sends to prevent buffer overflow
        self.last_elevation_send_time = 0
        self.last_vibration_send_time = 0
        self.min_send_interval = 0.02  # Minimum 20ms between sends (50 Hz max)
        self.send_time_lock = threading.Lock()
    
    def initialize(self, health_check=True):
        """Initialize the hardware driver and establish communication."""
        if not self.uart.begin(self.SERIAL_PORT, self.SERIAL_BAUD_RATE):
            self.hardware_connected = False
            logMessage("Hardware not connected")
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
            # Rate limiting check
            current_time = time.time()
            with self.send_time_lock:
                if current_time - self.last_vibration_send_time < self.min_send_interval:
                    # Skip send to prevent buffer overflow
                    pass
                else:
                    self.last_vibration_send_time = current_time
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
        with self.speed_lock:
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
                   
    def send_elevation(self, elevation, priority=False):
        """Send an elevation command to the device.
        
        Args:
            elevation: Elevation value to send (0.0-1.0)
            priority: If True, bypasses rate limiting and overrides pending commands
        """
        # Update current elevation state
        with self.elevation_lock:
            self.elevation = elevation
        
        if self.hardware_connected:
            current_time = time.time()
            
            if priority:
                # Priority commands bypass rate limiting and override pending
                with self.send_time_lock:
                    self.last_elevation_send_time = current_time
                # Send immediately
                pkt = self.uart_core.create_packet(self.H_ELEVATION)
                pkt.write_float(elevation)
                # Sends in guaranteed mode
                self.uart_core.send_packet(pkt, True)
            else:
                # Rate limiting check for normal commands
                with self.send_time_lock:
                    if current_time - self.last_elevation_send_time < self.min_send_interval:
                        # Skip send to prevent buffer overflow
                        pass
                    else:
                        self.last_elevation_send_time = current_time
                        # Send to hardware
                        pkt = self.uart_core.create_packet(self.H_ELEVATION)
                        pkt.write_float(elevation)
                        self.uart_core.send_packet(pkt)
        
        # Update emulator GUI
        if self.plugin.emulator_gui:
            self.plugin.emulator_gui.set_elevation(elevation)
            
    def add_elevation_offset(self, offset):
        """Add an elevation offset to the current elevation."""
        with self.elevation_lock:
            new_elevation = self.elevation + offset
        self.send_elevation(new_elevation)
    
    def get_current_elevation(self):
        """Get the current elevation value."""
        with self.elevation_lock:
            return self.elevation
    
    def update_depth_map(self, region, depth_map, mouse_pos):
        """Update the depth map display in emulator.
        
        Args:
            region: Screen region (location object with left, top, width, height)
            depth_map: Numpy array with normalized depth values (0-1)
            mouse_pos: Tuple of (x, y) mouse position in screen coordinates
        """
        if not self.plugin.emulator_gui:
            return
        
        if depth_map is None or region is None:
            # Clear depth map in emulator
            self.plugin.emulator_gui.update_depth_map(None)
            return
        
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
        self.plugin.emulator_gui.update_depth_map(window)
        
    def cycle_state(self):
        """Cycle the hardware state machine. Should be called periodically."""
        # For this simple driver, we don't have a complex state machine,
        # but we can perform periodic tasks here if needed.
        pass
        
    def terminate(self):
        """Terminate the hardware driver and close communication."""
        # Stop health check thread
        self.health_check_running = False
        # Close UART
        self.hardware_connected = False
        if self.uart:
            self.uart.close()