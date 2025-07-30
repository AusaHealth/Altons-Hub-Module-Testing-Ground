# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.

import sys
import signal
import threading
import subprocess
import json
import re
import time
import psutil
from datetime import datetime


# Event indicating client stop
stop_event = threading.Event()


class WiFiScanner:
    """Class to handle WiFi network scanning functionality"""
    
    def __init__(self):
        self.wifi_interfaces = []
        self._discover_wifi_interfaces()
    
    def _discover_wifi_interfaces(self):
        """Discover available WiFi interfaces"""
        try:
            # Get network interfaces
            net_if_addrs = psutil.net_if_addrs()
            
            # Look for wireless interfaces
            for interface_name in net_if_addrs.keys():
                if self._is_wireless_interface(interface_name):
                    self.wifi_interfaces.append(interface_name)
            
            print(f"Discovered WiFi interfaces: {self.wifi_interfaces}")
            
        except Exception as e:
            print(f"Error discovering WiFi interfaces: {e}")
    
    def _is_wireless_interface(self, interface_name):
        """Check if interface is wireless"""
        try:
            # Check if interface exists in /sys/class/net
            result = subprocess.run(
                ['test', '-d', f'/sys/class/net/{interface_name}/wireless'],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def scan_wifi_networks(self, interface_name=None):
        """Scan for WiFi networks using iwlist command"""
        networks = []
        
        try:
            # If no interface specified, try the first available one
            if interface_name is None and self.wifi_interfaces:
                interface_name = self.wifi_interfaces[0]
            elif interface_name is None:
                print("No WiFi interfaces available")
                return networks
            
            print(f"Scanning WiFi networks on interface: {interface_name}")
            
            # Run iwlist scan command
            result = subprocess.run(
                ['iwlist', interface_name, 'scan'],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                networks = self._parse_iwlist_output(result.stdout)
            else:
                print(f"iwlist scan failed: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            print("WiFi scan timed out")
        except Exception as e:
            print(f"Error scanning WiFi networks: {e}")
        
        return networks
    
    def _parse_iwlist_output(self, output):
        """Parse the output from iwlist command"""
        networks = []
        current_network = {}
        
        # Split output into lines
        lines = output.split('\n')
        
        for line in lines:
            line = line.strip()
            
            # Start of a new cell
            if 'Cell' in line and 'Address' in line:
                if current_network:
                    networks.append(current_network)
                current_network = {}
                
                # Extract MAC address
                mac_match = re.search(r'Address: ([0-9A-Fa-f:]+)', line)
                if mac_match:
                    current_network['mac_address'] = mac_match.group(1)
            
            # ESSID (network name)
            elif 'ESSID' in line:
                essid_match = re.search(r'ESSID:"([^"]*)"', line)
                if essid_match:
                    current_network['ssid'] = essid_match.group(1)
            
            # Channel
            elif 'Channel' in line:
                channel_match = re.search(r'Channel:(\d+)', line)
                if channel_match:
                    current_network['channel'] = int(channel_match.group(1))
            
            # Frequency
            elif 'Frequency' in line:
                freq_match = re.search(r'Frequency:([\d.]+)', line)
                if freq_match:
                    current_network['frequency'] = float(freq_match.group(1))
            
            # Quality/Signal strength
            elif 'Quality' in line:
                quality_match = re.search(r'Quality=(\d+)/(\d+)', line)
                if quality_match:
                    current_network['quality'] = int(quality_match.group(1))
                    current_network['max_quality'] = int(quality_match.group(2))
            
            # Encryption
            elif 'Encryption key' in line:
                encryption_match = re.search(r'Encryption key:(on|off)', line)
                if encryption_match:
                    current_network['encrypted'] = encryption_match.group(1) == 'on'
            
            # Mode
            elif 'Mode' in line:
                mode_match = re.search(r'Mode:([A-Za-z]+)', line)
                if mode_match:
                    current_network['mode'] = mode_match.group(1)
        
        # Add the last network
        if current_network:
            networks.append(current_network)
        
        return networks
    
    def get_network_info(self):
        """Get basic network information"""
        try:
            # Get network interfaces info
            net_if_addrs = psutil.net_if_addrs()
            net_if_stats = psutil.net_if_stats()
            
            network_info = {
                'timestamp': datetime.utcnow().isoformat(),
                'wifi_interfaces': self.wifi_interfaces,
                'all_interfaces': list(net_if_addrs.keys()),
                'interface_details': {}
            }
            
            for interface_name in net_if_addrs.keys():
                if interface_name in net_if_stats:
                    stats = net_if_stats[interface_name]
                    network_info['interface_details'][interface_name] = {
                        'is_up': stats.isup,
                        'speed': stats.speed,
                        'mtu': stats.mtu
                    }
            
            return network_info
            
        except Exception as e:
            print(f"Error getting network info: {e}")
            return {}


def run_wifi_scanner():
    """Main function that runs WiFi scanning periodically"""
    wifi_scanner = WiFiScanner()
    
    print("WiFi Tester module started")
    print(f"Available WiFi interfaces: {wifi_scanner.wifi_interfaces}")
    
    # Log initial network info
    network_info = wifi_scanner.get_network_info()
    if network_info:
        print("Initial network information:")
        print(json.dumps(network_info, indent=2))
    
    scan_count = 0
    while not stop_event.is_set():
        try:
            scan_count += 1
            print(f"\n--- WiFi Scan #{scan_count} ---")
            
            # Scan for WiFi networks
            networks = wifi_scanner.scan_wifi_networks()
            
            if networks:
                print(f"Found {len(networks)} WiFi networks")
                
                # Print detailed information for each network
                for i, network in enumerate(networks):
                    ssid = network.get('ssid', 'Hidden')
                    mac = network.get('mac_address', 'Unknown')
                    signal = network.get('quality', 'Unknown')
                    encrypted = network.get('encrypted', False)
                    channel = network.get('channel', 'Unknown')
                    frequency = network.get('frequency', 'Unknown')
                    
                    print(f"  {i+1}. {ssid}")
                    print(f"      MAC: {mac}")
                    print(f"      Signal: {signal}")
                    print(f"      Channel: {channel}")
                    print(f"      Frequency: {frequency} GHz")
                    print(f"      Encrypted: {encrypted}")
                    print()
            else:
                print("No WiFi networks found")
            
            # Wait before next scan (30 seconds)
            time.sleep(30)
            
        except Exception as e:
            print(f"Error in WiFi scanning loop: {e}")
            time.sleep(60)  # Wait longer on error


def main():
    if not sys.version >= "3.5.3":
        raise Exception( "The sample requires python 3.5.3+. Current version of Python: %s" % sys.version )
    print ( "WiFi Scanner for Python" )

    # Define a handler to cleanup when module is terminated
    def module_termination_handler(signal, frame):
        print ("WiFi Scanner stopped")
        stop_event.set()

    # Set the termination handler
    signal.signal(signal.SIGTERM, module_termination_handler)

    # Run the WiFi scanner
    try:
        run_wifi_scanner()
    except Exception as e:
        print("Unexpected error %s " % e)
        raise
    finally:
        print("Shutting down WiFi Scanner...")


if __name__ == "__main__":
    main()
