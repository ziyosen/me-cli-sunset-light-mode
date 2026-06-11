import os
import json
from typing import List, Dict

class Bookmark:
    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.packages: List[Dict] = []
            self.filepath = "bookmark.json"
            self.reload_for_current_dir()
            self._initialized = True

    def reload_for_current_dir(self):
        """Reset and re-read bookmark.json from current working dir."""
        self.packages = []
        self.filepath = "bookmark.json"
        if os.path.exists(self.filepath):
            try:
                self.load_bookmark()
            except Exception as e:
                print(f"[bookmark.reload] err: {e}")
                self.packages = []
        else:
            self._save([])

    def _save(self, data: List[Dict]):
        """Helper to write JSON safely."""
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def _ensure_schema(self):
        """Ensure all bookmarks have the latest schema fields."""
        updated = False
        for p in self.packages:
            if "family_name" not in p:  # add missing field
                p["family_name"] = ""
                updated = True
            if "order" not in p:
                p["order"] = 0
                updated = True
            if "package_option_code" not in p:
                p["package_option_code"] = ""
                updated = True
        if updated:
            self.save_bookmark()  # persist schema upgrade

    def load_bookmark(self):
        """Load bookmarks from JSON file and ensure schema consistency."""
        with open(self.filepath, "r", encoding="utf-8") as f:
            self.packages = json.load(f)
        self._ensure_schema()

    def save_bookmark(self):
        """Save current bookmarks to JSON file."""
        self._save(self.packages)

    def add_bookmark(
        self,
        family_code: str,
        family_name: str,
        is_enterprise: bool,
        variant_name: str,
        option_name: str,
        order: int,
        package_option_code: str = "",
    ) -> bool:
        """Add a bookmark if it does not already exist."""
        code = (package_option_code or "").strip()
        key = (family_code, variant_name, order)
        code_key = (family_code, code) if code else None

        if any(
            (p["family_code"], p["variant_name"], p["order"]) == key
            for p in self.packages
        ):
            print("Bookmark already exists.")
            return False
        if code_key and any(
            (p["family_code"], (p.get("package_option_code") or "").strip()) == code_key
            for p in self.packages
        ):
            print("Bookmark already exists.")
            return False

        row = {
            "family_name": family_name,
            "family_code": family_code,
            "is_enterprise": is_enterprise,
            "variant_name": variant_name,
            "option_name": option_name,
            "order": order,
        }
        if code:
            row["package_option_code"] = code
        self.packages.append(row)
        self.save_bookmark()
        print("Bookmark added.")
        return True

    def remove_bookmark(
        self,
        family_code: str,
        is_enterprise: bool,
        variant_name: str,
        order: int,
    ) -> bool:
        """Remove a bookmark if it exists. Returns True if removed."""
        for i, p in enumerate(self.packages):
            if (
                p["family_code"] == family_code
                and p["is_enterprise"] == is_enterprise
                and p["variant_name"] == variant_name
                and p["order"] == order
            ):
                del self.packages[i]
                self.save_bookmark()
                print("Bookmark removed.")
                return True
        print("Bookmark not found.")
        return False

    def get_bookmarks(self) -> List[Dict]:
        """Return all bookmarks."""
        return self.packages.copy()


def resolve_bookmark_option_code(family: dict, bookmark: dict) -> str | None:
    """Map a bookmark row to package_option_code from get_family() payload."""
    code = (bookmark.get("package_option_code") or "").strip()
    if code:
        return code

    variant_name = (bookmark.get("variant_name") or "").strip()
    option_name = (bookmark.get("option_name") or "").strip()
    order_raw = bookmark.get("order")
    try:
        order_int = int(order_raw) if order_raw is not None else None
    except (TypeError, ValueError):
        order_int = None

    variants = family.get("package_variants") or []

    if order_int is not None and order_int > 0:
        for variant in variants:
            if variant_name and variant.get("name") != variant_name:
                continue
            for opt in variant.get("package_options") or []:
                try:
                    if int(opt.get("order", -1)) == order_int:
                        return opt.get("package_option_code")
                except (TypeError, ValueError):
                    continue

    if not option_name:
        return None

    onorm = option_name.casefold()
    for variant in variants:
        if variant_name and variant.get("name") != variant_name:
            continue
        for opt in variant.get("package_options") or []:
            if (opt.get("name") or "").strip().casefold() == onorm:
                return opt.get("package_option_code")

    return None


BookmarkInstance = Bookmark()
