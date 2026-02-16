import logHandler
import controlTypes
import winUser
import ctypes

def logMessage(message):
        """Log a message to the NVDA log.
        """
        logHandler.log.info(message)

def logUIElement(obj, eventName):
    """
    Log information about a UI element.
    
    Args:
        obj: The NVDA object
        eventName (str): The name of the event
    """
    try:
        name = obj.name if obj.name else "Unnamed"
        role = obj.role if hasattr(obj, 'role') else None
        
        # Get the human-readable role name from controlTypes
        if role is not None:
            try:
                roleName = controlTypes.Role(role).displayString
            except:
                # Fallback for older NVDA versions
                try:
                    roleName = controlTypes.roleLabels.get(role, f"Unknown({role})")
                except:
                    roleName = str(role)
        else:
            roleName = "Unknown"
        
        value = obj.value if hasattr(obj, 'value') and obj.value else ""
        states = obj.states if hasattr(obj, 'states') else set()
        location = obj.location if hasattr(obj, 'location') else None
        
        info = {
            'event': eventName,
            'name': name,
            'role': roleName,
            'value': value,
            'states': str(states),
            'location': location
        }
        
        logMessage(f"Event: {eventName} | Name: {name} | Role: {roleName}")
        
        # You can extend this to send data to external systems
        # For example: send to serial port, TCP socket, or save to file
        
    except Exception as e:
        logMessage(f"Error logging UI element: {str(e)}")

def get_window_z_order(hwnd):
    """Get the z-order position of a window (0 = topmost).
    
    Args:
        hwnd: Window handle (HWND)
    
    Returns:
        int: Z-order position (0 = topmost, higher values = behind others)
        Returns -1 if window not found or error occurs
    """
    try:
        if not hwnd:
            return -1
        
        # GW_HWNDPREV = 3 (get window above this one in z-order)
        GW_HWNDPREV = 3
        
        z_order = 0
        current_hwnd = hwnd
        
        # Count how many windows are above this one
        while True:
            prev_hwnd = winUser.getWindow(current_hwnd, GW_HWNDPREV)
            if not prev_hwnd:
                break
            z_order += 1
            current_hwnd = prev_hwnd
            
            # Prevent infinite loop (safety check)
            if z_order > 1000:
                return -1
        
        return z_order
    except Exception as e:
        logMessage(f"Error getting window z-order: {e}")
        return -1

def compare_window_z_order(hwnd1, hwnd2):
    """Compare z-order of two windows.
    
    Args:
        hwnd1: First window handle
        hwnd2: Second window handle
    
    Returns:
        int: -1 if hwnd1 is above hwnd2, 1 if hwnd1 is below hwnd2, 0 if same or error
    """
    try:
        if not hwnd1 or not hwnd2:
            return 0
        
        if hwnd1 == hwnd2:
            return 0
        
        z1 = get_window_z_order(hwnd1)
        z2 = get_window_z_order(hwnd2)
        
        if z1 < 0 or z2 < 0:
            return 0
        
        if z1 < z2:
            return -1  # hwnd1 is above (closer to front)
        elif z1 > z2:
            return 1   # hwnd1 is below (farther from front)
        else:
            return 0
    except Exception as e:
        logMessage(f"Error comparing window z-order: {e}")
        return 0

def get_object_window_handle(obj):
    """Get the window handle from an NVDA object.
    
    Args:
        obj: NVDA object
    
    Returns:
        int: Window handle (HWND) or None if not available
    """
    try:
        # Try direct windowHandle attribute
        if hasattr(obj, 'windowHandle') and obj.windowHandle:
            return obj.windowHandle
        
        # Try going up to parent window
        current = obj
        depth = 0
        while current and depth < 50:  # Prevent infinite loop
            if hasattr(current, 'windowHandle') and current.windowHandle:
                return current.windowHandle
            current = current.parent if hasattr(current, 'parent') else None
            depth += 1
        
        return None
    except Exception as e:
        logMessage(f"Error getting object window handle: {e}")
        return None

def get_window_rect(hwnd):
    """Get the window rectangle.
    
    Args:
        hwnd: Window handle (HWND)
    
    Returns:
        tuple: (left, top, right, bottom) or None if error
    """
    try:
        if not hwnd:
            return None
        
        # Use ctypes to call GetWindowRect from user32.dll
        class RECT(ctypes.Structure):
            _fields_ = [('left', ctypes.c_long),
                       ('top', ctypes.c_long),
                       ('right', ctypes.c_long),
                       ('bottom', ctypes.c_long)]
        
        rect = RECT()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return (rect.left, rect.top, rect.right, rect.bottom)
        return None
    except Exception as e:
        logMessage(f"Error getting window rect: {e}")
        return None

def is_window_occluded(hwnd, obj_location=None):
    """Check if a window (or object region within it) is completely occluded by windows in front.
    
    Args:
        hwnd: Window handle to check
        obj_location: Optional object location namedtuple to check specific region
    
    Returns:
        bool: True if completely occluded, False otherwise
    """
    try:
        if not hwnd:
            return False
        
        # Get the region to check (either object location or full window)
        if obj_location:
            check_left = obj_location.left
            check_top = obj_location.top
            check_right = obj_location.left + obj_location.width
            check_bottom = obj_location.top + obj_location.height
        else:
            rect = get_window_rect(hwnd)
            if not rect:
                return False
            check_left, check_top, check_right, check_bottom = rect
        
        # Get z-order of this window
        z_order = get_window_z_order(hwnd)
        if z_order < 0:
            return False
        
        # GW_HWNDPREV = 3 (get window above this one in z-order)
        GW_HWNDPREV = 3
        
        # Check all windows in front (lower z-order)
        current_hwnd = hwnd
        for _ in range(z_order):  # Only check windows in front
            prev_hwnd = winUser.getWindow(current_hwnd, GW_HWNDPREV)
            if not prev_hwnd:
                break
            
            # Get the rect of the window in front
            front_rect = get_window_rect(prev_hwnd)
            if not front_rect:
                current_hwnd = prev_hwnd
                continue
            
            front_left, front_top, front_right, front_bottom = front_rect
            
            # Check if this window in front completely covers our region
            if (front_left <= check_left and 
                front_top <= check_top and 
                front_right >= check_right and 
                front_bottom >= check_bottom):
                # This window completely covers our region
                # Check if it's visible (not minimized)
                try:
                    if winUser.isWindowVisible(prev_hwnd):
                        return True
                except:
                    pass
            
            current_hwnd = prev_hwnd
        
        return False
    except Exception as e:
        logMessage(f"Error checking window occlusion: {e}")
        return False