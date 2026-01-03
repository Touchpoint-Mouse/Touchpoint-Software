# -*- coding: utf-8 -*-
# Touchpoint NVDA Global Plugin
# Captures UI element events for the Touchpoint project

import globalPluginHandler
import api
import ui
import eventHandler
import controlTypes
import NVDAObjects
import logHandler


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    """
    Global plugin that monitors and logs NVDA UI element events.
    This plugin captures various events like focus changes, mouse movements,
    and object state changes.
    """

    def __init__(self):
        """Initialize the global plugin."""
        super(GlobalPlugin, self).__init__()
        self.logMessage("Touchpoint NVDA addon initialized")

    def terminate(self):
        """Clean up when the plugin is terminated."""
        self.logMessage("Touchpoint NVDA addon terminated")
        super(GlobalPlugin, self).terminate()

    def logMessage(self, message):
        """Log a message to the NVDA log.
        """
        logHandler.log.info(message)

    def logUIElement(self, obj, eventName):
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
            
            self.logMessage(f"Event: {eventName} | Name: {name} | Role: {roleName}")
            
            # You can extend this to send data to external systems
            # For example: send to serial port, TCP socket, or save to file
            
        except Exception as e:
            self.logMessage(f"Error logging UI element: {str(e)}")

    # Event handlers for various UI events
    
    def event_gainFocus(self, obj, nextHandler):
        """
        Triggered when an object gains focus.
        
        Args:
            obj: The object that gained focus
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "gainFocus")
        nextHandler()

    def event_loseFocus(self, obj, nextHandler):
        """
        Triggered when an object loses focus.
        
        Args:
            obj: The object that lost focus
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "loseFocus")
        nextHandler()

    def event_foreground(self, obj, nextHandler):
        """
        Triggered when a window comes to the foreground.
        
        Args:
            obj: The window object
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "foreground")
        nextHandler()

    def event_nameChange(self, obj, nextHandler):
        """
        Triggered when an object's name changes.
        
        Args:
            obj: The object whose name changed
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "nameChange")
        nextHandler()

    def event_valueChange(self, obj, nextHandler):
        """
        Triggered when an object's value changes.
        
        Args:
            obj: The object whose value changed
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "valueChange")
        nextHandler()

    def event_stateChange(self, obj, nextHandler):
        """
        Triggered when an object's state changes.
        
        Args:
            obj: The object whose state changed
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "stateChange")
        nextHandler()

    def event_selection(self, obj, nextHandler):
        """
        Triggered when a selection is made.
        
        Args:
            obj: The selected object
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "selection")
        nextHandler()

    def event_mouseMove(self, obj, nextHandler, x=None, y=None):
        """
        Triggered when the mouse moves.
        
        Args:
            obj: The object under the mouse
            nextHandler: The next event handler in the chain
            x: Mouse X coordinate
            y: Mouse Y coordinate
        """
        # Note: This event can be very frequent, so you may want to filter it
        # Uncomment the line below to log mouse movements
        # self.logUIElement(obj, f"mouseMove (x={x}, y={y})")
        nextHandler()

    def event_typedCharacter(self, obj, nextHandler, ch=None):
        """
        Triggered when a character is typed.
        
        Args:
            obj: The object where the character was typed
            nextHandler: The next event handler in the chain
            ch: The typed character
        """
        self.logMessage(f"Typed character: {ch}")
        nextHandler()

    def event_caret(self, obj, nextHandler):
        """
        Triggered when the caret (text cursor) moves.
        
        Args:
            obj: The object containing the caret
            nextHandler: The next event handler in the chain
        """
        # This event is very frequent, uncomment to enable logging
        # self.logUIElement(obj, "caret")
        nextHandler()

    def event_menuStart(self, obj, nextHandler):
        """
        Triggered when a menu is opened.
        
        Args:
            obj: The menu object
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "menuStart")
        nextHandler()

    def event_menuEnd(self, obj, nextHandler):
        """
        Triggered when a menu is closed.
        
        Args:
            obj: The menu object
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "menuEnd")
        nextHandler()

    def event_alert(self, obj, nextHandler):
        """
        Triggered when an alert or notification appears.
        
        Args:
            obj: The alert object
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "alert")
        nextHandler()

    def event_documentLoadComplete(self, obj, nextHandler):
        """
        Triggered when a document finishes loading.
        
        Args:
            obj: The document object
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "documentLoadComplete")
        nextHandler()
