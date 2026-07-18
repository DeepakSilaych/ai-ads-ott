"""Viewer-demographic targeting for brand selection.

The detector finds *where* an ad can go; this module decides *which* brand
should go there for a given viewer. Every brand in brands/ carries an
`audience` block, every preset segment below describes a viewer cohort, and
score() measures how well the two match. The pipeline uses the ranking to
bias — never to hard-filter — so a scene-perfect brand is still reachable
for an off-profile audience, just demoted.
"""

AGE_BANDS = ["13-17", "18-24", "25-34", "35-49", "50-64", "65+"]
GENDERS = ["any", "female", "male"]
INCOME_BANDS = ["low", "mid", "high"]

# Preset viewer cohorts an OTT platform would actually sell against.
PROFILES = [
    {
        "id": "gen_z",
        "label": "Gen Z (13-24)",
        "age": ["13-17", "18-24"],
        "gender": "any",
        "income": ["low", "mid"],
        "interests": ["gaming", "music", "streetwear", "social", "sports"],
    },
    {
        "id": "young_urban",
        "label": "Young Urban Professionals (25-34)",
        "age": ["25-34"],
        "gender": "any",
        "income": ["mid", "high"],
        "interests": ["tech", "coffee", "fitness", "nightlife", "travel"],
    },
    {
        "id": "families",
        "label": "Families with Kids",
        "age": ["25-34", "35-49"],
        "gender": "any",
        "income": ["mid"],
        "interests": ["home", "cooking", "kids", "convenience", "value"],
    },
    {
        "id": "affluent_35_plus",
        "label": "Affluent 35+",
        "age": ["35-49", "50-64"],
        "gender": "any",
        "income": ["high"],
        "interests": ["tech", "home", "travel", "wellness"],
    },
    {
        "id": "sports_fans",
        "label": "Sports & Fitness Fans",
        "age": ["18-24", "25-34", "35-49"],
        "gender": "any",
        "income": ["mid", "high"],
        "interests": ["sports", "fitness", "gaming", "energy"],
    },
    {
        "id": "older_adults",
        "label": "Older Adults (50+)",
        "age": ["50-64", "65+"],
        "gender": "any",
        "income": ["mid", "high"],
        "interests": ["home", "cooking", "wellness", "value"],
    },
]

# Weights sum to 1.0 — age and interests carry the signal, gender is a light
# nudge because most catalog brands are gender-neutral.
W_AGE, W_INTEREST, W_INCOME, W_GENDER = 0.40, 0.35, 0.15, 0.10


def get_profile(profile_id):
    """Preset by id, or None. Unknown ids are treated as 'no targeting'."""
    if not profile_id:
        return None
    return next((p for p in PROFILES if p["id"] == profile_id), None)


def resolve(profile):
    """Accept a profile id, a full profile dict, or None."""
    if profile is None:
        return None
    if isinstance(profile, str):
        return get_profile(profile)
    if isinstance(profile, dict) and (profile.get("age") or profile.get("interests")):
        return profile
    return None


def _overlap(a, b):
    """Fraction of the profile's values the brand also covers."""
    a, b = set(a or []), set(b or [])
    if not a or not b:
        return 0.0
    return len(a & b) / len(a)


def _gender_fit(brand_gender, profile_gender):
    if not brand_gender or brand_gender == "any" or not profile_gender \
            or profile_gender == "any":
        return 1.0
    return 1.0 if brand_gender == profile_gender else 0.0


def score(brand, profile):
    """0-1 relevance of one brand to one viewer cohort.

    Returns 0.5 (neutral) when either side carries no audience data, so
    brands missing an `audience` block are neither boosted nor buried."""
    profile = resolve(profile)
    aud = brand.get("audience")
    if not profile or not aud:
        return 0.5
    return round(
        W_AGE * _overlap(profile.get("age"), aud.get("age"))
        + W_INTEREST * _overlap(profile.get("interests"), aud.get("interests"))
        + W_INCOME * _overlap(profile.get("income"), aud.get("income"))
        + W_GENDER * _gender_fit(aud.get("gender"), profile.get("gender")),
        3,
    )


def rank(catalog, profile, min_score=0.0):
    """Catalog sorted best-match first, each entry tagged audience_score."""
    profile = resolve(profile)
    scored = []
    for b in catalog:
        b = dict(b)
        b["audience_score"] = score(b, profile)
        if b["audience_score"] >= min_score:
            scored.append(b)
    scored.sort(key=lambda b: -b["audience_score"])
    return scored


def profiles_for_prompt():
    """Compact segment list so an LLM can map 'for younger viewers' -> gen_z."""
    return "\n".join(
        f"- {p['id']}: {p['label']} (age {', '.join(p['age'])}; "
        f"interests {', '.join(p['interests'])})"
        for p in PROFILES)


def describe(profile):
    """One-paragraph cohort description for LLM prompt context."""
    profile = resolve(profile)
    if not profile:
        return "No audience targeting — judge brand fit on scene context alone."
    parts = [f"Segment: {profile.get('label', profile.get('id', 'custom'))}"]
    if profile.get("age"):
        parts.append(f"Age: {', '.join(profile['age'])}")
    if profile.get("gender") and profile["gender"] != "any":
        parts.append(f"Skews: {profile['gender']}")
    if profile.get("income"):
        parts.append(f"Income: {', '.join(profile['income'])}")
    if profile.get("interests"):
        parts.append(f"Interests: {', '.join(profile['interests'])}")
    return " | ".join(parts)
