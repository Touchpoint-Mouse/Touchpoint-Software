import logHandler
import controlTypes

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