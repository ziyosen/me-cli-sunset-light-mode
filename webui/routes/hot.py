import json
from pathlib import Path
from fastapi import APIRouter, Request
from webui.deps import render

router = APIRouter()
PROJECT_DIR = Path(__file__).resolve().parents[2]


def _read_hot(filename: str):
    path = PROJECT_DIR / "hot_data" / filename
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


@router.get("/hot")
def hot(request: Request):
    return render(request, "hot.html", title="🔥 Hot", packages=_read_hot("hot.json"), kind="hot")


@router.get("/hot2")
def hot2(request: Request):
    return render(request, "hot.html", title="🔥🔥 Hot-2", packages=_read_hot("hot2.json"), kind="hot2")
