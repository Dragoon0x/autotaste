"""
generate.py — Design generation guided by taste.

Reads the taste profile (not a spec) and produces a self-contained
design.html file. The agent decides what to make based on what it
learned from the references.

No spec. No brief. Just taste.
"""

import anthropic
import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL = os.getenv("AUTOTASTE_MODEL", "claude-sonnet-4-20250514")
HISTORY_DIR = Path("history")

client = anthropic.Anthropic()

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

GENERATE_PROMPT = """You are a designer with a specific, deeply held aesthetic sensibility.

Your taste has been studied and profiled. Below is your taste profile: the principles you
design by, the patterns you favor, the anti-patterns you avoid, and the mood you aim for.

Your job: create a single, self-contained HTML file that embodies this sensibility.
You decide what to make. A landing page, a dashboard, a tool, an editorial layout,
whatever feels most natural for this aesthetic. The content can be fictional but it
should feel real and considered, not lorem ipsum.

The design should feel like it BELONGS with the reference images that produced this
taste profile. Not copied from them. Inspired by the same sensibility.

Rules:
- Single HTML file. All CSS and JS inline.
- No external dependencies. No CDNs. No Google Fonts.
- Clean, semantic HTML.
- The design should feel finished. Hover states, focus states, responsive behavior.
- Works on desktop (1440px) and mobile (375px).
- Every design choice should connect back to the taste profile. If a principle says
  "typography carries hierarchy," then your design must demonstrate that.

Return ONLY the complete HTML file. Start with <!DOCTYPE html>. No markdown fences.
No explanation.

YOUR TASTE PROFILE:
{taste_profile}

{mutation_context}"""

MUTATE_PROMPT_SECTION = """
EVOLUTION CONTEXT:
This is generation N+1. The previous generation scored {score}/100 on taste alignment.

The taste critic's feedback:
{critique}

Specific suggestion:
{suggestion}

Previous generation's HTML (evolve it, don't restart):

```html
{previous_html}
```

Use the feedback to close the gap between your output and the references. Keep what
resonated. Fix what felt off. The goal is higher taste alignment, not a different design.
"""


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def load_taste_profile() -> dict:
    """Load the latest taste profile."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    latest = None
    for path in sorted(HISTORY_DIR.glob("taste_profile_v*.json")):
        try:
            latest = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue

    # also check for final
    final = HISTORY_DIR / "taste_profile_final.json"
    if final.exists():
        try:
            latest = json.loads(final.read_text())
        except json.JSONDecodeError:
            pass

    if not latest:
        print("ERROR: No taste profile found. Run study.py first.")
        sys.exit(1)

    return latest


def call_model(prompt: str, max_tokens: int = 16000) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def clean_html(text: str) -> str:
    text = re.sub(r"^```(?:html)?\s*\n?", "", text)
    text = re.sub(r"\n?\s*```$", "", text)
    return text.strip()


def build_mutation_context(
    critique: str | None,
    suggestion: str | None,
    score: int | None,
    previous_html: str | None,
) -> str:
    if not critique or not previous_html:
        return ""
    html = previous_html
    if len(html) > 30000:
        html = html[:15000] + "\n<!-- ... truncated ... -->\n" + html[-15000:]
    return MUTATE_PROMPT_SECTION.format(
        score=score or "?",
        critique=critique or "",
        suggestion=suggestion or "",
        previous_html=html,
    )


def generate_design(
    taste_profile: dict,
    critique: str | None = None,
    suggestion: str | None = None,
    score: int | None = None,
    previous_html: str | None = None,
) -> str:
    """Generate a design guided by the taste profile."""
    mutation_ctx = build_mutation_context(critique, suggestion, score, previous_html)
    prompt = GENERATE_PROMPT.format(
        taste_profile=json.dumps(taste_profile, indent=2),
        mutation_context=mutation_ctx,
    )
    result = call_model(prompt)
    return clean_html(result)


def save_generation(gen_num: int, html: str, output_dir: Path = HISTORY_DIR) -> Path:
    gen_dir = output_dir / f"gen_{gen_num:03d}"
    gen_dir.mkdir(parents=True, exist_ok=True)
    (gen_dir / "design.html").write_text(html)
    return gen_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("autotaste — generating design from taste profile\n")

    profile = load_taste_profile()
    print(f"taste profile loaded (confidence: {profile.get('confidence', '?')})")
    print(f"mood: {profile.get('mood', '?')}\n")
    print("generating design...")

    html = generate_design(profile)
    gen_dir = save_generation(1, html)

    size_kb = len(html.encode("utf-8")) / 1024
    print(f"\ndone. saved to {gen_dir}/design.html ({size_kb:.1f} KB)")
    print(f"open in browser: file://{gen_dir.resolve()}/design.html")
