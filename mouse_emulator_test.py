"""
Mouse Emulator using Songbird UART Protocol

Receives mouse movement commands via UART and controls the system mouse using pyautogui.
Protocol: Header 0x01, X movement byte, Y movement byte, Click state boolean byte

Usage: python mouse_emulator_test.py
"""

import time
import sys
import pyautogui
from songbird import SongbirdUART


SERIAL_PORT = "COM6"  # Change to your serial port
SERIAL_BAUD_RATE = 115200


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
    previous_click_state = [False]  # Track previous click state
    
    def mouse_handler(pkt):
        """Handle incoming mouse command packets (header 0x01)."""
        if not pkt or pkt.get_header() != 0x01:
            return
        
        if pkt.get_payload_length() != 3:
            print(f"Warning: Expected 3 bytes, got {pkt.get_payload_length()}")
            return
        
        # Read mouse data
        x_move = pkt.read_byte()
        y_move = pkt.read_byte()
        click_state = bool(pkt.read_byte())
        
        # Convert unsigned bytes to signed movement (-128 to 127)
        if x_move > 127:
            x_move = x_move - 256
        if y_move > 127:
            y_move = y_move - 256
        
        # Move mouse if there's movement
        if x_move != 0 or y_move != 0:
            pyautogui.moveRel(x_move, y_move)
        
        # Handle click state changes
        if click_state and not previous_click_state[0]:
            # Click state went from False to True - press mouse button
            pyautogui.mouseDown()
            previous_click_state[0] = True
        elif not click_state and previous_click_state[0]:
            # Click state went from True to False - release mouse button
            pyautogui.mouseUp()
            previous_click_state[0] = False
    
    return mouse_handler


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
    
    print("\nMouse emulator running. Press Ctrl+C to exit.")
    print("Listening for mouse commands (header 0x01)...")
    
    try:
        # Keep running and processing incoming packets
        while True:
            time.sleep(0.01)  # Small delay to prevent CPU spinning
    except KeyboardInterrupt:
        print("\n\nShutting down mouse emulator...")
        core.clear_header_handler(0x01)
        uart.close()
        print("Closed.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
