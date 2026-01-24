import time

from songbird import SongbirdUART

class HardwareDriver:
    # Header definitions
    H_PING = 0xFF
    H_ELEVATION = 0x10
    H_ELEVATION_SPEED = 0x11
    H_VIBRATION = 0x20
    
    # Serial configuration
    SERIAL_PORT = "COM6"
    SERIAL_BAUD_RATE = 460800
    
    def __init__(self):
        self.uart = SongbirdUART("Touchpoint NVDA Addon")
        self.core = self.uart.get_protocol()
        self.enabled = True
    
    def initialize(self):
        """Initialize the hardware driver and establish communication."""
        # Initialize UART
        self.uart = SongbirdUART("Touchpoint NVDA Addon")
        self.core = self.uart.get_protocol()
        
        if not self.uart.begin(self.SERIAL_PORT, self.SERIAL_BAUD_RATE):
            self.enabled = False
            return
        
        # Wait for device ping
        self.enabled = self._wait_for_ping()
        
        return self.enabled
        
    def set_max_elevation_speed(self, speed):
        """Set the maximum elevation speed for the device."""
        if not self.enabled:
            return
        # Sends max elevation speed to device
        pkt = self.core.create_packet(self.H_ELEVATION_SPEED)
        pkt.write_float(speed)
        # Make a guaranteed send
        self.core.send_packet(pkt, True)
    
    def _wait_for_ping(self):
        """Wait for ping response from microcontroller."""
        time.sleep(1)
        self.core.flush()
        self.logMessage("Waiting for ping from microcontroller...")
        
        response = None
        timeout_count = 0
        while not response and timeout_count < 10:
            response = self.core.wait_for_header(self.H_PING, 1000)
            self.core.flush()
            timeout_count += 1
        
        if response:
            # Send response back to acknowledge ping
            pkt = self.core.create_packet(self.H_PING)
            self.core.send_packet(pkt)
            self.logMessage("Ping received from microcontroller")
            return True
        else:
            return False
        
    def send_vibration(self, amplitude, frequency, duration):
        """Send a vibration command to the device."""
        if not self.enabled:
            return
        pkt = self.core.create_packet(self.H_VIBRATION)
        pkt.write_float(amplitude)
        pkt.write_float(frequency)
        pkt.write_int16(duration)
        self.core.send_packet(pkt)
        
    def send_elevation(self, elevation):
        """Send an elevation command to the device."""
        if not self.enabled:
            return
        pkt = self.core.create_packet(self.H_ELEVATION)
        pkt.write_float(elevation)
        self.core.send_packet(pkt)
        
    def terminate(self):
        """Terminate the hardware driver and close communication."""
        if self.uart:
            try:
                self.uart.close()
            except:
                pass