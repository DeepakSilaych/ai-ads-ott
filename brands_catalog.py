"""Loader for the brands/ reference directory."""
import json
import os

import audience

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


def catalog_for_prompt(only=None, profile=None):
    """Compact text block describing brands for LLM context.
    only=<brand name> restricts to that brand (user-directed targeting).
    profile=<audience id or dict> ranks brands best-match-first and annotates
    each with its audience fit, so the LLM prefers on-target brands."""
    catalog = load_catalog()
    if profile is not None:
        catalog = audience.rank(catalog, profile)
    lines = []
    for b in catalog:
        if only and b["name"].lower() != only.lower():
            continue
        line = (
            f"- {b['name']} (say: \"{b['spoken_name']}\") | {b['category']} | "
            f"products: {', '.join(b['products'])} | "
            f"replaces generic terms like: {', '.join(b['generic_terms'])} | "
            f"fits scenes: {', '.join(b['scene_fit'])}"
        )
        if "audience_score" in b:
            aud = b.get("audience", {})
            line += (f" | audience fit: {b['audience_score']:.2f}"
                     f" (targets {', '.join(aud.get('age', [])) or 'any age'};"
                     f" {', '.join(aud.get('interests', [])) or 'general'})")
        lines.append(line)
    return "\n".join(lines)
