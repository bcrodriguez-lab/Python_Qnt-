"""
Download reports from Wolkvox servers for a date range.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from servers import load_servers
from api_handlers.wolkvox_utils import build_wolkvox_headers, find_wolkvox_token
from api_handlers.Wolkvox_Carga_Clientes import extract_url_placeholders, resolve_url_template

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

REPORT_ENDPOINT = "{{servidor}}/api/v2/campaign.php?api=campaigns&type={{type}}&date={{date}}"
REPORT_TYPE = os.environ.get("REPORT_TYPE", "corte")
CONFIG_PATH = Path(__file__).parent / "config.json"

BASE_DRIVE_PATH = os.environ.get(
    "BASE_DRIVE_PATH",
    r"/content/drive/Shareddrives/Analitica/Embudo de Conversión/Proyecto Robot Omnicanal/Producción/2026-05/Resultado"
)


def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_servers() -> list[dict]:
    result = load_servers()
    if result.get("success"):
        return result.get("servers", [])
    return []


def _build_api_config() -> dict:
    return {"name": "download_reports", "url": REPORT_ENDPOINT}


def _download_report(
    server: dict, date_str: str, report_type: str, max_retries: int = 3
) -> dict:
    server_name = server.get("name", "")
    server_url = (server.get("url") or "").strip().rstrip("/")
    if not server_url:
        return {"success": False, "message": f"Server '{server_name}' has no URL configured."}

    base_url = REPORT_ENDPOINT
    payload = {
        "servidor": server_url,
        "type": report_type,
        "date": date_str,
        "server": server_name,
    }

    missing_params = [
        name
        for name in extract_url_placeholders(base_url)
        if not str(payload.get(name, "")).strip()
    ]
    if missing_params:
        return {
            "success": False,
            "message": f"Missing parameters for URL: {', '.join(missing_params)}",
        }

    try:
        url = resolve_url_template(base_url, payload)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}

    token = find_wolkvox_token(payload, _build_api_config())
    if not token:
        return {
            "success": False,
            "message": f"No token found for server '{server_name}'.",
        }

    headers = build_wolkvox_headers(token)

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                f"Downloading report from '{server_name}' for date {date_str} "
                f"(attempt {attempt}/{max_retries})"
            )
            response = requests.get(url, headers=headers, timeout=60)
            response.raise_for_status()
            data = response.json()

            return {
                "success": True,
                "message": f"Report downloaded successfully for {date_str}",
                "url": url,
                "status": response.status_code,
                "data": data,
            }
        except requests.Timeout:
            logger.warning(f"Timeout downloading report for '{server_name}' on {date_str}")
            if attempt == max_retries:
                return {"success": False, "message": "Request timeout after max retries."}
        except requests.HTTPError as e:
            logger.error(
                f"HTTP error {e.response.status_code} for '{server_name}' on {date_str}"
            )
            if attempt == max_retries:
                return {
                    "success": False,
                    "message": f"HTTP error: {e.response.status_code}",
                }
        except Exception as e:
            logger.error(
                f"Error downloading report from '{server_name}' on {date_str}: {e}"
            )
            if attempt == max_retries:
                return {"success": False, "message": str(e)}

        backoff = 2 ** (attempt - 1)
        logger.info(f"Retrying in {backoff} seconds...")
        time.sleep(backoff)

    return {"success": False, "message": "Max retries exceeded."}


def _ensure_directory(base_dir: Path, date_str: str) -> Path:
    dir_path = base_dir / date_str / "corte 1"
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def _save_report(
    data: dict, server_name: str, report_name: str, dir_path: Path
) -> Path | None:
    ext = "json"
    filename = f"{server_name}_{report_name}.{ext}"
    file_path = dir_path / filename

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved report to {file_path}")
        return file_path
    except Exception as e:
        logger.error(f"Failed to save report to {file_path}: {e}")
        return None


def download_reports_for_date(
    date_str: str, base_dir: Path, report_type: str = REPORT_TYPE
) -> dict:
    servers = _get_servers()
    if not servers:
        return {"success": False, "message": "No servers configured.", "results": []}

    results = []
    for server in servers:
        server_name = server.get("name", "")
        result = _download_report(server, date_str, report_type)

        if result.get("success"):
            dir_path = _ensure_directory(base_dir, date_str)
            saved_path = _save_report(
                result.get("data", {}), server_name, report_type, dir_path
            )
            result["saved_to"] = str(saved_path) if saved_path else None

        results.append(
            {
                "server": server_name,
                "date": date_str,
                "success": result.get("success", False),
                "message": result.get("message", ""),
                "saved_to": result.get("saved_to"),
            }
        )

    successful = sum(1 for r in results if r["success"])
    return {
        "success": True,
        "message": f"Downloaded {successful}/{len(servers)} reports for {date_str}.",
        "date": date_str,
        "results": results,
    }


def download_reports_for_date_range(
    start_date: str,
    end_date: str,
    base_dir: Path | None = None,
    report_type: str | None = None,
) -> dict:
    if base_dir is None:
        base_dir = Path(__file__).parent

    if report_type is None:
        report_type = REPORT_TYPE

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError as e:
        return {"success": False, "message": f"Invalid date format: {e}", "results": []}

    if end < start:
        return {
            "success": False,
            "message": "End date must be >= start date.",
            "results": [],
        }

    all_results = []
    current = start
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        logger.info(f"Processing date: {date_str}")
        result = download_reports_for_date(date_str, base_dir, report_type)
        all_results.append(result)
        current += timedelta(days=1)

    total_success = [r for rr in all_results for r in rr.get("results", [])]
    successful = sum(1 for r in total_success if r["success"])
    total_servers = len(_get_servers())
    total_dates = (end - start).days + 1

    return {
        "success": True,
        "message": f"Downloaded {successful}/{total_servers * total_dates} reports total.",
        "start_date": start_date,
        "end_date": end_date,
        "report_type": report_type,
        "results": all_results,
    }


def main() -> None:
    start_date = "2026-05-01"
    end_date = "2026-05-20"
    base_dir = Path(BASE_DRIVE_PATH)
    report_type = REPORT_TYPE

    logger.info(
        f"Starting report download from {start_date} to {end_date} "
        f"(type: {report_type})"
    )
    logger.info(f"Base directory: {base_dir}")

    result = download_reports_for_date_range(start_date, end_date, base_dir, report_type)

    logger.info(result.get("message", ""))

    if result.get("success"):
        for date_result in result.get("results", []):
            date_str = date_result.get("date", "")
            for server_result in date_result.get("results", []):
                status = "SUCCESS" if server_result["success"] else "FAILED"
                logger.info(
                    f"  [{date_str}] {server_result['server']}: {status}"
                )


if __name__ == "__main__":
    main()