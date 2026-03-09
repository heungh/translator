"""
Glossary Manager — Hierarchical glossary system (Common → Genre → Work).

Provides load/save/merge/scan/build for a 3-layer glossary architecture
and project CRUD operations.
"""

import json
import os
import re
import shutil
from docx import Document

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GLOSSARIES_DIR = os.path.join(BASE_DIR, "glossaries")
PROJECTS_PATH = os.path.join(BASE_DIR, "projects.json")
LEGACY_GLOSSARY_PATH = os.path.join(BASE_DIR, "glossary.json")

EMPTY_GLOSSARY = {"characters": [], "places": [], "terms": [], "style_rules": ""}

# 13 common Korean honorifics
COMMON_HONORIFICS = {
    "누나", "누님", "언니", "오빠", "형", "형님",
    "동생", "아저씨", "아줌마", "할머니", "할아버지", "선배", "후배",
}


# =============================================================================
# Low-level: single layer load / save
# =============================================================================

def load_glossary_layer(path: str) -> dict:
    """Load a single glossary JSON file."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {**EMPTY_GLOSSARY}


def save_glossary_layer(glossary: dict, path: str):
    """Save a single glossary JSON file (creates parent dirs if needed)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, indent=2)


# =============================================================================
# Projects CRUD
# =============================================================================

def load_projects() -> dict:
    """Load projects.json."""
    if os.path.exists(PROJECTS_PATH):
        with open(PROJECTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"projects": [], "genres": ["fantasy", "scifi", "romance"], "active_project": None}


def save_projects(data: dict):
    """Save projects.json."""
    with open(PROJECTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def slugify(name: str) -> str:
    """Convert a name to a filesystem-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def create_genre(slug: str):
    """Create a genre directory with an empty glossary."""
    genre_dir = os.path.join(GLOSSARIES_DIR, "genres", slug)
    os.makedirs(genre_dir, exist_ok=True)
    path = os.path.join(genre_dir, "glossary.json")
    if not os.path.exists(path):
        save_glossary_layer({**EMPTY_GLOSSARY}, path)


def create_project(name: str, genre: str) -> dict:
    """Create a new project: directory + glossary + register in projects.json."""
    project_id = slugify(name)
    genre_slug = slugify(genre)

    # Ensure genre exists
    create_genre(genre_slug)

    # Create work directory with empty glossary
    work_dir = os.path.join(GLOSSARIES_DIR, "works", project_id)
    os.makedirs(work_dir, exist_ok=True)
    work_path = os.path.join(work_dir, "glossary.json")
    if not os.path.exists(work_path):
        save_glossary_layer({**EMPTY_GLOSSARY}, work_path)

    # Register in projects.json
    data = load_projects()
    project = {
        "id": project_id,
        "name": name,
        "genre": genre_slug,
        "work": project_id,
    }

    # Avoid duplicate
    if not any(p["id"] == project_id for p in data["projects"]):
        data["projects"].append(project)

    # Add genre if new
    if genre_slug not in data["genres"]:
        data["genres"].append(genre_slug)

    data["active_project"] = project_id
    save_projects(data)
    return project


# =============================================================================
# Glossary paths & merge
# =============================================================================

def get_glossary_paths(project: dict) -> tuple[str, str, str]:
    """Return (common_path, genre_path, work_path) for a project."""
    common_path = os.path.join(GLOSSARIES_DIR, "common", "glossary.json")
    genre_path = os.path.join(GLOSSARIES_DIR, "genres", project["genre"], "glossary.json")
    work_path = os.path.join(GLOSSARIES_DIR, "works", project["work"], "glossary.json")
    return common_path, genre_path, work_path


def merge_glossaries(common: dict, genre: dict, work: dict) -> dict:
    """Merge 3 glossary layers. Priority: work > genre > common (by korean key)."""
    merged = {"characters": [], "places": [], "terms": [], "style_rules": ""}

    for category in ("characters", "places", "terms"):
        seen = {}  # korean → entry (last wins)
        for layer in (common, genre, work):
            for entry in layer.get(category, []):
                seen[entry["korean"]] = entry
        merged[category] = list(seen.values())

    # style_rules: concatenate in order (common → genre → work)
    parts = [layer.get("style_rules", "").strip() for layer in (common, genre, work)]
    merged["style_rules"] = "\n\n".join(p for p in parts if p)

    return merged


def load_merged_glossary(project: dict) -> dict:
    """Load all 3 layers and return merged glossary."""
    common_path, genre_path, work_path = get_glossary_paths(project)
    common = load_glossary_layer(common_path)
    genre = load_glossary_layer(genre_path)
    work = load_glossary_layer(work_path)
    return merge_glossaries(common, genre, work)


# =============================================================================
# Text scanning & prompt building
# =============================================================================

def scan_text_for_glossary(text: str, glossary: dict) -> dict:
    """Scan input text and return only matching glossary entries (substring match)."""
    matched = {"characters": [], "places": [], "terms": []}

    for char in glossary.get("characters", []):
        if char["korean"] in text:
            matched["characters"].append(char)

    for place in glossary.get("places", []):
        if place["korean"] in text:
            matched["places"].append(place)

    for term in glossary.get("terms", []):
        if term["korean"] in text:
            matched["terms"].append(term)

    return matched


def build_glossary_json(matched: dict) -> str:
    """Build compact JSON glossary string for system prompt."""
    compact = {}
    if matched["characters"]:
        compact["characters"] = [
            {"ko": c["korean"], "en": c["english"], "g": c["gender"][0]}
            for c in matched["characters"]
        ]
    if matched["places"]:
        compact["places"] = [
            {"ko": p["korean"], "en": p["english"]}
            for p in matched["places"]
        ]
    if matched["terms"]:
        compact["terms"] = [
            {"ko": t["korean"], "en": t["english"]}
            for t in matched["terms"]
        ]
    return "GLOSSARY (use these exact translations):\n" + json.dumps(compact, ensure_ascii=False)


# =============================================================================
# Import from DOCX
# =============================================================================

def import_glossary_from_docx(file) -> dict:
    """Parse prompt.docx and extract glossary data."""
    doc = Document(file)
    full_text = "\n".join(p.text for p in doc.paragraphs)

    glossary = {"characters": [], "places": [], "terms": [], "style_rules": ""}

    # Parse characters
    for m in re.finditer(
        r"<korean>([^<]+?)(?:\[([MFN])\])?</korean>\s*<english>([^<]+)</english>"
        r"(?:\s*<gender>([^<]+)</gender>)?",
        full_text,
    ):
        korean = re.sub(r"\[[MFN]\]", "", m.group(1)).strip()
        english = m.group(3).strip()
        gender = m.group(4).strip() if m.group(4) else "unknown"
        glossary["characters"].append(
            {"korean": korean, "english": english, "gender": gender}
        )

    # Parse places
    for m in re.finditer(
        r"<place>\s*<korean>([^<]+)</korean>\s*<english>([^<]+)</english>\s*</place>",
        full_text,
    ):
        glossary["places"].append(
            {"korean": m.group(1).strip(), "english": m.group(2).strip()}
        )

    # Parse terms
    for m in re.finditer(
        r"<term>\s*<korean>([^<]+)</korean>\s*<english>([^<]+)</english>\s*</term>",
        full_text,
    ):
        glossary["terms"].append(
            {"korean": m.group(1).strip(), "english": m.group(2).strip()}
        )

    # Extract style rules
    style_parts = []
    for tag in ("style_guide", "critical_rules", "translation_planning", "instructions", "output_format"):
        pattern = rf"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, full_text, re.DOTALL)
        if match:
            style_parts.append(f"<{tag}>{match.group(1)}</{tag}>")

    glossary["style_rules"] = "\n\n".join(style_parts)
    return glossary


# =============================================================================
# Legacy migration
# =============================================================================

def migrate_legacy_glossary():
    """One-time migration: split legacy glossary.json into 3 layers.

    - common: 13 honorifics + style_guide/critical_rules/output_format
    - genre/fantasy: empty
    - work/neo-incheon-hero: everything else + translation_planning/instructions

    The original glossary.json is preserved as-is (backup).
    """
    if not os.path.exists(LEGACY_GLOSSARY_PATH):
        return False

    legacy = load_glossary_layer(LEGACY_GLOSSARY_PATH)
    style_rules = legacy.get("style_rules", "")

    # --- Split style_rules ---
    common_style_tags = ("style_guide", "critical_rules", "output_format")
    work_style_tags = ("translation_planning", "instructions")

    def extract_tags(text, tags):
        parts = []
        for tag in tags:
            match = re.search(rf"<{tag}>.*?</{tag}>", text, re.DOTALL)
            if match:
                parts.append(match.group(0))
        return "\n\n".join(parts)

    common_style = extract_tags(style_rules, common_style_tags)
    work_style = extract_tags(style_rules, work_style_tags)

    # --- Split terms ---
    common_terms = []
    work_terms = []
    for term in legacy.get("terms", []):
        if term["korean"] in COMMON_HONORIFICS:
            common_terms.append(term)
        else:
            work_terms.append(term)

    # --- Build layers ---
    common_glossary = {
        "characters": [],
        "places": [],
        "terms": common_terms,
        "style_rules": common_style,
    }
    genre_glossary = {**EMPTY_GLOSSARY}
    work_glossary = {
        "characters": legacy.get("characters", []),
        "places": legacy.get("places", []),
        "terms": work_terms,
        "style_rules": work_style,
    }

    # --- Write ---
    os.makedirs(os.path.join(GLOSSARIES_DIR, "common"), exist_ok=True)
    os.makedirs(os.path.join(GLOSSARIES_DIR, "genres", "fantasy"), exist_ok=True)
    os.makedirs(os.path.join(GLOSSARIES_DIR, "works", "neo-incheon-hero"), exist_ok=True)

    save_glossary_layer(common_glossary, os.path.join(GLOSSARIES_DIR, "common", "glossary.json"))
    save_glossary_layer(genre_glossary, os.path.join(GLOSSARIES_DIR, "genres", "fantasy", "glossary.json"))
    save_glossary_layer(work_glossary, os.path.join(GLOSSARIES_DIR, "works", "neo-incheon-hero", "glossary.json"))

    # Ensure projects.json exists
    if not os.path.exists(PROJECTS_PATH):
        save_projects({
            "projects": [{
                "id": "neo-incheon-hero",
                "name": "Neo Incheon Hero",
                "genre": "fantasy",
                "work": "neo-incheon-hero",
            }],
            "genres": ["fantasy", "scifi", "romance"],
            "active_project": "neo-incheon-hero",
        })

    return True
