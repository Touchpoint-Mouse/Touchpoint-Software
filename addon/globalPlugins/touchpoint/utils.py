import logHandler
import controlTypes
import winUser
import ctypes

class Rect:
    """Simple rectangle class to represent object locations.
    """
    def __init__(self, left, top, right, bottom, width=None, height=None):
        """Generates rectangle coordinates from either left/top/right/bottom (default) or left/top/width/height."""
        self.left = left
        self.top = top
        self.right = right if right is not None else left + width if width is not None else left
        self.bottom = bottom if bottom is not None else top + height if height is not None else top
        self.width = self.right - self.left
        self.height = self.bottom - self.top
        
    def copy(self):
        """Create a copy of this Rect."""
        return Rect(self.left, self.top, self.right, self.bottom)
    
    def area(self):
        """Calculate the area of the rectangle."""
        return max(0, self.width) * max(0, self.height)
    
    def perimeter(self):
        """Calculate the perimeter of the rectangle."""
        return 2 * (max(0, self.width) + max(0, self.height))
        
    def intersection(self, other):
        """Calculate the intersection of this rectangle with another.
        
        Args:
            other (Rect): Another rectangle to intersect with.
        
        Returns:
            Rect: A new Rect representing the intersection area, or None if no intersection.
        """
        left = max(self.left, other.left)
        top = max(self.top, other.top)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        
        if left < right and top < bottom:
            return Rect(left, top, right, bottom)
        return None
    
    def union(self, other):
        """Calculate the union of this rectangle with another.
        
        Args:
            other (Rect): Another rectangle to union with.
        
        Returns:
            Rect: A new Rect representing the union area.
        """
        left = min(self.left, other.left)
        top = min(self.top, other.top)
        right = max(self.right, other.right)
        bottom = max(self.bottom, other.bottom)
        
        return Rect(left, top, right, bottom)
    
    def iou(self, other):
        """Calculate the Intersection over Union (IoU) with another rectangle.
        
        Args:
            other (Rect): Another rectangle to compare with.
        
        Returns:
            float: IoU value between 0 and 1, or 0 if no intersection.
        """
        intersection = self.intersection(other)
        if not intersection:
            return 0.0
        
        intersection_area = intersection.area()
        union_area = self.area() + other.area() - intersection_area
        
        return intersection_area / union_area if union_area > 0 else 0.0
    
    def contains(self, other):
        """Check if this rectangle completely contains another.
        
        Args:
            other (Rect): Another rectangle to check.
        
        Returns:
            bool: True if this rectangle contains the other, False otherwise.
        """
        return (self.left <= other.left and
                self.top <= other.top and
                self.right >= other.right and
                self.bottom >= other.bottom)
        
    def intersects(self, other):
        """Check if this rectangle intersects with another.
        
        Args:
            other (Rect): Another rectangle to check.
        
        Returns:
            bool: True if the rectangles intersect, False otherwise.
        """
        return not (self.right <= other.left or
                    self.left >= other.right or
                    self.bottom <= other.top or
                    self.top >= other.bottom)
    
    def inside(self, other):
        """Check if this rectangle is completely inside another.
        
        Args:
            other (Rect): Another rectangle to check.
        
        Returns:
            bool: True if this rectangle is inside the other, False otherwise.
        """
        return (self.left >= other.left and
                self.top >= other.top and
                self.right <= other.right and
                self.bottom <= other.bottom)
        
    def __repr__(self):
        return f"Rect({self.left}, {self.top}, {self.right}, {self.bottom})"

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
            if z_order > 100:
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
        Rect object
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
            return Rect(rect.left, rect.top, rect.right, rect.bottom)
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
            check_rect = obj_location.copy()
        else:
            check_rect = get_window_rect(hwnd)
            if not check_rect:
                return False
            
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
            
            # Check if this window in front completely covers our region
            if (front_rect.contains(check_rect)):
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