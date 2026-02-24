import traceback

import logHandler
import controlTypes
import winUser
import ctypes
from .dependencies import np

class Rect:
    """Simple rectangle class to represent object locations.
    """
    def __init__(self, left, top, right=None, bottom=None, width=None, height=None):
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
    
    def top_left(self):
        """Get the top-left corner coordinates."""
        return (self.left, self.top)
    
    def bottom_right(self):
        """Get the bottom-right corner coordinates."""
        return (self.right, self.bottom)
    
    def shape(self):
        """Get the shape of the rectangle as (width, height)."""
        return (self.width, self.height)
        
    def global_to_local(self, global_point):
        """Get rectangle coordinates relative to a global point (e.g., screen origin)."""
        return Rect(
            self.left - global_point[0],
            self.top - global_point[1],
            self.right - global_point[0],
            self.bottom - global_point[1]
        )
        
    def local_to_global(self, local_point):
        """Get rectangle coordinates relative to a local point (e.g., window origin)."""
        return Rect(
            self.left + local_point[0],
            self.top + local_point[1],
            self.right + local_point[0],
            self.bottom + local_point[1]
        )
        
    def pad(self, padding):
        """Expand the rectangle by a certain padding on all sides."""
        return Rect(
            self.left - padding,
            self.top - padding,
            self.right + padding,
            self.bottom + padding
        )
        
    def __repr__(self):
        return f"Rect({self.left}, {self.top}, {self.right}, {self.bottom})"
    
    def __eq__(self, other):
        """Check if two rectangles are equal by comparing their coordinates."""
        if not isinstance(other, Rect):
            return False
        return (self.left == other.left and
                self.top == other.top and
                self.right == other.right and
                self.bottom == other.bottom)
    
    def __hash__(self):
        """Make Rect hashable so it can be used in sets and as dict keys."""
        return hash((self.left, self.top, self.right, self.bottom))

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

def get_actual_border_mask(rect, image_shape):
    """Generate a boolean mask for only the actual borders of a rectangle that fall within image bounds.
    
    This does not create artificial borders when the rectangle extends beyond the image.
    Only marks pixels that are on the rectangle's actual border lines.
    
    Args:
        rect: Rect object defining the area to mask (can extend beyond image bounds)
        image_shape: Tuple (height, width) of the image
    Returns:
        np.ndarray: Boolean mask with True for pixels on the actual border that are visible
    """
    mask = np.zeros(image_shape, dtype=bool)
    img_height, img_width = image_shape
    
    # Check each border independently
    # Left border (x = rect.left)
    if 0 <= rect.left < img_width:
        y_start = max(0, rect.top)
        y_end = min(img_height, rect.bottom)
        if y_start < y_end:
            mask[y_start:y_end, rect.left] = True
    
    # Right border (x = rect.right - 1)
    if 0 <= rect.right - 1 < img_width and rect.right > 0:
        y_start = max(0, rect.top)
        y_end = min(img_height, rect.bottom)
        if y_start < y_end:
            mask[y_start:y_end, rect.right - 1] = True
    
    # Top border (y = rect.top)
    if 0 <= rect.top < img_height:
        x_start = max(0, rect.left)
        x_end = min(img_width, rect.right)
        if x_start < x_end:
            mask[rect.top, x_start:x_end] = True
    
    # Bottom border (y = rect.bottom - 1)
    if 0 <= rect.bottom - 1 < img_height and rect.bottom > 0:
        x_start = max(0, rect.left)
        x_end = min(img_width, rect.right)
        if x_start < x_end:
            mask[rect.bottom - 1, x_start:x_end] = True
    
    return mask
        

def get_window_z_orders(hwnds):
    """Get absolute z-orders for a set of tracked windows, including child windows recursively.
    Args:
        hwnds: Iterable of window handles to get absolute z-orders for
    Returns:
        dict: {hwnd -> absolute_z_order} where 0 = frontmost tracked window
    """
    import ctypes
    from ctypes import wintypes

    try:
        tracked_hwnds = set(hwnds)
        if not tracked_hwnds:
            return {}

        z_order_map = {hwnd: 999 for hwnd in tracked_hwnds}
        user32 = ctypes.windll.user32
        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        # Use a mutable object to hold the counter so it can be updated in recursion
        index = [0]

        def enum_child_windows(hwnd_parent):
            def child_callback(hwnd, lParam):
                enum_child_windows(hwnd)  # Recurse into children
                if hwnd in tracked_hwnds and z_order_map[hwnd] == 999:
                    z_order_map[hwnd] = index[0]
                index[0] += 1
                return True
            user32.EnumChildWindows(hwnd_parent, EnumWindowsProc(child_callback), 0)

        def enum_windows_callback(hwnd, lParam):
            enum_child_windows(hwnd)
            if hwnd in tracked_hwnds and z_order_map[hwnd] == 999:
                z_order_map[hwnd] = index[0]
            index[0] += 1
            return True

        try:
            user32.EnumWindows(EnumWindowsProc(enum_windows_callback), 0)
        except Exception as e:
            logMessage(f"[get_absolute_z_orders] EnumWindows/EnumChildWindows failed: {e}")
            return {hwnd: 999 for hwnd in tracked_hwnds}

        # Log results for debugging
        missing = [hwnd for hwnd in tracked_hwnds if z_order_map[hwnd] == 999]
        logMessage(f"[get_absolute_z_orders] Found {len(tracked_hwnds)-len(missing)}/{len(tracked_hwnds)} tracked windows (including children). Missing: {missing[:5]} (z-order 0 means topmost)")
        return z_order_map
    except Exception as e:
        logMessage(f"[get_absolute_z_orders] Error: {e}")
        logMessage(f"[get_absolute_z_orders] Traceback: {traceback.format_exc()}")
    return {hwnd: 999 for hwnd in hwnds}

