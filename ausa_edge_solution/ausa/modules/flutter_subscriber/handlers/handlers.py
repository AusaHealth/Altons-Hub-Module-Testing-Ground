import asyncio
import logging
import os
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from azure.iot.device.iothub.aio import IoTHubModuleClient
import re
import json
import requests
import httpx

from handlers.utils import get_device_token, get_user_token, get_twin_property, create_auth_headers

from azure.iot.device.common.auth import connection_string as cs

logger = logging.getLogger(__name__)

MAIN_SERVER_URI = os.getenv("MAIN_SERVER","https://api.ausa.health")
DEVICE_ID = os.getenv("IOTEDGE_DEVICEID")
CERTIFICATE_FILE_PATH = os.getenv("CERT_PATH","/var/aziot")
CERTIFICATE_PATTERN = r".*-full-chain\.cert\.pem$"
AUTH_TYPE = os.getenv("IOTEDGE_AUTHSCHEME")
MODULE_ID = os.getenv("IOTEDGE_MODULEID","subscriber")
PRIMARY_KEY = os.getenv("PRIMARY_KEY")
SECONDARY_KEY = os.getenv("SECONDARY_KEY")

ALLOWED_ENDPOINTS = [
    "/device/user/records",
    "/device/user/appointments",
    "assigned/care-providers",
    "/device/user/routines"
]

def get_sha256_thumbprint(cert_path):
    with open(cert_path, "rb") as f:
        cert_data = f.read()
    cert = x509.load_pem_x509_certificate(cert_data, default_backend())
    return cert.fingerprint(hashes.SHA256()).hex().upper()


def check_main_server_uri_set(func):
    def actual_check():
        if not MAIN_SERVER_URI:
            raise Exception("Set the Variable Main Server Uri in the environments in the manifest file. Youuuuu Dumb Bruhhhhhh. Like Sooooooo Dumbbb")
    def inner(topic,payload):
        actual_check()
        return func(topic,payload)
    return inner


def api_request(module_client, method_request):
    """Generic API request handler for any allowed endpoint"""
    try:
        device_token, error = get_device_token(module_client)
        if error:
            return {"status": "error", "message": error}
        
        payload = method_request.payload or {}
        
        url_path = payload.get("url")
        if not url_path:
            return {"status": "error", "message": "url required in payload"}
        
        if url_path not in ALLOWED_ENDPOINTS:
            return {"status": "error", "message": f"URL '{url_path}' not allowed. Available: {ALLOWED_ENDPOINTS}"}
        
        user_id = payload.get("userId")
        if not user_id:
            return {"status": "error", "message": "userId required in payload"}
        
        user_token, user_data, error = get_user_token(module_client, user_id)
        if error:
            return {"status": "error", "message": error}
        
        headers = create_auth_headers(device_token, user_token)
        
        queries = payload.get("queries", {})
        
        query_string = ""
        if queries:
            query_params = []
            for key, value in queries.items():
                query_params.append(f"{key}={value}")
            query_string = "?" + "&".join(query_params)
        
        full_url = f"{MAIN_SERVER_URI}{url_path}{query_string}"
        
        with httpx.Client(http2=True) as client:
            http_method = payload.get("method", "GET").upper()
            
            if http_method == "GET":
                response = client.get(full_url, headers=headers)
            elif http_method == "POST":
                request_body = payload.get("body", {})
                response = client.post(full_url, headers=headers, json=request_body)
            elif http_method == "PUT":
                request_body = payload.get("body", {})
                response = client.put(full_url, headers=headers, json=request_body)
            elif http_method == "DELETE":
                response = client.delete(full_url, headers=headers)
            else:
                return {"status": "error", "message": f"HTTP method '{http_method}' not supported"}
            
            if 200 <= response.status_code < 300:
                try:
                    response_data = response.json()
                    return {
                        "status": "success", 
                        "message": "Request completed successfully",
                        "url": full_url,
                        "method": http_method,
                        "statusCode": response.status_code,
                        "data": response_data
                    }
                except:
                    return {
                        "status": "success", 
                        "message": "Request completed successfully",
                        "url": full_url,
                        "method": http_method,
                        "statusCode": response.status_code,
                        "data": response.text
                    }
            else:
                return {
                    "status": "error", 
                    "message": f"Request failed with status {response.status_code}",
                    "url": full_url,
                    "method": http_method,
                    "statusCode": response.status_code,
                    "response": response.text
                }
                
    except Exception as e:
        logger.error(f"Error in api_request: {e}")
        return {"status": "error", "message": str(e)}

def api_request_topic_handler(topic, module_client,payload):
    method_request = dict()
    method_request["payload"] = payload
    return json.dumps(api_request(module_client,method_request)).encode("utf-8")


def get_users_from_twin(module_client, method_request):
    """Get all users from twin with their IDs, names and emails"""
    try:
        users_data = get_twin_property(module_client, "users")
        if not users_data:
            return {"status": "error", "message": "No users found in twin"}
        
        users_list = []
        for user_id, user_info in users_data.items():
            user_entry = {
                "userId": user_id,
                "identifier": user_info.get("identifier", ""),
                "deviceId": user_info.get("deviceId", ""),
                "lastAuthenticatedTime": user_info.get("lastAuthenticatedTime", ""),
                "tokenExpiry": user_info.get("tokenExpiry", "")
            }
            users_list.append(user_entry)
        
        return {
            "status": "success", 
            "message": f"Found {len(users_list)} users", 
            "users": users_list,
            "total": len(users_list)
        }
        
    except Exception as e:
        logger.error(f"Error in get_users_from_twin: {e}")
        return {"status": "error", "message": str(e)}


def is_device_authenticated(module_client, method_request):
    """Check if device is currently authenticated with server"""
    try:
        device_token, error = get_device_token(module_client)
        if error:
            return {"status": "error", "message": error, "authenticated": False}
        
        headers = {'x-ausa-device-auth': f'Bearer {device_token}'}
        
        async with httpx.Client(http2=True) as client:
            response = client.get(f"{MAIN_SERVER_URI}/device/me", headers=headers)
            
        if response.status_code == 200:
            return {"status": "success", "message": "Device is authenticated", "authenticated": True}
        else:
            return {"status": "error", "message": "Device token is invalid", "authenticated": False}
            
    except Exception as e:
        logger.error(f"Error in is_device_authenticated: {e}")
        return {"status": "error", "message": str(e), "authenticated": False}


def is_user_authenticated(module_client, method_request):
    """Check if user is currently authenticated with server"""
    try:
        device_token, error = get_device_token(module_client)
        if error:
            return {"status": "error", "message": error, "authenticated": False}
        
        payload = method_request.payload or {}
        user_id = payload.get("userId")
        
        if not user_id:
            return {"status": "error", "message": "userId required in payload"}
        
        user_token, user_data, error = get_user_token(module_client, user_id)
        if error:
            return {"status": "error", "message": error, "authenticated": False}
        
        headers = create_auth_headers(device_token, user_token)
        
        with httpx.Client(http2=True) as client:
            response = client.get(f"{MAIN_SERVER_URI}/device/user/me", headers=headers)
            
        if response.status_code == 200:
            return {"status": "success", "message": "User is authenticated", "authenticated": True}
        else:
            return {"status": "error", "message": "User token is invalid", "authenticated": False}
            
    except Exception as e:
        logger.error(f"Error in is_user_authenticated: {e}")
        return {"status": "error", "message": str(e), "authenticated": False}


def authenticate_device(module_client, method_request):
    """Authenticate device with server using SAS keys"""
    try:
        device_token_data = get_twin_property(module_client, "deviceToken")
        if device_token_data:
            validation_result = is_device_authenticated(module_client, method_request)
            if validation_result.get("authenticated"):
                return {"status": "success", "message": "Device already authenticated"}
        
        if not DEVICE_ID:
            logger.error("Device ID not set in environment variables")
            return {"status": "error", "message": "Device ID missing"}
        
        if not PRIMARY_KEY or not SECONDARY_KEY:
            logger.error("Primary key or secondary key not set in environment variables")
            return {"status": "error", "message": "Keys missing"}
        
        request_body = {
            "keys": {
                "primaryKey": PRIMARY_KEY,
                "secondaryKey": SECONDARY_KEY
            },
            "authModuleId": MODULE_ID
        }

        with httpx.Client(http2=True) as client:
            response = client.post(
                f"{MAIN_SERVER_URI}/device/login/edge-device/{DEVICE_ID}?authType=sas",
                json=request_body
            )
            response_data = response.json()
            
        return response_data
        
    except Exception as e:
        logger.error(f"Error in authenticate_device: {e}")
        return {"status": "error", "message": str(e)}


def authenticate_user(module_client, method_request):
    """Authenticate user using device token from twin"""
    try:
        users_data = get_twin_property(module_client, "users")
        if users_data:
            validation_result = is_user_authenticated(module_client, method_request)
            if validation_result.get("authenticated"):
                return {"status": "success", "message": "User already authenticated"}
        
        device_token_data = get_twin_property(module_client, "deviceToken")
        
        if not device_token_data:
            logger.error("No deviceToken found in twin properties")
            return {"status": "error", "message": "Device token not found in twin"}
        
        if isinstance(device_token_data, dict):
            bearer_token = device_token_data.get("token")
        else:
            bearer_token = device_token_data
            
        if not bearer_token:
            logger.error("No bearer token found in deviceToken property")
            return {"status": "error", "message": "Bearer token not found"}
        
        payload = method_request.payload
        if not payload:
            logger.error("No payload provided for user authentication")
            return {"status": "error", "message": "User credentials required"}
        
        user_id = payload.get("userId")
        if not user_id:
            logger.error("Missing userId in payload")
            return {"status": "error", "message": "userId required in payload"}
        
        identifier = payload.get("identifier")
        password = payload.get("password")
        
        if not identifier or not password:
            logger.error("Missing identifier or password in payload")
            return {"status": "error", "message": "Identifier and password required"}
        
        request_body = {
            "identifier": identifier,
            "password": password,
            "authModuleId": MODULE_ID
        }
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {bearer_token}'
        }
        
        with httpx.Client(http2=True) as client:
            response = client.post(
                f"{MAIN_SERVER_URI}/device/login/user?authType=x509",
                json=request_body,
                headers=headers
            )
            response_data = response.json()
                
        return response_data
        
    except Exception as e:
        logger.error(f"Error in authenticate_user: {e}")
        return {"status": "error", "message": str(e)}


def authenticate_user_topic_handler(topic, module_client, payload):
    method_request = dict()
    method_request["payload"] = payload
    result = authenticate_user(module_client, method_request)
    return json.dumps(result).encode("utf-8")

def authenticate_device_topic_handler(topic, module_client, payload):
    method_request = dict()
    method_request["payload"] = payload
    result = authenticate_device(module_client, method_request)
    return json.dumps(result).encode("utf-8")

def is_device_authenticated_topic_handler(topic, module_client, payload):
    method_request = dict()
    method_request["payload"] = payload
    result = is_device_authenticated(module_client, method_request)
    return json.dumps(result).encode("utf-8")

def is_user_authenticated_topic_handler(topic, module_client, payload):
    method_request = dict()
    method_request["payload"] = payload
    result = is_user_authenticated(module_client, method_request)
    return json.dumps(result).encode("utf-8")

def get_users_from_twin_topic_handler(topic, module_client, payload):
    method_request = dict()
    method_request["payload"] = payload
    result = get_users_from_twin(module_client, method_request)
    return json.dumps(result).encode("utf-8")