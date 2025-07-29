import asyncio
import json
import os
import uuid
import threading
import logging
import time
from azure.iot.device.aio import IoTHubModuleClient
import pyrqlite.dbapi2 as dbapi2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

Tables = {
    "data_sync": {"columns": ["id","data"], "data_type": [], "interval": 10},
    "data_sync_1": {"columns": ["id", "data"], "data_type": [], "interval": 20}
}

HOST = os.getenv("HOST", "localhost")
PORT = int(os.getenv("PORT", 4001))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 10))

def wait_for_rqlite_ready(max_retries=10, delay=3):
    for attempt in range(max_retries):
        try:
            conn = dbapi2.connect(host=HOST, port=PORT)
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

class RqliteConnectionSingleton:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_connection(cls):
        with cls._lock:
            if cls._instance is None or not cls._is_connection_alive(cls._instance):
                logger.info(f"Creating rqlite connection to {HOST}:{PORT}")
                cls._instance = dbapi2.connect(host=HOST, port=PORT)
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

class IoTHubClientSingleton:
    _client = None

    @classmethod
    def get_client(cls, max_retries=5, delay=2):
        if cls._client is None:
            for attempt in range(max_retries):
                try:
                    cls._client = IoTHubModuleClient.create_from_edge_environment()
                    logger.info("IoTHub client connected")
                    return cls._client
                except Exception as e:
                    logger.warning(f"IoTHub connection failed (attempt {attempt + 1}): {e}")
                    time.sleep(delay * (attempt + 1))
            raise RuntimeError("IoTHub client connection failed after retries")
        return cls._client

    @classmethod
    def disconnect(cls):
        if cls._client:
            cls._client.disconnect()
            logger.info("IoTHub client disconnected")
            cls._client = None

def perform_migrations():
    logger.info("Waiting for rqlite before migrations...")
    wait_for_rqlite_ready()
    conn = RqliteConnectionSingleton.get_connection()
    try:
        with conn.cursor() as cursor:
            for table in Tables:
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        id TEXT PRIMARY KEY NOT NULL,
                        data TEXT,
                        synced BOOLEAN DEFAULT FALSE
                    )
                """)
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                if cursor.fetchone()[0] == 0:
                    for _ in range(100):
                        cursor.execute(f"""
                            INSERT INTO {table} (id, data, synced)
                            VALUES (?, ?, ?)
                        """, (str(uuid.uuid4()), "This is a test record", False))
        logger.info("Migration and seeding complete.")
    except Exception as e:
        logger.exception("Migration error: %s", e)
    finally:
        conn.close()

async def send_to_cloud(table, columns, interval):
    logger.info(f"Starting sync loop for {table}")
    batch_size = BATCH_SIZE
    hub_client = IoTHubClientSingleton.get_client()

    while True:
        offset = 0
        rows_fetched = 0
        conn = RqliteConnectionSingleton.get_connection()

        try:
            with conn.cursor() as cursor:
                more_rows = True
                while more_rows:
                    col_string = ", ".join(f"`{col}`" for col in columns) if columns else "*"
                    cursor.execute(f"""
                        SELECT id, {col_string} FROM {table}
                        WHERE synced = false
                        LIMIT {batch_size} OFFSET {offset}
                    """)
                    rows = cursor.fetchall()

                    if not rows:
                        more_rows = False
                        break

                    ids = []
                    payload = dict()
                    payload[table]=list()
                    for row in rows:
                        ids.append(row[0])
                        logger.debug(f"[{table}] Sending row: {row}")
                        entry = dict()
                        for i,column in enumerate(columns):
                            entry[column] = row[i+1]
                        payload[table].append(entry)
                    await hub_client.send_message_to_output(json.dumps(payload),table)
                    if ids:
                        placeholders = ",".join("?" for _ in ids)
                        cursor.execute(f"""
                            UPDATE {table} SET synced = true
                            WHERE id IN ({placeholders})
                        """, ids)

                    offset += batch_size
                    rows_fetched += len(rows)
                    await asyncio.sleep(0.1)

        except Exception as e:
            logger.exception(f"[{table}] Error during sync: {e}")
        finally:
            conn.close()

        logger.info(f"[{table}] Sync complete. Total rows: {rows_fetched}")
        await asyncio.sleep(interval)

async def main():
    logger.info("Starting all sync tasks")
    tasks = []
    for table, config in Tables.items():
        task = asyncio.create_task(send_to_cloud(
            table=table,
            columns=config.get("columns", []),
            interval=config.get("interval", 10)
        ))
        tasks.append(task)

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Tasks cancelled.")
    finally:
        IoTHubClientSingleton.disconnect()

if __name__ == "__main__":
    perform_migrations()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user.")
