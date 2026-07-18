"""Loader for the brands/ reference directory."""
import json
import os

BRANDS_DIR = os.path.join(os.path.dirname(__file__), "brands")


def load_catalog():
    """All brand entries across category files."""
    catalog = []
    for name in sorted(os.listdir(BRANDS_DIR)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(BRANDS_DIR, name)) as f:
            data = json.load(f)
        for b in data.get("brands", []):
            b["category"] = data["category"]
            catalog.append(b)
    return catalog


def catalog_for_prompt(only=None):
    """Compact text block describing brands for LLM context.
    only=<brand name> restricts to that brand (user-directed targeting)."""
    lines = []
    for b in load_catalog():
        if only and b["name"].lower() != only.lower():
            continue
        lines.append(
            f"- {b['name']} (say: \"{b['spoken_name']}\") | {b['category']} | "
            f"products: {', '.join(b['products'])} | "
            f"replaces generic terms like: {', '.join(b['generic_terms'])} | "
            f"fits scenes: {', '.join(b['scene_fit'])}"
        )
    return "\n".join(lines)
