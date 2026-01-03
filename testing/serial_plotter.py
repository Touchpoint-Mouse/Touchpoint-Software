import serial
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque

# Configuration
SERIAL_PORT = 'COM6'  # Change to your serial port (e.g., 'COM3' on Windows, '/dev/ttyUSB0' on Linux)
BAUD_RATE = 115200      # Change to match your device's baud rate
MAX_POINTS = 100      # Maximum number of points to display

# Data storage
data_points = deque(maxlen=MAX_POINTS)

# Initialize serial connection
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    print(f"Connected to {SERIAL_PORT} at {BAUD_RATE} baud")
except serial.SerialException as e:
    print(f"Error opening serial port: {e}")
    exit(1)

# Set up the plot
fig, ax = plt.subplots()
line, = ax.plot([], [], 'b-')
ax.set_xlabel('Sample')
ax.set_ylabel('Value')
ax.set_title('Real-time Serial Data Plot')
ax.grid(True)

def init():
    """Initialize the plot"""
    ax.set_xlim(0, MAX_POINTS)
    ax.set_ylim(-10, 10)  # Adjust these limits based on your expected data range
    return line,

def update(frame):
    """Update the plot with new data"""
    try:
        if ser.in_waiting > 0:
            # Read line from serial port
            line_data = ser.readline().decode('utf-8').strip()
            
            if line_data:
                try:
                    # Parse float value
                    value = float(line_data)
                    data_points.append(value)
                    print(f"Received: {value}")
                except ValueError:
                    print(f"Invalid data: {line_data}")
    except Exception as e:
        print(f"Error reading serial data: {e}")
    
    # Update plot data
    if data_points:
        x_data = list(range(len(data_points)))
        y_data = list(data_points)
        line.set_data(x_data, y_data)
        
        # Auto-scale y-axis
        if len(data_points) > 0:
            y_min = min(data_points)
            y_max = max(data_points)
            margin = (y_max - y_min) * 0.1 if y_max != y_min else 1
            ax.set_ylim(y_min - margin, y_max + margin)
        
        # Update x-axis
        ax.set_xlim(0, max(MAX_POINTS, len(data_points)))
    
    return line,

# Create animation
ani = animation.FuncAnimation(fig, update, init_func=init, 
                            interval=50, blit=False, cache_frame_data=False)

# Show plot
plt.tight_layout()
try:
    plt.show()
except KeyboardInterrupt:
    print("\nClosing...")
finally:
    ser.close()
    print("Serial port closed")
