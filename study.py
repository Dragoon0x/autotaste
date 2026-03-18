"""
study.py — Reference analysis.

Studies the images in /references and builds a taste profile.
This is the foundation everything else builds on. The quality of the
taste profile determines the quality of everything that follows.

Run this once before starting the evolution loop.
Re-run it (or let evolve.py do it) to refine the profile with new learnings.
"""

import anthropic
import base64
import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL = os.getenv("AUTOTASTE_MODEL", "claude-sonnet-4-20250514")
REFERENCES_DIR = Path("references")
HISTORY_DIR = Path("history")

client = anthropic.Anthropic()

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

STUDY_PROMPT = """You are a design critic with extraordinary visual sensitivity.

You are being shown a collection of reference images. These are screenshots of interfaces,
designs, or layouts that someone admires. Your job is to study them deeply and extract
the underlying design sensibility they share.

Don't describe what you see. Describe WHY it works. What design decisions create the
feeling these references share? What's the common thread? What would break if you changed it?

Think about:
- How is space used? Is it generous or tight? Structural or decorative?
- What carries the visual hierarchy? Type? Color? Space? Weight?
- How do interactive elements relate to content?
- What's the color philosophy? Warm/cool? Restrained/expressive? Natural/synthetic?
- What's the typography doing? Is it invisible or characterful? How many roles does it play?
- What's the rhythm? Vertical spacing, section breaks, breathing room.
- What mood does all of this create together?
- What's ABSENT? What choices were deliberately NOT made?

Return ONLY valid JSON:
{
  "principles": [
    "5-8 core design principles extracted from the references. Each should be specific and actionable, not generic. Not 'good typography' but 'typography carries hierarchy through weight and size contrast, not color'"
  ],
  "patterns": {
    "spacing": "specific observations about spacing patterns",
    "typography": "specific observations about type choices and usage",
    "color": "specific observations about color philosophy",
    "layout": "specific observations about layout approach",
    "interaction": "specific observations about interactive element design"
  },
  "anti_patterns": [
    "3-5 things these references clearly avoid. Be specific."
  ],
  "mood": "A 1-2 sentence description of the overall feeling. Not adjectives. A scene or sensation.",
  "confidence": 0.0-1.0,
  "notes": "Anything surprising or contradictory you noticed across the references"
}

The confidence score (0-1) reflects how consistent the references are:
- 0.9+ = very unified sensibility, clear shared taste
- 0.7-0.9 = coherent with some variation
- 0.5-0.7 = mixed signals, agent will need to make judgment calls
- Below 0.5 = contradictory references, the thread is hard to find

{refinement_context}"""

REFINE_PROMPT_SECTION = """
REFINEMENT CONTEXT:
This is not the first time studying these references. Previous analysis produced this
taste profile:

{previous_profile}

Since then, {generations_run} generations of design have been produced. Here's what
was learned during that evolution:

Top critiques and learnings:
{learnings}

Refine the taste profile. Sharpen principles that proved useful. Drop or revise
principles that didn't help close the gap. Add new principles if the evolution
revealed something the initial study missed. The refined profile should be more
precise and more useful than the previous one.
"""


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def load_references() -> list[dict]:
    """Load all reference images from the references directory."""
    if not REFERENCES_DIR.exists():
        print("ERROR: references/ directory not found.")
        sys.exit(1)

    images = []
    extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

    for path in sorted(REFERENCES_DIR.iterdir()):
        if path.suffix.lower() in extensions:
            with open(path, "rb") as f:
                data = base64.standard_b64encode(f.read()).decode("utf-8")

            # determine media type
            suffix = path.suffix.lower()
            media_types = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
            }

            images.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_types.get(suffix, "image/png"),
                    "data": data,
                },
            })
            print(f"  loaded {path.name}")

    return images


def study_references(
    images: list[dict],
    previous_profile: dict | None = None,
    learnings: str | None = None,
    generations_run: int = 0,
) -> dict:
    """Study reference images and produce a taste profile."""

    # build refinement context if this is a re-study
    refinement_ctx = ""
    if previous_profile and learnings:
        refinement_ctx = REFINE_PROMPT_SECTION.format(
            previous_profile=json.dumps(previous_profile, indent=2),
            generations_run=generations_run,
            learnings=learnings[:4000],
        )

    prompt = STUDY_PROMPT.format(refinement_context=refinement_ctx)

    # build message: all images + the study prompt
    content = []
    for i, img in enumerate(images):
        content.append({"type": "text", "text": f"[Reference {i + 1}]"})
        content.append(img)
    content.append({"type": "text", "text": prompt})

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    return json.loads(text)


def save_profile(profile: dict, version: int = 1) -> Path:
    """Save a taste profile to history."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / f"taste_profile_v{version}.json"
    path.write_text(json.dumps(profile, indent=2))
    return path


def load_latest_profile() -> tuple[dict | None, int]:
    """Load the most recent taste profile and its version number."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    version = 1
    latest = None

    for path in sorted(HISTORY_DIR.glob("taste_profile_v*.json")):
        try:
            v = int(path.stem.split("_v")[1])
            latest = json.loads(path.read_text())
            version = v
        except (ValueError, json.JSONDecodeError):
            continue

    return latest, version


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("autotaste — studying references\n")

    images = load_references()

    if not images:
        print("\nERROR: No images found in references/")
        print("Drop PNG or JPG screenshots of designs you admire into the references/ folder.")
        sys.exit(1)

    print(f"\n{len(images)} references loaded. studying...\n")

    profile = study_references(images)
    path = save_profile(profile, version=1)

    print(f"taste profile saved to {path}\n")
    print(f"confidence: {profile.get('confidence', '?')}")
    print(f"mood: {profile.get('mood', '?')}")
    print(f"\nprinciples:")
    for p in profile.get("principles", []):
        print(f"  - {p}")
    print(f"\nanti-patterns:")
    for a in profile.get("anti_patterns", []):
        print(f"  - {a}")
