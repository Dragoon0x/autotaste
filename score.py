"""
score.py — Taste alignment scoring.

The scoring model doesn't use a rubric. It uses comparison.
It sees the original references and the current generation side by side
and answers: does this feel like it belongs in the same collection?

Returns a taste alignment score and a critique that drives the next generation.
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
SCORE_MODEL = os.getenv("AUTOTASTE_SCORE_MODEL", "claude-sonnet-4-20250514")
REFERENCES_DIR = Path("references")
HISTORY_DIR = Path("history")

client = anthropic.Anthropic()

# ---------------------------------------------------------------------------
# Scoring prompt
# ---------------------------------------------------------------------------

SCORING_PROMPT = """You are a design critic with extraordinary sensitivity to aesthetic coherence.

You are being shown two things:
1. A collection of REFERENCE images. These represent a specific taste, a design sensibility.
2. A GENERATED design (screenshots at desktop and mobile sizes).

Your job: evaluate whether the generated design shares the same sensibility as the references.

Not whether it copied the references. Whether it BELONGS with them. Would you pin this on
the same mood board? Does it feel like the same designer made it? Does it breathe the same way?

Think about:
- Does the spacing philosophy match? Same generosity, same rhythm?
- Does the typography approach match? Same hierarchy strategy, same personality?
- Does the color usage match? Same restraint or expressiveness, same temperature?
- Does the overall mood match? Same confidence level, same energy?
- Does the layout logic match? Same structural decisions?
- What's the GAP? Where does the generated design drift from the references' sensibility?

CURRENT TASTE PROFILE (the agent's understanding of the references):
{taste_profile}

Score on a scale of 0-100:
- 0-30: Different universe. No shared sensibility.
- 30-50: Surface similarities but the soul is different.
- 50-70: Getting there. Principles are right, execution is off.
- 70-85: Belongs in the collection. A designer would recognize the kinship.
- 85-100: Indistinguishable in sensibility.

Return ONLY valid JSON:
{
  "score": 0-100,
  "taste_alignment": {
    "spacing": {"aligned": true/false, "notes": "specific observation"},
    "typography": {"aligned": true/false, "notes": "..."},
    "color": {"aligned": true/false, "notes": "..."},
    "mood": {"aligned": true/false, "notes": "..."},
    "layout": {"aligned": true/false, "notes": "..."},
    "interaction": {"aligned": true/false, "notes": "..."}
  },
  "what_matches": ["specific things that feel right", "..."],
  "what_drifts": ["specific things that feel off", "..."],
  "critique": "4-6 sentences of design critique focused on closing the taste gap. Be specific about what to change and what to preserve. Write this as creative direction.",
  "suggestion": "One specific, concrete change that would most improve taste alignment.",
  "taste_delta": {
    "learned": "One thing this generation revealed about the taste profile that wasn't obvious before",
    "revise": "One principle in the taste profile that should be revised based on this generation's results"
  }
}"""


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def load_reference_images(max_refs: int = 5) -> list[dict]:
    """Load reference images for comparison. Cap at max_refs to manage context."""
    images = []
    extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

    for path in sorted(REFERENCES_DIR.iterdir()):
        if path.suffix.lower() in extensions and len(images) < max_refs:
            with open(path, "rb") as f:
                data = base64.standard_b64encode(f.read()).decode("utf-8")
            suffix = path.suffix.lower()
            media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
            images.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_types.get(suffix, "image/png"), "data": data},
            })
    return images


def load_screenshot(path: Path) -> dict | None:
    if not path.exists():
        return None
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": data},
    }


def load_taste_profile() -> dict:
    """Load the latest taste profile."""
    latest = None
    for path in sorted(HISTORY_DIR.glob("taste_profile_v*.json")):
        try:
            latest = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
    final = HISTORY_DIR / "taste_profile_final.json"
    if final.exists():
        try:
            latest = json.loads(final.read_text())
        except json.JSONDecodeError:
            pass
    return latest or {}


def score_generation(gen_dir: Path, html_content: str | None = None) -> dict:
    """Score a generation's taste alignment against references."""

    taste_profile = load_taste_profile()

    # build content: references, then generated screenshots, then prompt
    content = []

    # reference images
    refs = load_reference_images()
    content.append({"type": "text", "text": "=== REFERENCE IMAGES (the target sensibility) ==="})
    for i, img in enumerate(refs):
        content.append({"type": "text", "text": f"[Reference {i + 1}]"})
        content.append(img)

    # generated screenshots
    content.append({"type": "text", "text": "\n=== GENERATED DESIGN (evaluate this) ==="})
    for name in ["desktop", "mobile"]:
        img = load_screenshot(gen_dir / f"{name}.png")
        if img:
            content.append({"type": "text", "text": f"[Generated — {name}]"})
            content.append(img)

    # prompt
    prompt = SCORING_PROMPT.format(taste_profile=json.dumps(taste_profile, indent=2))
    content.append({"type": "text", "text": prompt})

    response = client.messages.create(
        model=SCORE_MODEL,
        max_tokens=3000,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    return json.loads(text)


def save_score(gen_dir: Path, score_data: dict) -> None:
    (gen_dir / "score.json").write_text(json.dumps(score_data, indent=2))

    # also save taste delta separately for easy aggregation
    delta = score_data.get("taste_delta", {})
    if delta:
        (gen_dir / "taste_delta.json").write_text(json.dumps(delta, indent=2))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run score.py <gen_dir>")
        sys.exit(1)

    gen_dir = Path(sys.argv[1])
    if not gen_dir.exists():
        print(f"ERROR: {gen_dir} not found.")
        sys.exit(1)

    print(f"scoring {gen_dir} against references...")
    score_data = score_generation(gen_dir)
    save_score(gen_dir, score_data)

    print(f"\ntaste alignment: {score_data['score']}/100")
    print(f"\ncritique: {score_data.get('critique', '')}")
    print(f"\nsuggestion: {score_data.get('suggestion', '')}")
    if score_data.get("taste_delta"):
        print(f"\nlearned: {score_data['taste_delta'].get('learned', '')}")
        print(f"revise: {score_data['taste_delta'].get('revise', '')}")
