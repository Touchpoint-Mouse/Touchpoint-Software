import time
import threading
import math

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
        self.H_VIBRATION_EFFECT = hw_config['headers']['vibration_effect']
        self.H_VIBRATION_INTENSITY = hw_config['headers']['vibration_intensity']
        self.H_PIXELS_PER_MM = hw_config['headers'].get('pixels_per_mm', 0x30)
        
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
        
        # Maximum vibration intensity (units) - read-only from config
        self.max_vibration_intensity = hw_config['vibration']['max_intensity']

        # Per-command enable flags (default to enabled when keys are missing)
        command_enable = hw_config.get('command_enable', {})
        self.enable_elevation_commands = command_enable.get('elevation', True)
        self.enable_vibration_effect_commands = command_enable.get('vibration_effect', True)
        self.enable_vibration_intensity_commands = command_enable.get('vibration_intensity', True)
        self.enable_dynamic_capture_resize = command_enable.get('dynamic_capture_resize', True)

        # Physical display size for dynamic capture scaling from pixels/mm packets.
        display_cfg = hw_config.get('display', {})
        self.display_width_mm = float(display_cfg.get('width_mm', 0.0) or 0.0)
        self.display_height_mm = float(display_cfg.get('height_mm', 0.0) or 0.0)
        self.initial_pixels_per_mm = float(display_cfg.get('initial_pixels_per_mm', 2.0) or 2.0)
        self.last_pixels_per_mm = self.initial_pixels_per_mm if self.initial_pixels_per_mm > 0 else None
        
        # Display resolution (equivalent dots per display region)
        self.resolution = hw_config['display']['resolution']
        # Aspect ratio of texture pixels (width/height), derived from physical dimensions when available.
        if self.display_width_mm > 0 and self.display_height_mm > 0:
            self.aspect_ratio = self.display_width_mm / self.display_height_mm
        else:
            self.aspect_ratio = hw_config['display']['aspect_ratio']
        
        # Mesh dimensions (calculated from resolution and aspect ratio)
        self.mesh_dims = self.config.get_mesh_dimensions()

        # Register asynchronous packet handler for dynamic capture resizing.
        self.uart_core.set_header_handler(self.H_PIXELS_PER_MM, self._handle_pixels_per_mm_packet)

    def _to_byte(self, value):
        """Clamp and cast numeric values to a uint8-compatible integer."""
        return max(0, min(255, int(round(value))))
    
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

    def _handle_pixels_per_mm_packet(self, pkt):
        """Handle incoming pixels-per-mm packet and update capture region dimensions."""
        if not self.enable_dynamic_capture_resize:
            return

        try:
            pixels_per_mm = float(pkt.read_float())
            if pixels_per_mm <= 0:
                return

            self.last_pixels_per_mm = pixels_per_mm
            if self.display_width_mm <= 0 or self.display_height_mm <= 0:
                return

            # Use ceil so increasing ppm never gets stuck due to banker's rounding.
            hardware_width_px = max(1, int(math.ceil(self.display_width_mm * pixels_per_mm)))
            hardware_height_px = max(1, int(math.ceil(self.display_height_mm * pixels_per_mm)))
            scale_factor = float(getattr(self.config, 'capture_scale_factor', 1.0) or 1.0)
            capture_width_px = max(hardware_width_px, int(math.ceil(self.display_width_mm * pixels_per_mm * scale_factor)))
            capture_height_px = max(hardware_height_px, int(math.ceil(self.display_height_mm * pixels_per_mm * scale_factor)))

            if hasattr(self.plugin, 'set_capture_region_size'):
                self.plugin.set_capture_region_size(
                    capture_width_px,
                    capture_height_px,
                    pixels_per_mm=pixels_per_mm,
                )
        except Exception as e:
            logMessage(f"[ERROR] Failed to handle pixels-per-mm packet: {e}")
    
    def _health_check_loop(self):
        """Background thread to periodically check hardware connection."""
        while self.health_check_running:
            time.sleep(self.health_check_sleep)
            
            if not self.uart.is_open():
                # Attempt to reopen port
                self.initialize(health_check=False)
            else:
                # Send ping and check for response
                if not self._wait_for_ping():
                    logMessage("Hardware ping failed during health check")
                    self.hardware_connected = False
                    # Update emulator GUI hardware status if available
                    if self.plugin.emulator_gui:
                        self.plugin.emulator_gui.set_hardware_status(False)
                else:
                    if not self.hardware_connected:
                        logMessage("Hardware reconnected successfully")
                    self.hardware_connected = True
                    # Update emulator GUI hardware status if available
                    if self.plugin.emulator_gui:
                        self.plugin.emulator_gui.set_hardware_status(True)
        
    def send_vibration_effects(self, priority, effect_ids):
        """Send vibration effects to the device."""
        priority = self._to_byte(priority)
        effect_ids = [self._to_byte(effect_id) for effect_id in effect_ids]

        if not self.enable_vibration_effect_commands:
            return

        if self.hardware_connected:
            # Send to hardware
            pkt = self.uart_core.create_packet(self.H_VIBRATION_EFFECT)
            pkt.write_byte(priority)
            for effect_id in effect_ids:
                pkt.write_byte(effect_id)
            self.uart_core.send_packet(pkt)
                    
        # Update emulator GUI
        if self.plugin.emulator_gui:
            for effect_id in effect_ids:
                self.plugin.emulator_gui.set_vibration_effect(effect_id, priority)
            
    def send_vibration_intensity(self, priority, intensity, gauranteed=False):
        """Send a vibration intensity command to the device."""
        priority = self._to_byte(priority)
        intensity = self._to_byte(intensity)

        if not self.enable_vibration_intensity_commands:
            return
        
        # Clips intensity to max from config
        if intensity > self.max_vibration_intensity:
            intensity = self.max_vibration_intensity

        if self.hardware_connected:
            # Send to hardware
            pkt = self.uart_core.create_packet(self.H_VIBRATION_INTENSITY)
            pkt.write_byte(priority)
            pkt.write_byte(intensity)
            self.uart_core.send_packet(pkt, guarantee_delivery=gauranteed)
                    
        # Update emulator GUI
        if self.plugin.emulator_gui:
            self.plugin.emulator_gui.set_vibration_intensity(priority, intensity)
            
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
        if not self.enable_elevation_commands:
            return

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