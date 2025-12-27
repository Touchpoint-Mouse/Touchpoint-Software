"""
Real-Time Screen Capture Depth Map Feedback Test using Songbird UART Protocol

Continuously captures the full screen using DXcam (DirectX screen capture) and converts it to a depth map.
Tracks global mouse position in real-time and sends elevation commands based on 
the depth map value at the current mouse position. No OpenCV window is displayed.

Protocol: 
- Receives header 0x01 (mouse X/Y movement)
- Receives header 0x02 (mouse button state)
- Sends header 0x10 (elevation command as float)

Usage: python screen_capture_test.py
Requirements: pip install dxcam
"""

import time
import sys
import threading
import pyautogui
import cv2
import numpy as np
from songbird import SongbirdUART
import dxcam


SERIAL_PORT = "COM6"  # Change to your serial port
SERIAL_BAUD_RATE = 460800

# Screen capture settings
CAPTURE_INTERVAL = 0.0  # No artificial delay - run at maximum speed
MOUSE_CHECK_INTERVAL = 0.01  # Seconds between mouse position checks

# Elevation scale factor
elevation_scale = 0.5
# Depth map settings
DOWNSAMPLE_FACTOR = 16  # Downsample by this factor for faster processing (higher = faster)
ksize = 1  # Gaussian blur kernel size (1 = disabled for speed)
zres = 256         # Depth resolution levels
invert = 1       # Invert depth map (1 or -1)

# Global variables for accumulating mouse movements
mouse_lock = threading.Lock()
accumulated_x = 0
accumulated_y = 0
button_state_changed = False
button_state = False

# Global variables for depth map and screen info
depth_map = None
depth_map_lock = threading.Lock()
screen_size = None
core_global = None
last_mouse_pos = None

def scale_image(img, min_val, max_val):
    """Scale image values to a specific range."""
    img = img - np.min(img)
    return img / np.max(img) * (max_val - min_val) + min_val

def capture_screen_as_depth_map(camera):
    """Capture the full screen and convert it to a depth map."""
    # Capture frame using DXcam (much faster than mss)
    frame = camera.grab()
    
    if frame is None:
        return None, None
    
    # Frame is already a numpy array in RGB format
    # Simple downsampling using array slicing (much faster than cv2.resize)
    # Take every Nth pixel
    img_small = frame[::DOWNSAMPLE_FACTOR, ::DOWNSAMPLE_FACTOR, :]
    
    # Convert to grayscale using simple average (faster than weighted)
    gray_small = np.mean(img_small, axis=2).astype(np.float32)
    
    # Normalize to 0-1
    depth_map_small = gray_small / 255.0
    
    # Invert if needed
    if invert == -1:
        depth_map_small = 1.0 - depth_map_small
    
    # Return depth map and screen size
    return depth_map_small, (frame.shape[1], frame.shape[0])

def screen_capture_thread():
    """Thread function to continuously capture and update the screen depth map."""
    global depth_map, screen_size
    
    print("Starting real-time screen capture thread...", flush=True)
    
    # Create DXcam camera instance (uses DirectX for fast capture)
    camera = dxcam.create()
    
    print("DXcam initialized", flush=True)
    
    frame_count = 0
    start_time = time.time()
    
    while True:
        try:
            capture_start = time.time()
            
            # Capture screen and update depth map
            new_depth_map, new_screen_size = capture_screen_as_depth_map(camera)
            
            if new_depth_map is None:
                time.sleep(0.001)
                continue
            
            capture_time = time.time() - capture_start
            
            with depth_map_lock:
                depth_map = new_depth_map
                screen_size = new_screen_size
            
            frame_count += 1
            if frame_count % 100 == 0:
                elapsed = time.time() - start_time
                actual_fps = frame_count / elapsed
                print(f"Capture FPS: {actual_fps:.1f}, Last capture time: {capture_time*1000:.1f}ms", flush=True)
                # Reset counters to avoid overflow
                frame_count = 0
                start_time = time.time()
            
            # No sleep - run at maximum speed
        except Exception as e:
            print(f"Error in screen capture thread: {e}", flush=True)
            import traceback
            traceback.print_exc()
            time.sleep(1)

def get_elevation_at_position(x, y):
    """Get elevation value at a specific screen position."""
    global depth_map, screen_size
    
    with depth_map_lock:
        if depth_map is None or screen_size is None:
            return None
        
        # Map screen coordinates to downsampled depth map coordinates
        map_x = int(x // DOWNSAMPLE_FACTOR)
        map_y = int(y // DOWNSAMPLE_FACTOR)
        
        # Clamp to valid range
        map_x = max(0, min(map_x, depth_map.shape[1] - 1))
        map_y = max(0, min(map_y, depth_map.shape[0] - 1))
        
        # Get elevation from depth map directly
        elevation = depth_map[map_y, map_x] * elevation_scale
        
        return elevation

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

def main():
    """Main program."""
    global core_global, last_mouse_pos
    
    try:
        # Initialize UART
        uart = SongbirdUART("Real-Time Screen Depth Map Feedback Test")
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
    
    # Start screen capture thread
    print("Starting screen capture thread...", flush=True)
    capture_thread = threading.Thread(target=screen_capture_thread, daemon=True)
    capture_thread.start()
    print(f"Thread started: {capture_thread.is_alive()}", flush=True)
    
    # Wait for initial depth map
    print("Waiting for initial screen capture...", flush=True)
    wait_count = 0
    while depth_map is None:
        time.sleep(0.1)
        wait_count += 1
        if wait_count % 10 == 0:
            print(f"Still waiting... ({wait_count/10:.0f}s)", flush=True)
    
    #print(f"Screen captured at target {int(1/CAPTURE_INTERVAL)} FPS", flush=True)
    print(f"Screen size: {screen_size}", flush=True)
    
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
    
    print("\nReal-time screen depth map feedback running. Press Ctrl+C to exit.")
    print("Listening for mouse commands (header 0x01)...")
    print("Global mouse position tracked for elevation commands.")
    
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
            
            # Get current mouse position and send elevation command
            current_pos = pyautogui.position()
            
            # Always send elevation command (updates with screen capture rate)
            elevation = get_elevation_at_position(current_pos[0], current_pos[1])
            
            if elevation is not None:
                # Send elevation command
                pkt = core.create_packet(0x10)
                pkt.write_float(elevation)
                core.send_packet(pkt)
            
            time.sleep(MOUSE_CHECK_INTERVAL)  # Check mouse position frequently
    except KeyboardInterrupt:
        print("\n\nShutting down real-time screen depth map feedback test...")
        core.clear_header_handler(0x01)
        core.clear_header_handler(0x02)
        uart.close()
        print("Closed.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
