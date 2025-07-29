import logging

logger = logging.getLogger(__name__)


def get_twin_property(module_client, property_name):
    """Get a specific property from module twin"""
    try:
        twin = module_client.get_twin()
        reported_properties = twin.get("reported", {})
        desired_properties = twin.get("desired", {})
        
        property_value = reported_properties.get(property_name) or desired_properties.get(property_name)
        return property_value
        
    except Exception as e:
        logger.error(f"Error getting twin property '{property_name}': {e}")
        return None


def update_twin_property(module_client, properties):
    """Update module twin reported properties"""
    try:
        module_client.patch_twin_reported_properties(properties)
        logger.info(f"Updated twin properties: {properties}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating twin properties: {e}")
        return False


def get_device_token(module_client):
    """Get device token from twin and extract bearer token"""
    try:
        device_token_data = get_twin_property(module_client, "deviceToken")
        if not device_token_data:
            return None, "No device token found in twin"
        
        if isinstance(device_token_data, dict):
            bearer_token = device_token_data.get("token")
        else:
            bearer_token = device_token_data
            
        if not bearer_token:
            return None, "No bearer token found in device token"
            
        return bearer_token, None
        
    except Exception as e:
        logger.error(f"Error getting device token: {e}")
        return None, str(e)


def get_user_token(module_client, user_id):
    """Get user token from twin for specific user ID"""
    try:
        users_data = get_twin_property(module_client, "users")
        if not users_data:
            return None, None, "No users found in twin"
        
        if user_id not in users_data:
            return None, None, f"User {user_id} not found in twin"
        
        user_data = users_data[user_id]
        user_token = user_data.get("token")
        if not user_token:
            return None, user_data, "No user bearer token found"
            
        return user_token, user_data, None
        
    except Exception as e:
        logger.error(f"Error getting user token: {e}")
        return None, None, str(e)


def create_auth_headers(device_token, user_token=None):
    """Create authorization headers for API requests"""
    headers = {
        'X-Ausa-Device-Auth': f'Bearer {device_token}'
    }
    
    if user_token:
        headers['X-Ausa-User-Auth'] = f'Bearer {user_token}'
        
    return headers 