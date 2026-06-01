import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import requests
from api_handlers.wolkvox_utils import find_wolkvox_token, build_wolkvox_headers, _read_config_file

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")


def _get_api_config(api_name: str) -> dict:
    """Retrieve API configuration by name from config.json."""
    config = _read_config_file()
    for api in config.get("apis", []):
        if api.get("name") == api_name:
            return api
    return {}


def _get_server_url(server_name: str) -> str:
    """Retrieve server URL by name from config.json, or return directly if it's a URL."""
    # If it looks like a URL, return it directly
    if server_name.startswith("http://") or server_name.startswith("https://"):
        return server_name.rstrip("/")
    
    config = _read_config_file()
    for server in config.get("servers", []):
        if server.get("name") == server_name:
            url = server.get("url")
            if url:
                return url.rstrip("/")
    return ""


def upload_csv_to_campaign(
    campaign_id: str,
    csv_file_path: str,
    server_name: str = None,
    type_campaign: str = 'predictive',
    campaign_status: str = '1',
    api_name: str = 'Cargue de clientes',
    token: str = None
) -> dict:
    """
    Upload a CSV file to a Wolkvox campaign using the add_record endpoint.
    
    Args:
        campaign_id: The ID of the campaign to upload to.
        csv_file_path: Path to the CSV file to upload.
        server_name: Name of the server (as defined in config.json) or a full URL.
                     If not provided, will try to use the server from the API configuration.
        type_campaign: Type of campaign (default: 'predictive').
        campaign_status: Status of the campaign (default: '1').
        api_name: Name of the API configuration in config.json (default: 'Cargue de clientes').
        token: Wolkvox token. If not provided, will be looked up from config.json.
    
    Returns:
        dict: Result of the upload operation with keys:
            - success (bool)
            - message (str)
            - status (int, HTTP status code if available)
            - url (str, the final URL used)
            - campaign_id (str)
    """
    # 1. Get API configuration
    api_config = _get_api_config(api_name)
    if not api_config:
        return {
            "success": False,
            "message": f"API configuration '{api_name}' not found in config.json."
        }
    
    url_template = api_config.get("url")
    if not url_template:
        return {
            "success": False,
            "message": f"URL not found in API configuration '{api_name}'."
        }
    
    # 2. Determine server URL
    if server_name is None:
        # Try to get server from API configuration? Not directly available.
        # We'll look for a server that has this API enabled in server_apis.
        config = _read_config_file()
        server_name_from_config = None
        for server_name_key, apis in config.get("server_apis", {}).items():
            if apis.get(api_name, False):
                server_name_from_config = server_name_key
                break
        if server_name_from_config:
            server_name = server_name_from_config
        else:
            return {
                "success": False,
                "message": "Server name not provided and could not be determined from config.json."
            }
    
    server_url = _get_server_url(server_name)
    if not server_url:
        return {
            "success": False,
            "message": f"Server '{server_name}' not found in config.json and is not a valid URL."
        }
    
    # 3. Replace placeholders in URL template
    placeholders = {
        "servidor": server_url,
        "campaign_id": campaign_id,
        "type_campaign": type_campaign,
        "campaign_status": campaign_status
    }
    try:
        url = url_template
        for placeholder, value in placeholders.items():
            url = url.replace(f"{{{{{placeholder}}}}}", value)
    except Exception as e:
        return {
            "success": False,
            "message": f"Error replacing placeholders in URL: {str(e)}"
        }
    
    # 4. Get Wolkvox token
    if token is None:
        # We'll try to find the token using the existing function.
        # We need to provide a payload and api_config to the find_wolkvox_token function.
        # We'll create a minimal payload with the server name and api_name.
        payload_for_token = {
            "server": server_name,
            "api_name": api_name
        }
        token = find_wolkvox_token(payload_for_token, api_config)
        if not token:
            return {
                "success": False,
                "message": "Wolkvox token not found. Please provide a token or configure it in config.json."
            }
    
    # 5. Prepare headers
    headers = build_wolkvox_headers(token, json_body=False)
    
    # 6. Upload the CSV file
    if not os.path.isfile(csv_file_path):
        return {
            "success": False,
            "message": f"CSV file not found: {csv_file_path}"
        }
    
    try:
        with open(csv_file_path, 'rb') as f:
            files = {'file': (os.path.basename(csv_file_path), f, 'text/csv')}
            response = requests.post(
                url,
                files=files,
                headers=headers,
                timeout=60
            )
    except requests.Timeout:
        return {
            "success": False,
            "message": "Timeout uploading CSV to Wolkvox.",
            "url": url
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error uploading CSV: {str(e)}",
            "url": url
        }
    
    # 7. Process response
    try:
        response_json = response.json()
    except ValueError:
        response_json = {"raw": response.text[:2000]}
    
    if response.ok:
        return {
            "success": True,
            "message": f"CSV uploaded successfully to campaign {campaign_id} (HTTP {response.status_code})",
            "status": response.status_code,
            "url": url,
            "campaign_id": campaign_id,
            "data": response_json
        }
    else:
        return {
            "success": False,
            "message": f"Failed to upload CSV to Wolkvox (HTTP {response.status_code})",
            "status": response.status_code,
            "url": url,
            "campaign_id": campaign_id,
            "data": response_json
        }