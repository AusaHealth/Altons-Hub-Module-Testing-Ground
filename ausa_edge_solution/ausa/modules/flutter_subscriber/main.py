import logging
import asyncio
import os
import fnmatch
from typing import Callable, List, Tuple

from amqtt.client import MQTTClient, ClientException
from amqtt.mqtt.constants import QOS_0, QOS_1, QOS_2
from azure.iot.device import MethodResponse
from azure.iot.device.iothub.aio import IoTHubModuleClient


from handlers.handlers import authenticate_device, is_user_authenticated, get_users_from_twin, \
    api_request, authenticate_device_topic_handler, authenticate_user_topic_handler, \
    is_device_authenticated_topic_handler, is_user_authenticated_topic_handler, get_users_from_twin_topic_handler, \
    api_request_topic_handler,is_device_authenticated

# Setup logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s"
)

# Environment broker config
BROKER_HOST = os.getenv("HOST", "localhost")
BROKER_PORT = os.getenv("PORT",2883)

# Handler type: (pattern, qos, callback)
subscriptions: List[Tuple[str, int, Callable[[str, str], None]]] = []

method_handlers = {}


def register_handler_for_topic(topic_pattern: str, qos: int, callback: Callable[[str, str], None]):
    subscriptions.append((topic_pattern, qos, callback))
    logger.debug(f"Registered handler for topic '{topic_pattern}' with QoS {qos}")

def register_handler_direct_method(method_name, handler_func):
    """Register a method handler function"""
    method_handlers[method_name] = handler_func
    logger.info(f"Registered handler for method: {method_name}")


async def handle_method_request(module_client, method_request):
    """Generic method request handler that routes to registered handlers"""
    try:
        method_name = method_request.name

        if method_name in method_handlers:
            logger.info(f"Handling method: {method_name}")

            handler_func = method_handlers[method_name]
            result = await handler_func(module_client, method_request)

            method_response = MethodResponse.create_from_method_request(
                method_request, 200, result
            )
            await module_client.send_method_response(method_response)

        else:
            logger.warning(f"Unknown method: {method_name}")
            error_response = {"status": "error", "message": f"Unknown method: {method_name}"}
            method_response = MethodResponse.create_from_method_request(
                method_request, 404, error_response
            )
            await module_client.send_method_response(method_response)

    except Exception as e:
        logger.error(f"Error in handle_method_request: {e}")
        error_response = {"status": "error", "message": str(e)}
        method_response = MethodResponse.create_from_method_request(
            method_request, 500, error_response
        )
        await module_client.send_method_response(method_response)



async def message_receiver():
    azure_iot_edge_client = IoTHubModuleClient.create_from_edge_environment()

    main_loop = asyncio.get_running_loop()

    def method_request_handler(method_request):
        """Synchronous wrapper for async method handler"""
        asyncio.run_coroutine_threadsafe(handle_method_request(azure_iot_edge_client, method_request), main_loop)

    def twin_patch_handler(patch):
        logger.info(f"the data in the desired properties patch was:{patch}")
        if "deviceToken" in patch:
            os.environ["DEVICE_TOKEN"] = patch["deviceToken"]["token"]

    azure_iot_edge_client.on_twin_desired_properties_patch_received = twin_patch_handler

    for key, value in os.environ.items():
        logger.info(f"{key}: {value}")
    client = MQTTClient()
    await client.connect(f"mqtt://{BROKER_HOST}:{BROKER_PORT}/")

    # Build subscription list
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


# Run the MQTT subscriber
if __name__ == "__main__":
    handlers = {
        "authenticate_device": authenticate_device,
        "authenticate_user": authenticate_user,
        "is_device_authenticated": is_device_authenticated,
        "is_user_authenticated": is_user_authenticated,
        "get_users_from_twin": get_users_from_twin,
        "api_request": api_request,
    }

    # Topic handler wrappers
    topic_wrappers = {
        "authenticate_device": authenticate_device_topic_handler,
        "authenticate_user": authenticate_user_topic_handler,
        "is_device_authenticated": is_device_authenticated_topic_handler,
        "is_user_authenticated": is_user_authenticated_topic_handler,
        "get_users_from_twin": get_users_from_twin_topic_handler,
        "api_request": api_request_topic_handler,
    }

    # Unified registration
    for name, func in handlers.items():
        register_handler_direct_method(name, func)
        register_handler_for_topic(name, QOS_2, topic_wrappers[name])
    try:
        asyncio.get_event_loop().run_until_complete(message_receiver())
    except KeyboardInterrupt:
        print("Subscriber stopped manually.")
