
import requests
from datetime import datetime, date
from typing import Optional, List, Dict, Any
import re


def _to_wolkvox_ts(s: str, is_end: bool = False) -> str:
    if not s:
        return ""
    try:
        if "T" in s or len(s) > 10:
            dt = datetime.fromisoformat(s)
        else:
            d = date.fromisoformat(s)
            dt = datetime(d.year, d.month, d.day, 23, 59, 59) if is_end else datetime(d.year, d.month, d.day, 0, 0, 0)
        return dt.strftime("%Y%m%d%H%M%S")
    except Exception:
        return s


def build_wolkvox_base_url(server_name: str, server_config: Optional[Dict] = None) -> str:
    if not server_name:
        return ""
    server_name = server_name.strip()
    if server_name.lower().startswith("http"):
        return server_name.rstrip("/")
    if server_config:
        prefix = (server_config.get("url") or "").strip().rstrip("/")
        if prefix:
            return prefix if prefix.lower().startswith("http") else f"https://wv{prefix}.wolkvox.com"
    return f"https://wv{server_name}.wolkvox.com"


def get_wolkvox_headers(token: str, *, json_body: bool = False) -> Dict[str, str]:
    headers = {"wolkvox-token": token}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def find_wolkvox_token(
    payload: Optional[Dict],
    api_config: Optional[Dict],
    servers_config: Optional[List[Dict]] = None,
    global_token: str = ""
) -> str:
    payload = payload if isinstance(payload, dict) else {}
    api_config = api_config if isinstance(api_config, dict) else {}
    servers_config = servers_config or []
    token_keys = ("wolkvox-token", "wolkvox_token", "token")
    for key, value in payload.items():
        if key.lower().replace("_", "-") in token_keys:
            text = str(value).strip() if value is not None else ""
            if text:
                return text
    for key in token_keys:
        value = api_config.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    server_name = (
        payload.get("server")
        or payload.get("nombre_servidor")
        or payload.get("server_name")
        or api_config.get("server")
        or api_config.get("nombre_servidor")
        or api_config.get("server_name")
        or ""
    )
    if server_name and servers_config:
        for server in servers_config:
            if (server.get("name") or "").strip() == server_name.strip():
                value = server.get("token")
                if value is not None and str(value).strip():
                    return str(value).strip()
    return global_token


def extract_rows_from_response(data_json: Any) -> Optional[List[Dict]]:
    if not data_json:
        return None
    if isinstance(data_json, list) and len(data_json) > 0:
        return data_json
    if isinstance(data_json, dict):
        for key in ["data", "files", "rows", "records", "results", "cdr"]:
            value = data_json.get(key)
            if isinstance(value, list) and len(value) > 0:
                return value
        for key, value in data_json.items():
            if isinstance(value, list) and len(value) > 0:
                return value
        if any(isinstance(v, (str, int, float)) for v in data_json.values()):
            return [data_json]
    return None


def make_wolkvox_request(
    method: str,
    url: str,
    token: str,
    *,
    json_body: bool = False,
    timeout: int = 60,
    **kwargs
) -> Dict[str, Any]:
    headers = get_wolkvox_headers(token, json_body=json_body)
    try:
        response = requests.request(
            method=method.upper(),
            url=url,
            headers=headers,
            timeout=timeout,
            **kwargs
        )
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:2000]}
        if response.ok:
            return {
                "success": True,
                "status_code": response.status_code,
                "data": data,
                "message": f"Request exitoso (HTTP {response.status_code})",
                "url": url
            }
        return {
            "success": False,
            "status_code": response.status_code,
            "data": data,
            "message": f"Request falló (HTTP {response.status_code})",
            "url": url
        }
    except requests.Timeout:
        return {
            "success": False,
            "status_code": 0,
            "data": None,
            "message": f"Timeout al invocar {method} {url}",
            "url": url
        }
    except Exception as exc:
        return {
            "success": False,
            "status_code": 0,
            "data": None,
            "message": str(exc),
            "url": url
        }


def normalize_cdr_columns(rows: List[Dict], server_name: str) -> List[Dict]:
    if not rows:
        return []
    column_mapping = {
        "date": "DATE", "fecha": "DATE", "call_date": "DATE",
        "telephone": "TELEPHONE", "phone": "TELEPHONE", "customer_phone": "TELEPHONE", "telefono": "TELEPHONE",
        "cod_act": "COD_ACT", "code": "COD_ACT", "result": "COD_ACT", "resultado": "COD_ACT", "status": "COD_ACT",
        "conn_id": "CONN_ID", "call_id": "CONN_ID", "id_llamada": "CONN_ID", "uniqueid": "CONN_ID",
        "customer_id": "CUSTOMER_ID", "contacto__c": "CUSTOMER_ID", "client_id": "CUSTOMER_ID",
        "agent_name": "AGENT_NAME", "agent": "AGENT_NAME", "agente": "AGENT_NAME",
    }
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        new_row = {"SERVIDOR": server_name}
        for key, value in row.items():
            key_lower = key.lower().strip()
            mapped = False
            for pattern, target in column_mapping.items():
                if pattern in key_lower:
                    new_row[target] = value
                    mapped = True
                    break
            if not mapped:
                new_row[key.upper()] = value
        for col in ["DATE", "TELEPHONE", "COD_ACT", "CONN_ID", "CUSTOMER_ID"]:
            if col not in new_row:
                new_row[col] = None
        agent_name = str(new_row.get("AGENT_NAME", "")).upper().strip()
        if agent_name == "TOTAL":
            continue
        normalized.append(new_row)
    return normalized