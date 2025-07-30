# WiFi Tester Module

This IoT Edge module scans for available WiFi networks and reports the results to IoT Hub. It works within the container without requiring host networking mode.

## Features

- **WiFi Network Scanning**: Automatically discovers and scans available WiFi networks
- **Network Interface Detection**: Identifies wireless network interfaces
- **Comprehensive Network Data**: Captures SSID, signal strength, encryption status, channel, frequency, and more
- **Periodic Scanning**: Performs scans every 30 seconds by default
- **IoT Hub Integration**: Sends scan results as structured JSON messages to IoT Hub
- **Multi-Architecture Support**: Works on AMD64, ARM32v7, and ARM64v8 architectures

## How It Works

The module uses the `iwlist` command-line tool to scan for WiFi networks. It:

1. **Discovers WiFi Interfaces**: Uses `psutil` to find network interfaces and checks for wireless capabilities
2. **Scans Networks**: Executes `iwlist <interface> scan` to discover nearby networks
3. **Parses Results**: Extracts network information including:
   - SSID (network name)
   - MAC address
   - Signal strength/quality
   - Channel and frequency
   - Encryption status
   - Network mode
4. **Sends Data**: Formats results as JSON and sends to IoT Hub via the module's output

## Message Format

### Network Information Message
Sent once at startup:
```json
{
  "timestamp": "2024-01-01T12:00:00.000000",
  "wifi_interfaces": ["wlan0", "wlan1"],
  "all_interfaces": ["eth0", "wlan0", "lo"],
  "interface_details": {
    "wlan0": {
      "is_up": true,
      "speed": 1000,
      "mtu": 1500
    }
  }
}
```

### WiFi Scan Message
Sent every 30 seconds:
```json
{
  "timestamp": "2024-01-01T12:00:00.000000",
  "scan_count": 1,
  "networks_found": 5,
  "networks": [
    {
      "ssid": "MyWiFi",
      "mac_address": "00:11:22:33:44:55",
      "channel": 6,
      "frequency": 2.437,
      "quality": 85,
      "max_quality": 100,
      "encrypted": true,
      "mode": "Master"
    }
  ]
}
```

## Configuration

The module requires no additional configuration. It will:

- Automatically discover available WiFi interfaces
- Use the first available WiFi interface for scanning
- Scan every 30 seconds
- Send results to the `output1` endpoint

## Requirements

### System Dependencies
- `wireless-tools` - Provides `iwlist` command
- `iw` - Modern wireless tools (backup)

### Python Dependencies
- `azure-iot-device~=2.7.0` - IoT Hub client
- `psutil==5.9.5` - System and process utilities

## Deployment

The module is ready to deploy to IoT Edge. It includes Dockerfiles for multiple architectures:

- `Dockerfile.amd64` - For x86_64 systems
- `Dockerfile.arm32v7` - For ARM 32-bit systems
- `Dockerfile.arm64v8` - For ARM 64-bit systems

## Troubleshooting

### No WiFi Networks Found
1. Check if the device has WiFi capabilities
2. Verify the WiFi interface is up: `ip link show wlan0`
3. Check if the interface is in monitor mode or managed mode
4. Ensure the container has access to the WiFi hardware

### Permission Errors
The module may need elevated privileges to access WiFi hardware. Consider:
- Running the container with `--privileged` flag
- Adding specific capabilities: `--cap-add=NET_ADMIN --cap-add=NET_RAW`

### Scan Timeouts
If scans are timing out:
- The WiFi interface might be busy
- Too many networks in range
- Hardware limitations

## Security Considerations

- The module only scans for networks and does not attempt to connect
- No sensitive network credentials are stored or transmitted
- Scan results are sent to IoT Hub and should be handled securely
- Consider implementing message filtering if sensitive network information is detected

## Customization

To modify the scanning behavior:

1. **Change scan interval**: Modify the `await asyncio.sleep(30)` value in `run_sample()`
2. **Add specific interface**: Pass interface name to `scan_wifi_networks(interface_name)`
3. **Filter networks**: Add filtering logic in `_parse_iwlist_output()`
4. **Custom message format**: Modify the JSON structure in the scan data creation 