import asyncio
import fnmatch
import json
import logging
import os
import time
from typing import Tuple, List, Callable

from amqtt.client import MQTTClient, ClientException
from amqtt.mqtt.constants import QOS_0, QOS_1, QOS_2
from azure.iot.device import IoTHubModuleClient
import bluetooth
from bleak import BleakScanner
import pywifi
from pywifi import const

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s"
)

BROKER_HOST = os.getenv("HOST", "localhost")

# Handler type: (pattern, qos, callback)
subscriptions: List[Tuple[str, int, Callable[[str, str], bytes]]] = []

def register_handler(topic_pattern: str, qos: int, callback: Callable[[str, str], bytes]):
    subscriptions.append((topic_pattern, qos, callback))
    logger.debug(f"Registered handler for topic '{topic_pattern}' with QoS {qos}")


async def message_receiver():
    azure_iot_edge_client = IoTHubModuleClient.create_from_edge_environment()

    def twin_patch_handler(patch):
        logger.info(f"the data in the desired properties patch was:{patch}")

    azure_iot_edge_client.on_twin_desired_properties_patch_received = twin_patch_handler

    client = MQTTClient()
    await client.connect(f"mqtt://{BROKER_HOST}:1883/")
    topic_qos_list = [(pattern, qos) for pattern, qos, _ in subscriptions]
    await client.subscribe(topic_qos_list)
    logger.info(f"Subscribed to topics: {topic_qos_list}")

    try:
        while True:
            message = await client.deliver_message()
            packet = message.publish_packet
            topic = packet.variable_header.topic_name
            payload = packet.payload.data.decode()
            logger.info(f"Received: {topic} => {payload}")

            matched = False
            for pattern, _, callback in subscriptions:
                if topic.endswith("/response"):
                    continue
                if fnmatch.fnmatchcase(topic, pattern.replace("#", "*").replace("+", "?")):
                    response = callback(topic, payload)
                    logger.info(f"Callback:{callback} and Response: {response}")
                    await client.publish(topic + "/response", response, QOS_2)
                    matched = True
            if not matched:
                logger.warning(f"No handler matched for topic: {topic}")
    except ClientException as ce:
        logger.error(f"Client exception: {ce}")
    except asyncio.CancelledError:
        logger.info("Subscriber coroutine cancelled.")
    finally:
        await client.unsubscribe([pattern for pattern, _, _ in subscriptions])
        await client.disconnect()
        logger.info("Disconnected and unsubscribed.")

def bluetooth_scan(topic, payload):
    ble_device = asyncio.run(BleakScanner.discover())
    bluetooth_classic = bluetooth.discover_devices(duration=8, lookup_names=True,flush_cache=True)
    return json.dumps({
        "ble_device": [
            (device.name, device.address) for device in ble_device],
        "bluetooth_classic": [(name, addr) for addr, name in bluetooth_classic]
    }).encode("utf-8")

def wifi_scan(topic:str,payload:str):
    wifi = pywifi.PyWiFi()
    iface = wifi.interfaces()[0]
    payload = json.dumps(payload)# Get first wireless interface

    iface.scan()  # Start scan
    time.sleep(2)  # Wait for scan to complete
    results = iface.scan_results()

    return json.dumps({
        "wifi_devices": [(network.ssid, network.bssid) for network in results]
    }).encode("utf-8")

import pywifi
import json
import time
from pywifi import const

def wifi_connect(topic: str, payload: str):
    wifi = pywifi.PyWiFi()
    iface = wifi.interfaces()[0]
    try:
        data = json.loads(payload)
        ssid = data.get("ssid")
        password = data.get("password")
        if not ssid or not password:
            return json.dumps({"status": "error", "message": "Missing ssid or password"})
    except json.JSONDecodeError:
        return json.dumps({"status": "error", "message": "Invalid JSON payload"}).encode("utf-8")
    iface.disconnect()
    time.sleep(1)
    if iface.status() != const.IFACE_DISCONNECTED:
        return json.dumps({"status": "error", "message": "Failed to disconnect before connecting"}).encode("utf-8")

    profile = pywifi.Profile()
    profile.ssid = ssid
    profile.auth = const.AUTH_ALG_OPEN
    profile.akm.append(const.AKM_TYPE_WPA2PSK)  # WPA2 PSK
    profile.cipher = const.CIPHER_TYPE_CCMP
    profile.key = password
    iface.remove_all_network_profiles()
    tmp_profile = iface.add_network_profile(profile)
    iface.connect(tmp_profile)
    start_time = time.time()
    timeout = 15  # seconds
    while time.time() - start_time < timeout:
        if iface.status() == const.IFACE_CONNECTED:
            return json.dumps({"status": "success", "message": f"Connected to {ssid}"}).encode("utf-8")
        time.sleep(1)

    return json.dumps({"status": "error", "message": f"Failed to connect to {ssid} within timeout"}).encode("utf-8")

def bluetooth_connect(topic: str, payload: str):
    return "".encode()




if __name__ == "__main__":
    register_handler("bluetooth/devices", QOS_2,bluetooth_scan)
    register_handler("wifi/devices",QOS_2,wifi_scan)
    register_handler("wifi/connect",QOS_2,wifi_connect)
    register_handler("bluetooth/connect",QOS_2,bluetooth_connect)
    try:
        asyncio.get_event_loop().run_until_complete(message_receiver())
    except KeyboardInterrupt:
        print("Subscriber stopped manually.")