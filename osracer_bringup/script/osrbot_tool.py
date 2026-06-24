import serial
import serial.tools.list_ports
import threading
import time
import sys

class SimpleSerialFilter:
    def __init__(self):
        self.ser = None
        self.running = False
        # Filter all high-frequency telemetry frames (i/r/o/m/b/s), only show command responses and status messages
        self.filter_prefixes = ['i ', 'r ', 'm', 'o', 'b', 's ']

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

    def connect(self, port, baudrate=460800):
        """Connect to serial port, default baudrate is 460800 (recommended by protocol)"""
        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1
            )
            print(f"Connected to: {port}, baudrate: {baudrate}")
            print("Filter rule: filter high-frequency telemetry frames (i/r/o/m/b/s), show command responses and status messages")
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    def start_reading(self):
        """Start serial reading thread"""
        if not self.ser or not self.ser.is_open:
            print("Serial port is not connected")
            return

        self.running = True
        thread = threading.Thread(target=self._read_serial)
        thread.daemon = True
        thread.start()
        print("Started reading serial data... (Press Ctrl+C to stop)")

    def _read_serial(self):
        """Internal reading thread"""
        buffer = ""

        while self.running and self.ser and self.ser.is_open:
            try:
                if self.ser.in_waiting > 0:
                    raw_data = self.ser.read(self.ser.in_waiting)
                    try:
                        data = raw_data.decode('utf-8')
                    except:
                        data = raw_data.hex()

                    buffer += data
                    lines = buffer.split('\n')
                    buffer = lines[-1]

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
        """Process one line of data and decide whether to display it based on prefix"""
        should_filter = False
        for prefix in self.filter_prefixes:
            if line.startswith(prefix):
                should_filter = True
                break

        if not should_filter:
            print(line)

    def send_data(self, data):
        """Send data to serial port, automatically appending newline"""
        if not self.ser or not self.ser.is_open:
            print("Serial port is not connected")
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
    print("OSRACER Debug Assistant")
    print("Default baudrate: 460800 (recommended by protocol)")
    print("=" * 40)

    ports = debugger.list_ports()
    if not ports:
        return

    # Select port
    while True:
        try:
            choice = input("\nSelect serial port number (1,2,3...) or enter port name directly: ").strip()

            if choice.isdigit():
                index = int(choice) - 1
                if 0 <= index < len(ports):
                    port = ports[index].device
                    break
                else:
                    print(f"Invalid selection, please enter a number between 1 and {len(ports)}")
            elif choice:
                port = choice
                break
            else:
                print("Please enter a port number or name")

        except ValueError:
            print("Please enter a valid number")
            continue

    # Connect with fixed baudrate 460800
    if not debugger.connect(port, 460800):
        return

    print("\n" + "=" * 40)
    print("Command list:")
    print("  v <vx_m/s> <steer_deg>       Set linear velocity (m/s) and steering angle (deg)")
    print("  i                            Query single IMU frame")
    print("  b                            Query single battery voltage frame")
    print("  o                            Query single odometry frame")
    print("  m                            Query single magnetometer frame")
    print("  s                            Query single synchronized snapshot")
    print("  stream sync                  Enable default periodic telemetry (s/m/r/b)")
    print("  stream off                   Disable periodic telemetry")
    print("  stream legacy                Enable legacy periodic telemetry (i/o/m/r/b)")
    print("  sn get                       Query ESP32-S3 hardware serial number")
    print("  fw version                   Query firmware version and ProjectVer")
    print("  status                       Query speed, voltage, control source, IMU, heater, diagnostics and chassis calibration status")
    print("  mc cal [sec]                 Magnetometer calibration (default 30s, rotate 360 deg)")
    print("  mc set <12 floats>           Set magnetometer calibration parameters and save to NVS")
    print("  mc get                       Query current magnetometer calibration")
    print("  mc reset                     Reset magnetometer calibration to identity matrix")
    print("  level cal                    Trigger accelerometer level calibration")
    print("  level get                    Query level offset")
    print("  level reset                  Reset level calibration and run automatic calibration again")
    print("  odom get                     Query odometry status")
    print("  odom reset                   Reset odometry position and yaw zero point")
    print("  odom scale get               Query odometry scale")
    print("  odom scale set <value>       Set odometry scale (0.50~1.50) and save to NVS")
    print("  odom scale reset             Reset odometry scale to 1.0")
    print("  trim get                     Query steering center trim")
    print("  trim set <deg>               Set steering trim (-5.0~5.0 deg) and save to NVS")
    print("  trim reset                   Reset steering trim to 0 deg")
    print("  pid get                      Query PID parameters")
    print("  pid set <kp> <ki> <kd>       Set PID parameters and save to NVS")
    print("  pid reset                    Reset PID to firmware default values")
    print("  speed deadband get           Query throttle feed-forward deadband")
    print("  speed deadband set <us>      Set throttle feed-forward deadband and save to NVS")
    print("  speed deadband reset         Reset throttle feed-forward deadband")
    print("  fw begin <size> <sha256>     Start serial OTA update")
    print("  fw data <seq> <hex>          Send one OTA data packet")
    print("  fw end                       Verify and switch OTA partition, then reboot")
    print("  fw abort                     Abort OTA update")
    print("  fw status                    Query OTA status")
    print("  reset                        Restart controller")
    print("  help                         Show this help message")
    print("=" * 40)
    print("Usage:")
    print("  Enter a command and press Enter - send it to serial port")
    print("  Enter 'exit' or 'quit' - exit program")
    print("  All high-frequency telemetry frames (i/r/o/m/b/s) are automatically filtered and hidden")
    print("=" * 40 + "\n")

    debugger.start_reading()

    try:
        while True:
            try:
                user_input = input()

                if user_input.lower() in ['exit', 'quit']:
                    break

                if user_input:
                    debugger.send_data(user_input)

            except KeyboardInterrupt:
                print("\nInterrupt caught, enter 'exit' to quit")
                continue
            except Exception as e:
                print(f"Error: {e}")

    finally:
        debugger.disconnect()
        print("Program exited")

if __name__ == "__main__":
    main()
