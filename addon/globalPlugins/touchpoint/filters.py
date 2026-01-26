import controlTypes
from .utils import logMessage

class ObjectFilter:
    """Class to identify NVDA objects based on specific criteria."""
    def matches(self, plugin, obj):
        """Determine if the given NVDA object matches the filter criteria.
        
        Args:
            obj: The NVDA object to check.
        
        Returns:
            bool: True if the object matches the filter, False otherwise.
        """
        return True  # Default implementation matches all objects

class ComboObjectFilter(ObjectFilter):
    """Class to combine multiple ObjectFilters using include and exclude lists."""
    def __init__(self, include=[], exclude=[]):
        self.include = include
        self.exclude = exclude
        
    def matches(self, plugin, obj):
        """Check if the given NVDA object matches all filter criteria.
        
        Args:
            obj: The NVDA object to check.
        
        Returns:
            bool: True if the object matches all filters, False otherwise.
        """
        for filter in self.include:
            if not filter.matches(plugin, obj):
                return False
        for filter in self.exclude:
            if filter.matches(plugin, obj):
                return False
        return True
    
class GlobalFilter:
    def matches(self, plugin):
        """Check if the given global plugin matches the filter criteria.
        
        Args:
            plugin: The global plugin to check.
        """
        return True  # Default implementation matches all plugins
    
class ComboGlobalFilter(GlobalFilter):
    """Class to combine multiple GlobalFilters using include and exclude lists."""
    def __init__(self, include=[], exclude=[]):
        self.include = include
        self.exclude = exclude
        
    def matches(self, plugin):
        """Check if the given global plugin matches all filter criteria.
        
        Args:
            plugin: The global plugin to check.
        
        Returns:
            bool: True if the plugin matches all filters, False otherwise.
        """
        for filter in self.include:
            if not filter.matches(plugin):
                return False
        for filter in self.exclude:
            if filter.matches(plugin):
                return False
        return True
    
class GraphicFilter(ObjectFilter):
    IMAGE_ROLES = (controlTypes.Role.GRAPHIC, controlTypes.Role.IMAGEMAP)
    
    """Filter to identify graphic-related NVDA objects."""
    def matches(self, plugin, obj):
        """Check if the given NVDA object is a graphic (image or video).
        
        Args:
            obj: The NVDA object to check.
        
        Returns:
            bool: True if the object is a graphic, False otherwise.
        """
        if not obj:
            return False
        
        try:
            role = obj.role if hasattr(obj, 'role') else None
            if role is None:
                return False
            
            # Check if role is GRAPHIC or IMAGEMAP
            is_img = role in self.IMAGE_ROLES
            
            # Also check for video tags in IAccessible2 attributes
            if not is_img:
                # Try different attribute properties
                ia2_attrs = None
                if hasattr(obj, 'IA2Attributes'):
                    ia2_attrs = obj.IA2Attributes
                elif hasattr(obj, 'IAccessibleObject') and hasattr(obj.IAccessibleObject, 'attributes'):
                    ia2_attrs = obj.IAccessibleObject.attributes
                
                if ia2_attrs:
                    # IA2Attributes can be a dict or a string
                    if isinstance(ia2_attrs, dict):
                        if ia2_attrs.get('tag') == 'video':
                            is_img = True
                    elif isinstance(ia2_attrs, str):
                        if 'tag:video' in ia2_attrs:
                            is_img = True
            
            return is_img
        except Exception as e:
            logMessage(f"[DEBUG] Error checking if image: {e}")
            return False