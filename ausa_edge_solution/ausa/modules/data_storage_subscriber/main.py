import json
import logging
import asyncio
import os
import fnmatch
import threading
import uuid
from typing import Callable, List, Tuple, Dict

from amqtt.client import MQTTClient, ClientException
from amqtt.mqtt.constants import QOS_0, QOS_1, QOS_2
from azure.iot.device.iothub.aio import IoTHubModuleClient
import pyrqlite.dbapi2 as dbapi2
import time


DATABASE_HOST = os.getenv("DATABASE_HOST", "localhost")
DATABASE_PORT = int(os.getenv("DATABASE_PORT", 4001))

# Setup logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s"
)

# Environment broker config
BROKER_HOST = os.getenv("BROKER_HOST", "localhost")

# Handler type: (pattern, qos, callback)
subscriptions: List[Tuple[str, int, Callable[[str, str], None]]] = []

class RqliteConnectionSingleton:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_connection(cls):
        with cls._lock:
            if cls._instance is None or not cls._is_connection_alive(cls._instance):
                logger.info(f"Creating rqlite connection to {DATABASE_HOST}:{DATABASE_PORT}")
                cls._instance = dbapi2.connect(host=DATABASE_HOST, port=DATABASE_PORT)
            return cls._instance

    @staticmethod
    def _is_connection_alive(conn):
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1;")
            cursor.fetchone()
            return True
        except Exception as e:
            logger.warning("Rqlite connection check failed: %s", e)
            return False

def register_handler(topic_pattern: str, qos: int, callback: Callable[[str, str], None]):
    subscriptions.append((topic_pattern, qos, callback))
    logger.debug(f"Registered handler for topic '{topic_pattern}' with QoS {qos}")


def wait_for_rqlite_ready(max_retries=10, delay=3):
    for attempt in range(max_retries):
        try:
            conn = dbapi2.connect(host=DATABASE_HOST, port=DATABASE_PORT)
            cursor = conn.cursor()
            cursor.execute("SELECT 1;")
            cursor.fetchone()
            conn.close()
            logger.info("Connected to rqlite.")
            return
        except Exception as e:
            logger.warning(f"rqlite not ready (attempt {attempt + 1}): {e}")
            time.sleep(delay * (attempt + 1))  # exponential backoff
    raise RuntimeError("rqlite not ready after multiple retries.")

def perform_migrations():
    logger.info("Waiting for rqlite before migrations...")
    wait_for_rqlite_ready()
    conn = RqliteConnectionSingleton.get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS vitals_data (
                        id TEXT PRIMARY KEY NOT NULL,
                        data TEXT,
                        synced BOOLEAN DEFAULT FALSE
                    )
                """)
        logger.info("Migration and seeding complete.")
    except Exception as e:
        logger.exception("Migration error: %s", e)
    finally:
        conn.close()

async def message_receiver():
    azure_iot_edge_client = IoTHubModuleClient.create_from_edge_environment()

    def twin_patch_handler(patch):
        logger.info(f"the data in the desired properties patch was:{patch}")

    azure_iot_edge_client.on_twin_desired_properties_patch_received = twin_patch_handler

    for key, value in os.environ.items():
        logger.info(f"{key}: {value}")
    client = MQTTClient()
    await client.connect(f"mqtt://{BROKER_HOST}:1883/")

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


def storage(topic,payload):
    logger.info(f"Received Payload:{payload} on topic {topic} ")
    conn = RqliteConnectionSingleton.get_connection()
    payload:Dict = json.loads(payload)
    response = None
    if payload is None or payload.keys() is None:
        response = json.dumps({
            "error":"No keys present"
        }).encode("utf-8")
    try:
        with conn.cursor() as cursor:
            for key in payload.keys():
                table_name = key
                value = payload[key]
                assert isinstance(value, list), f"Value for key '{key}' is not a list."

                for idx, item in enumerate(value):
                    assert isinstance(item, dict), f"Item at index {idx} in list for key '{key}' is not a dict."

                for row in value:
                    columns = ", ".join(row.keys())
                    placeholders = ", ".join(["?"] * len(row.keys()))
                    values = tuple(row.values())

                    query = f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"
                    cursor.execute(query, values)
            response = json.dumps({
                "status": "Successful"
            }).encode("utf-8")
    except Exception as e:
        logger.exception(f"An error occured while inserting to a table:{e}")
        response = json.dumps({
                "error": "Error occured while inserting to a table"
            }).encode("utf-8")
    finally:
        conn.close()
        return response

if __name__ == "__main__":
    perform_migrations()
    register_handler("storage", QOS_2, storage)
    try:
        asyncio.get_event_loop().run_until_complete(message_receiver())
    except KeyboardInterrupt:
        print("Subscriber stopped manually.")