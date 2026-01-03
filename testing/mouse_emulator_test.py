"""
Mouse Emulator using Songbird UART Protocol

Receives mouse movement commands via UART and controls the system mouse using pyautogui.
Protocol: Header 0x01, X movement byte, Y movement byte, Click state boolean byte

Usage: python mouse_emulator_test.py
"""

import time
import sys
import threading
import pyautogui
from songbird import SongbirdUART


SERIAL_PORT = "COM6"  # Change to your serial port
SERIAL_BAUD_RATE = 460800

# Global variables for accumulating mouse movements
mouse_lock = threading.Lock()
accumulated_x = 0
accumulated_y = 0
button_state_changed = False
button_state = False


def wait_for_ping(core):
    """Wait for ping response from microcontroller."""
    time.sleep(1)  # Wait to flush initial data
    core.flush()
    print("Waiting for ping from microcontroller", end="", flush=True)
    
    response = None
    while not response:
        response = core.wait_for_header(0xFF, 1000)
        core.flush()
        print(".", end="", flush=True)

    # Sends response back to acknowledge ping
    pkt = core.create_packet(0xFF)
    core.send_packet(pkt)
    
    print("\nPing received from microcontroller.")
    print("Mouse emulation enabled.")


def create_mouse_handler():
    """Create a handler for mouse command packets."""
    def mouse_handler(pkt):
        """Handle incoming mouse command packets (header 0x01)."""
        global accumulated_x, accumulated_y
        
        if pkt.get_payload_length() != 2:
            print(f"Warning: Expected 2 bytes, got {pkt.get_payload_length()}")
            return
        
        # Read mouse data
        x_move = pkt.read_byte()
        y_move = pkt.read_byte()
        
        # Convert unsigned bytes to signed movement (-128 to 127)
        if x_move > 127:
            x_move = x_move - 256
        if y_move > 127:
            y_move = y_move - 256
        
        # Accumulate movement instead of applying immediately
        with mouse_lock:
            accumulated_x += x_move
            accumulated_y += y_move
    
    return mouse_handler

def create_button_handler():
    """Create a handler for mouse button command packets."""
    def button_handler(pkt):
        """Handle incoming mouse button command packets (header 0x02)."""
        global button_state_changed, button_state
        
        if pkt.get_payload_length() != 1:
            print(f"Warning: Expected 1 byte, got {pkt.get_payload_length()}")
            return
        
        # Read click state
        click_state = bool(pkt.read_byte())
        
        # Store state change instead of applying immediately
        with mouse_lock:
            button_state = click_state
            button_state_changed = True
    
    return button_handler

def main():
    """Main program."""
    try:
        # Initialize UART
        uart = SongbirdUART("Mouse Emulator")
        core = uart.get_protocol()
        
        # Begin connection
        if not uart.begin(SERIAL_PORT, SERIAL_BAUD_RATE):
            print(f"Failed to open serial port {SERIAL_PORT}")
            return 1
    except Exception as e:
        print(f"Error initializing UART: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # Wait for device ping
    wait_for_ping(core)
    
    # Set up mouse command handler for header 0x01
    mouse_handler = create_mouse_handler()
    core.set_header_handler(0x01, mouse_handler)
    
    # Set up mouse button handler for header 0x02
    button_handler = create_button_handler()
    core.set_header_handler(0x02, button_handler)
    
    # Disable pyautogui safety delay for maximum performance
    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = False
    
    print("\nMouse emulator running. Press Ctrl+C to exit.")
    print("Listening for mouse commands (header 0x01)...")
    
    try:
        # Keep running and processing incoming packets
        while True:
            # Apply accumulated mouse movements
            global accumulated_x, accumulated_y, button_state_changed, button_state
            with mouse_lock:
                x = accumulated_x
                y = accumulated_y
                accumulated_x = 0
                accumulated_y = 0
                btn_changed = button_state_changed
                btn_state = button_state
                button_state_changed = False
            
            # Apply movements outside the lock
            if x != 0 or y != 0:
                pyautogui.moveRel(x, y)
            
            # Apply button state changes
            if btn_changed:
                if btn_state:
                    pyautogui.mouseDown()
                else:
                    pyautogui.mouseUp()
            
            time.sleep(0.01)  # Minimal delay to yield to other threads
    except KeyboardInterrupt:
        print("\n\nShutting down mouse emulator...")
        core.clear_header_handler(0x01)
        uart.close()
        print("Closed.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
