import os
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).parent.parent / "config"
ASC_PATH = Path(__file__).parent.parent / "asc"

NONE = "None"


def _read() -> dict:
    data = {}
    if CONFIG_PATH.exists():
        for line in CONFIG_PATH.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip() if v.strip() != NONE else None
    return data


def _write(data: dict):
    lines = [f"{k}={v if v is not None else NONE}" for k, v in data.items()]
    CONFIG_PATH.write_text("\n".join(lines))


def get_config() -> dict:
    defaults = {
        "EMAIL": None,
        "PASSWORD": None,
        "COUNTRY": None,
        "FACILITY_ID": None,
        "MIN_DATE": None,
        "MAX_DATE": None,
        "NEED_ASC": "False",
        "ASC_FACILITY_ID": None,
        "SCHEDULE_ID": None,
    }
    cfg = _read()
    defaults.update(cfg)
    return defaults


def save_config(form: dict):
    # keep only the keys we understand
    allowed = {
        "EMAIL", "PASSWORD", "COUNTRY", "FACILITY_ID",
        "MIN_DATE", "MAX_DATE", "NEED_ASC",
        "ASC_FACILITY_ID", "SCHEDULE_ID"
    }
    cleaned = {k: v.strip() if v else None for k, v in form.items() if k in allowed}
    _write(cleaned)