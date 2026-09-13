"""Secret-free configuration report; network access must be explicitly requested."""
from datetime import datetime, timezone
import os
import platform
import sys

from .nebius import DEFAULT_BASE_URL, NebiusError, NemotronPlanner


def diagnose(catalog=False):
    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime": {"system": platform.system(), "python": platform.python_version(),
                    "python_supported": sys.version_info >= (3, 11)},
        "endpoint": DEFAULT_BASE_URL,
        "status": "blocked", "credential_present": False,
        "configuration_present": False, "catalog_verified": False,
        "inference_verified": False, "network_requested": bool(catalog),
        "credit_balance": "unverified",
    }
    try:
        planner = NemotronPlanner()
        report["credential_present"] = bool(planner._api_key)
        report["credential_source"] = (
            "process_environment" if os.environ.get("NEBIUS_API_KEY")
            else "private_runtime_store" if planner._api_key else "missing")
        local = planner.preflight(live=False)
        report["configuration"] = local
        report["configuration_present"] = local["status"] == "configuration_present"
        if report["configuration_present"] and report["runtime"]["python_supported"]:
            report["status"] = "configured"
        if catalog:
            models = planner.list_models()
            eligible = [item["id"] for item in models if item["nvidia_nemotron"]]
            report["catalog_accessible"] = True
            report["eligible_models"] = eligible
            report["catalog_verified"] = report["configuration_present"] and planner.model in eligible
            if report["catalog_verified"] and report["runtime"]["python_supported"]:
                report["status"] = "catalog_verified"
            else:
                report["status"] = "blocked"
                report["next_action"] = "Select an exact NVIDIA Nemotron ID from eligible_models."
    except (ValueError, RuntimeError, OSError) as error:
        # Storage and transport failures must never echo exception details or keys.
        report["status"] = "blocked"
        report["error_code"] = error.code if isinstance(error, NebiusError) else "configuration_unavailable"
    return report
