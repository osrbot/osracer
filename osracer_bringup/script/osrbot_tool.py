import serial
import serial.tools.list_ports
import threading
import time
import sys

class SimpleSerialFilter:
    def __init__(self):
        self.ser = None
        self.running = False
        self.filter_prefixes = ['i', 'r', 'o', 'm']  # Filter lines starting with i, r, o
        
    def list_ports(self):
        """List all available serial ports"""
        ports = serial.tools.list_ports.comports()
        if not ports:
            print("No serial ports found")
            return []
        
        print("\nAvailable serial ports:")
        for i, port in enumerate(ports, 1):
            print(f"{i}. {port.device} - {port.description}")
        return ports
    
    def connect(self, port, baudrate=115200):
        """Connect to serial port"""
        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1
            )
            print(f"Connected to: {port}, Baudrate: {baudrate}")
            print(f"Filter rules: Filter lines starting with i, r, o.")
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False
    
    def start_reading(self):
        """Start reading serial data"""
        if not self.ser or not self.ser.is_open:
            print("Serial port not connected")
            return
        
        self.running = True
        thread = threading.Thread(target=self._read_serial)
        thread.daemon = True
        thread.start()
        print("Started reading serial data... (Press Ctrl+C to stop)")
    
    def _read_serial(self):
        """Internal method for reading serial data"""
        buffer = ""
        
        while self.running and self.ser and self.ser.is_open:
            try:
                if self.ser.in_waiting > 0:
                    # Read data
                    raw_data = self.ser.read(self.ser.in_waiting)
                    
                    # Try UTF-8 decoding, fallback to hex
                    try:
                        data = raw_data.decode('utf-8')
                    except:
                        data = raw_data.hex()
                    
                    buffer += data
                    
                    # Split by lines for processing
                    lines = buffer.split('\n')
                    buffer = lines[-1]  # Keep incomplete line
                    
                    # Process each line
                    for line in lines[:-1]:
                        line = line.strip('\r').strip()
                        if line:
                            self._process_line(line)
                
                time.sleep(0.01)
                
            except Exception as e:
                if self.running:
                    print(f"Read error: {e}")
                time.sleep(0.1)
    
    def _process_line(self, line):
        """Process a single line of data"""
        # Check if filtering is needed
        should_filter = False
        for prefix in self.filter_prefixes:
            if line.startswith(prefix):
                should_filter = True
                break
        
        # Display if not filtered
        if not should_filter:
            print(line)
    
    def send_data(self, data):
        """Send data to serial port"""
        if not self.ser or not self.ser.is_open:
            print("Serial port not connected")
            return False
        
        try:
            if isinstance(data, str):
                if not data.endswith('\n'):
                    data += '\n'
                self.ser.write(data.encode())
            else:
                self.ser.write(data)
            
            print(f"Sent: {data.strip()}")
            return True
        except Exception as e:
            print(f"Send failed: {e}")
            return False
    
    def disconnect(self):
        """Disconnect serial port"""
        self.running = False
        time.sleep(0.1)
        
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("Serial port disconnected")

def main():
    debugger = SimpleSerialFilter()
    
    print("=" * 40)
    print("OSRBOT Debug Assistant")
    print("Baudrate: 115200")
    print("=" * 40)
    
    # Show available ports
    ports = debugger.list_ports()
    if not ports:
        return
    
    # Select port
    while True:
        try:
            choice = input("\nSelect serial port number (1, 2, 3... or enter port name): ").strip()
            
            if choice.isdigit():
                index = int(choice) - 1
                if 0 <= index < len(ports):
                    port = ports[index].device
                    break
                else:
                    print(f"Invalid selection, please enter a number between 1-{len(ports)}")
            elif choice:  # User directly entered port name
                port = choice
                break
            else:
                print("Please enter port number or name")
                
        except ValueError:
            print("Please enter a valid number")
            continue
    
    # Connect to serial port, baudrate fixed at 115200
    if not debugger.connect(port, 115200):
        return
    
    print("\n" + "=" * 40)
    print("v vx steering    : Set linear velocity (m/s) and steering angle (deg)")
    print("kp value         : Set proportional coefficient")
    print("ki value         : Set integral coefficient")
    print("kd value         : Set derivative coefficient")
    print("pid              : Query PID parameters")
    print("status           : Query status")
    print("help             : Display help")
    print("=" * 40)
    print("Instructions:")
    print("  Enter text and press Enter - Send data to serial port")
    print("  Enter 'exit' or 'quit' - Exit program")
    print("=" * 40 + "\n")
    
    # Start reading data
    debugger.start_reading()
    
    # Command processing loop
    try:
        while True:
            try:
                # Read user input
                user_input = input()
                
                if user_input.lower() in ['exit', 'quit']:
                    break
                
                # Send data if not exit command
                if user_input:
                    debugger.send_data(user_input)
                    
            except KeyboardInterrupt:
                print("\nInterrupt received, enter 'exit' to quit")
                continue
            except Exception as e:
                print(f"Error: {e}")
                
    finally:
        debugger.disconnect()
        print("Program exited")

if __name__ == "__main__":
    main()