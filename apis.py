import json
import os
import threading
from datetime import datetime

from campaigns import has_pending_campaigns_for_api, pending_campaigns_for_api
from api_runner import validate_handler

_lock = threading.Lock()
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
DEMO_API_NAME = "API DEMO"
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def is_system_api(name: str) -> bool:
    """APIs del sistema (referencia) no se pueden editar ni borrar."""
    return (name or "").strip() == DEMO_API_NAME


def _ensure_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2, ensure_ascii=False)


def _read_config() -> dict:
    _ensure_config()
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def ensure_demo_api():
    """Registra la API DEMO de referencia si no existe."""
    try:
        ensure_demo = {
            "name": DEMO_API_NAME,
            "url": "https://httpbin.org/post",
            "descripcion": "API de referencia POST. Plantilla en api_handlers/DEMO.py",
            "frecuencia_ejecucion": 60,
            "archivo": "DEMO",
            "metodo": "post",
        }
        with _lock:
            cfg = _read_config()
            apis = cfg.get("apis", [])
            if any(a.get("name") == ensure_demo["name"] for a in apis):
                return
            apis.append({**ensure_demo, "created_at": datetime.utcnow().isoformat()})
            cfg["apis"] = apis
            _write_config(cfg)
    except Exception:
        pass


def load_apis():
    """Carga la lista de APIs desde config.json."""
    try:
        ensure_demo_api()
        cfg = _read_config()
        apis = cfg.get("apis", [])
        for api in apis:
            api.pop("token", None)
            _apply_api_flags(api)
        return {"success": True, "apis": apis}
    except Exception as e:
        return {"success": False, "message": str(e), "apis": []}


def normalize_http_metodo(metodo: str) -> str | None:
    """Devuelve el metodo en minusculas si es HTTP valido; si no, None."""
    value = (metodo or "").strip().upper()
    if value not in HTTP_METHODS:
        return None
    return value.lower()


def validate_http_metodo(metodo: str) -> dict:
    if normalize_http_metodo(metodo) is None:
        return {
            "success": False,
            "message": f"Método HTTP inválido. Use: {', '.join(HTTP_METHODS)}.",
        }
    return {"success": True}


def _apply_api_flags(api: dict) -> None:
    name = api.get("name", "")
    if is_system_api(name):
        api["system"] = True
        api["editable"] = False
        api["deletable"] = False
        return
    api["system"] = False
    api["editable"] = True
    api["deletable"] = not has_pending_campaigns_for_api(name)


def get_api(name: str) -> dict | None:
    if not name:
        return None
    name = name.strip()
    try:
        cfg = _read_config()
        for api in cfg.get("apis", []):
            if api.get("name") == name:
                api_copy = dict(api)
                api_copy.pop("token", None)
                _apply_api_flags(api_copy)
                return api_copy
        return None
    except Exception:
        return None


def save_api(
    name: str,
    url: str,
    descripcion: str,
    frecuencia_ejecucion: int,
    archivo: str,
    metodo: str,
    original_name: str = None,
) -> dict:
    """Guarda o actualiza una API en config.json."""
    if not name or not url:
        return {"success": False, "message": "El nombre y la URL son obligatorios."}

    original_name = (original_name or "").strip() or name.strip()
    if is_system_api(original_name):
        return {
            "success": False,
            "message": f"La {DEMO_API_NAME} es de referencia del sistema y no se puede modificar.",
        }
    if is_system_api(name):
        return {
            "success": False,
            "message": f"No se puede registrar otra API con el nombre '{DEMO_API_NAME}'.",
        }

    metodo_check = validate_http_metodo(metodo)
    if not metodo_check.get("success"):
        return metodo_check

    archivo = (archivo or "").strip()
    metodo = normalize_http_metodo(metodo)
    handler_check = validate_handler(archivo, metodo)
    if not handler_check.get("success"):
        return handler_check

    try:
        frecuencia = int(frecuencia_ejecucion)
        if frecuencia < 1:
            return {"success": False, "message": "La frecuencia de ejecución debe ser al menos 1 minuto."}
    except (TypeError, ValueError):
        return {"success": False, "message": "La frecuencia de ejecución debe ser un número entero."}

    name = name.strip()
    url = url.strip()
    descripcion = (descripcion or "").strip()
    original_name = original_name.strip() or name

    try:
        with _lock:
            cfg = _read_config()
            apis = cfg.get("apis", [])

            existing_idx = next(
                (i for i, a in enumerate(apis) if a.get("name") == original_name),
                None,
            )
            if existing_idx is None:
                if any(a.get("name") == name for a in apis):
                    return {"success": False, "message": "Ya existe una API con ese nombre."}
                apis.append({
                    "name": name,
                    "url": url,
                    "descripcion": descripcion,
                    "frecuencia_ejecucion": frecuencia,
                    "archivo": archivo,
                    "metodo": metodo,
                    "created_at": datetime.utcnow().isoformat(),
                })
            else:
                if name != original_name and any(
                    a.get("name") == name for i, a in enumerate(apis) if i != existing_idx
                ):
                    return {"success": False, "message": "Ya existe una API con ese nombre."}

                entry = apis[existing_idx]
                entry["name"] = name
                entry["url"] = url
                entry["descripcion"] = descripcion
                entry.pop("token", None)
                entry["frecuencia_ejecucion"] = frecuencia
                entry["archivo"] = archivo
                entry["metodo"] = metodo
                entry["updated_at"] = datetime.utcnow().isoformat()

                if name != original_name:
                    _rename_api_in_campaigns(original_name, name)
                    from server_apis import on_api_renamed
                    on_api_renamed(original_name, name)

            cfg["apis"] = apis
            _write_config(cfg)

        return {"success": True, "message": "API guardada correctamente.", "apis": apis}
    except Exception as e:
        return {"success": False, "message": str(e)}


def _rename_api_in_campaigns(old_name: str, new_name: str) -> None:
    from database import Campaign, db

    Campaign.query.filter(Campaign.api == old_name).update(
        {Campaign.api: new_name}, synchronize_session=False
    )
    db.session.commit()


def delete_api(name: str) -> dict:
    """Elimina una API si no tiene campañas pendientes asociadas."""
    if not name:
        return {"success": False, "message": "El nombre de la API es obligatorio."}

    name = name.strip()
    if is_system_api(name):
        return {
            "success": False,
            "message": f"La {DEMO_API_NAME} es de referencia del sistema y no se puede eliminar.",
        }
    if has_pending_campaigns_for_api(name):
        pending = pending_campaigns_for_api(name)
        names = ", ".join(f"#{c['id']} {c['nombre']}" for c in pending[:3])
        extra = f" (+{len(pending) - 3} más)" if len(pending) > 3 else ""
        return {
            "success": False,
            "message": (
                f"No se puede eliminar la API '{name}': tiene campañas programadas "
                f"que aún no se han ejecutado ({names}{extra})."
            ),
        }

    try:
        with _lock:
            cfg = _read_config()
            apis = cfg.get("apis", [])
            new_apis = [a for a in apis if a.get("name") != name]
            if len(new_apis) == len(apis):
                return {"success": False, "message": "No se encontró la API."}

            cfg["apis"] = new_apis
            _write_config(cfg)

        from server_apis import on_api_deleted
        on_api_deleted(name)

        return {"success": True, "message": "API eliminada correctamente.", "apis": new_apis}
    except Exception as e:
        return {"success": False, "message": str(e)}
