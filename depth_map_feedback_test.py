"""
Depth Map Feedback Test using Songbird UART Protocol

Displays an OpenCV window with a depth map image. When the mouse (emulated via 
pyautogui from UART commands) passes over the image, elevation commands are sent 
back based on pixel brightness.

Protocol: 
- Receives header 0x01 (mouse X/Y movement)
- Receives header 0x02 (mouse button state)
- Sends header 0x10 (elevation command as float)

Usage: python depth_map_feedback_test.py
"""

import time
import sys
import threading
import pyautogui
import cv2
import numpy as np
from songbird import SongbirdUART


SERIAL_PORT = "COM6"  # Change to your serial port
SERIAL_BAUD_RATE = 460800

# OpenCV window settings
WINDOW_NAME = "Depth Map Feedback Test"
IMAGE_WIDTH = 640
IMAGE_HEIGHT = 480

# Image file path
image_file_path = "tree.jpg"
# Elevation scale factor
elevation_scale = 0.5
# Depth map settings
ksize = (75, 75)  # Gaussian blur kernel size
zres = 256         # Depth resolution levels
invert = -1       # Invert depth map (1 or -1)

# Global variables for accumulating mouse movements
mouse_lock = threading.Lock()
accumulated_x = 0
accumulated_y = 0
button_state_changed = False
button_state = False

# Global variables for image and OpenCV window
depth_image = None
window_position = None
core_global = None

def image_to_depth_map(img, ksize, zres, invert=1):
    """Convert an image to a depth map suitable for elevation commands."""
    # Convert to grayscale
    depth_map = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # Apply guassian blur to reduce details
    depth_map = cv2.GaussianBlur(depth_map, ksize, 0)

    # Scale between 0 and 1 with zres levels
    depth_map = scale(depth_map, zres)
    
    # Invert if needed
    if invert == -1:
        depth_map = 1.0 - depth_map
    
    return depth_map

def scale(img, zres):
    # Scale image to given zres levels
    data = img.astype(np.float32)
    data = data-np.min(data)
    return np.round(data/np.max(data)*(zres-1))/(zres-1)

def setup_opencv_window():
    """Set up the OpenCV window with the depth map image."""
    global depth_image, window_position, depth_map
    
    # Create the depth map
    depth_map = image_to_depth_map(cv2.imread(image_file_path), ksize, zres, invert)
    
    # Create grayscale image from depth map
    depth_image = cv2.normalize(depth_map, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    
    # Convert to BGR for OpenCV display
    depth_image = cv2.cvtColor(depth_image, cv2.COLOR_GRAY2BGR)
    
    # Create window and set mouse callback
    cv2.namedWindow(WINDOW_NAME)
    cv2.setMouseCallback(WINDOW_NAME, mouse_callback)
    
    # Display the image
    cv2.imshow(WINDOW_NAME, depth_image)
    
    print(f"\nOpenCV window '{WINDOW_NAME}' created.")
    print("Move your mouse over the image to send elevation commands.")

# Mouse emulation / communication functions

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

def mouse_callback(event, x, y, flags, param):
    """OpenCV mouse callback to handle mouse events over the image."""
    global core_global, depth_image, depth_map
    
    if event == cv2.EVENT_MOUSEMOVE and core_global is not None and depth_image is not None:
        # Get pixel brightness at mouse position
        if 0 <= y < depth_image.shape[0] and 0 <= x < depth_image.shape[1]:
            # Gets depth map value
            elevation = depth_map[y, x] * elevation_scale
            
            # Send elevation command
            pkt = core_global.create_packet(0x10)
            pkt.write_float(elevation)
            core_global.send_packet(pkt)
            
            # Optional: Display current elevation on image
            img_with_text = depth_image.copy()
            cv2.putText(img_with_text, f"Elevation: {elevation:.3f}", (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(img_with_text, f"Pos: ({x}, {y})", (10, 60),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.circle(img_with_text, (x, y), 5, (0, 0, 255), -1)
            cv2.imshow(WINDOW_NAME, img_with_text)

def main():
    """Main program."""
    global core_global
    
    try:
        # Initialize UART
        uart = SongbirdUART("Depth Map Feedback Test")
        core = uart.get_protocol()
        core_global = core
        
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
    
    # Set up OpenCV window with depth map
    setup_opencv_window()
    
    # Send initial elevation command
    pkt = core.create_packet(0x10)
    pkt.write_float(0.0)
    core.send_packet(pkt)
    
    # Set up mouse command handler for header 0x01
    mouse_handler = create_mouse_handler()
    core.set_header_handler(0x01, mouse_handler)
    
    # Set up mouse button handler for header 0x02
    button_handler = create_button_handler()
    core.set_header_handler(0x02, button_handler)
    
    # Disable pyautogui safety delay for maximum performance
    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = False
    
    print("\nDepth map feedback test running. Press Ctrl+C or 'q' to exit.")
    print("Listening for mouse commands (header 0x01)...")
    print("Mouse movement over image will trigger elevation commands.")
    
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
            
            # Process OpenCV window events (necessary for mouse callback)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                print("\n'q' key pressed. Exiting...")
                break
            
            time.sleep(0.01)  # Minimal delay to yield to other threads
    except KeyboardInterrupt:
        print("\n\nShutting down depth map feedback test...")
        core.clear_header_handler(0x01)
        core.clear_header_handler(0x02)
        cv2.destroyAllWindows()
        uart.close()
        print("Closed.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
