import controlTypes
import re
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
    IMAGE_REGEX = r'(image|graphic|picture|video|map|chart|graph|photo|snapshot|screenshot)'
    
    def checkString(self, string):
        """Check if the given string matches image-related patterns.
        
        Args:
            string: The string to check.
        Returns:
            bool: True if the string matches image-related patterns, False otherwise.
        """
        if not string:
            return False
        return re.search(self.IMAGE_REGEX, string, re.IGNORECASE) is not None
    
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
        
        role = obj.role if hasattr(obj, 'role') else None
        
        # Check if role is image related
        if role in self.IMAGE_ROLES:
            return True
        
        # Also check IAccessible and IAccessible2 attributes if available
        ia_attrs = None
        if hasattr(obj, 'IA2Attributes'):
            ia_attrs = obj.IA2Attributes
        elif hasattr(obj, 'IAccessibleObject') and hasattr(obj.IAccessibleObject, 'attributes'):
            ia_attrs = obj.IAccessibleObject.attributes
            
        if ia_attrs:
            # Attributes can be a dict or a string
            if isinstance(ia_attrs, dict):
                # Check all values in the dict
                for value in ia_attrs.values():
                    if self.checkString(value):
                        return True
            elif isinstance(ia_attrs, str) and self.checkString(ia_attrs):
                return True
        return False