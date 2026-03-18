"""
evolve.py — The taste evolution loop.

Study → generate → screenshot → score → keep or discard → mutate → repeat.
Every TASTE_REFINE_EVERY generations, re-study the references and sharpen
the taste profile with everything learned so far.

Run this and walk away.
"""

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from generate import generate_design, save_generation, load_taste_profile
from screenshot import screenshot_sync
from score import score_generation, save_score
from study import load_references, study_references, save_profile, load_latest_profile

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GENERATIONS = int(os.getenv("AUTOTASTE_GENERATIONS", "300"))
KEEP_TOP_N = 3
SCORE_THRESHOLD = 15
TASTE_REFINE_EVERY = 10    # re-study references every N generations
HISTORY_DIR = Path("history")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def load_state() -> dict:
    state_path = HISTORY_DIR / "state.json"
    if state_path.exists():
        return json.loads(state_path.read_text())
    return {
        "current_gen": 0,
        "best_score": 0,
        "best_gen": None,
        "survivors": [],
        "profile_version": 1,
    }


def save_state(state: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    (HISTORY_DIR / "state.json").write_text(json.dumps(state, indent=2))


def append_csv(gen_num: int, score: int, parent: int | None, critique: str) -> None:
    csv_path = HISTORY_DIR / "evolution.csv"
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["gen", "score", "parent", "timestamp", "critique_preview"])
        writer.writerow([
            gen_num, score, parent or "",
            datetime.now(timezone.utc).isoformat(),
            critique[:120] if critique else "",
        ])


def update_best(state: dict, gen_num: int, score: int) -> None:
    if score > state["best_score"]:
        state["best_score"] = score
        state["best_gen"] = gen_num
        (HISTORY_DIR / "best.json").write_text(json.dumps({
            "gen": gen_num, "score": score,
            "path": f"gen_{gen_num:03d}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, indent=2))


def pick_parent(state: dict) -> tuple[str | None, str | None, int | None, str | None]:
    if not state["survivors"]:
        return None, None, None, None

    best = max(state["survivors"], key=lambda s: s["score"])
    gen_dir = HISTORY_DIR / f"gen_{best['gen']:03d}"

    critique = suggestion = None
    score_path = gen_dir / "score.json"
    if score_path.exists():
        sd = json.loads(score_path.read_text())
        critique = sd.get("critique", "")
        suggestion = sd.get("suggestion", "")

    previous_html = None
    html_path = gen_dir / "design.html"
    if html_path.exists():
        previous_html = html_path.read_text()

    return critique, suggestion, best["score"], previous_html


def gather_learnings(state: dict, last_n: int = 10) -> str:
    """Gather taste deltas and critiques from recent generations."""
    learnings = []
    start = max(1, state["current_gen"] - last_n + 1)

    for gen_num in range(start, state["current_gen"] + 1):
        gen_dir = HISTORY_DIR / f"gen_{gen_num:03d}"

        score_path = gen_dir / "score.json"
        if score_path.exists():
            sd = json.loads(score_path.read_text())
            score = sd.get("score", 0)
            critique = sd.get("critique", "")
            delta = sd.get("taste_delta", {})
            learned = delta.get("learned", "")
            revise = delta.get("revise", "")

            learnings.append(
                f"Gen {gen_num} (score {score}): {critique[:200]}"
                + (f" Learned: {learned}" if learned else "")
                + (f" Revise: {revise}" if revise else "")
            )

    return "\n".join(learnings)


def refine_taste(state: dict, images: list[dict]) -> None:
    """Re-study references with accumulated learnings."""
    previous_profile, prev_version = load_latest_profile()
    learnings = gather_learnings(state)
    new_version = prev_version + 1

    print(f"  refining taste profile (v{prev_version} → v{new_version})...")

    refined = study_references(
        images,
        previous_profile=previous_profile,
        learnings=learnings,
        generations_run=state["current_gen"],
    )

    save_profile(refined, version=new_version)
    state["profile_version"] = new_version
    print(f"  taste profile v{new_version} saved (confidence: {refined.get('confidence', '?')})")


def generate_summary(state: dict) -> None:
    csv_path = HISTORY_DIR / "evolution.csv"
    if not csv_path.exists():
        return

    rows = list(csv.DictReader(open(csv_path)))
    if not rows:
        return

    scores = [int(r["score"]) for r in rows]
    early = scores[:10]
    late = scores[-10:]

    # copy final taste profile
    profile, version = load_latest_profile()
    if profile:
        (HISTORY_DIR / "taste_profile_final.json").write_text(json.dumps(profile, indent=2))

    summary = f"""# Taste Evolution Summary

**Total generations:** {len(rows)}
**Best taste alignment:** {max(scores)}/100 (gen {state.get('best_gen', '?')})
**Average:** {sum(scores)/len(scores):.1f}/100
**Taste profile versions:** {state.get('profile_version', 1)}

## Trajectory

- First 10 avg: {sum(early)/len(early):.1f}
- Last 10 avg: {sum(late)/len(late):.1f}
- Improvement: {sum(late)/len(late) - sum(early)/len(early):+.1f} points

## Best generation

Open `gen_{state.get('best_gen', 0):03d}/design.html` in your browser.

## Taste profile evolution

The taste profile was refined {state.get('profile_version', 1)} times during this run.
Compare `taste_profile_v1.json` to `taste_profile_final.json` to see how the agent's
understanding of your references sharpened over time.

---

*Generated by autotaste at {datetime.now(timezone.utc).isoformat()}*
"""
    (HISTORY_DIR / "summary.md").write_text(summary)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run():
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    start_gen = state["current_gen"] + 1

    # load references once (they don't change)
    print("loading references...")
    images = load_references()
    if not images:
        print("\nERROR: No images in references/. Add some and run study.py first.")
        sys.exit(1)

    # check for taste profile
    profile = load_taste_profile()

    print("\n" + "=" * 60)
    print("autotaste — taste evolution")
    print("=" * 60)
    print(f"references: {len(images)} images")
    print(f"taste profile: v{state.get('profile_version', 1)} (confidence: {profile.get('confidence', '?')})")
    print(f"starting from generation {start_gen}")
    print(f"target: {GENERATIONS} generations")
    print(f"taste refinement every {TASTE_REFINE_EVERY} generations")
    if state["best_gen"]:
        print(f"current best: {state['best_score']}/100 (gen {state['best_gen']})")
    print("=" * 60 + "\n")

    for gen_num in range(start_gen, GENERATIONS + 1):
        cycle_start = time.time()

        # taste refinement check
        if gen_num > 1 and (gen_num - 1) % TASTE_REFINE_EVERY == 0:
            refine_taste(state, images)
            profile = load_taste_profile()  # reload

        print(f"--- generation {gen_num}/{GENERATIONS} ---")

        critique, suggestion, parent_score, previous_html = pick_parent(state)
        parent_gen = None

        if previous_html:
            parent_gen = max(state["survivors"], key=lambda s: s["score"])["gen"]
            print(f"  parent: gen {parent_gen} (score {parent_score})")
        else:
            print("  fresh generation")

        # generate
        print("  generating design...")
        try:
            html = generate_design(
                profile,
                critique=critique,
                suggestion=suggestion,
                score=parent_score,
                previous_html=previous_html,
            )
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        gen_dir = save_generation(gen_num, html)
        size_kb = len(html.encode("utf-8")) / 1024
        print(f"  design.html ({size_kb:.1f} KB)")

        # screenshot
        print("  screenshotting...")
        try:
            screenshot_sync(gen_dir / "design.html", gen_dir)
        except Exception as e:
            print(f"  screenshot error: {e}")

        # score
        print("  scoring taste alignment...")
        try:
            score_data = score_generation(gen_dir)
            save_score(gen_dir, score_data)
        except Exception as e:
            print(f"  scoring error: {e}")
            score_data = {"score": 0, "critique": str(e), "suggestion": ""}
            save_score(gen_dir, score_data)

        score = score_data.get("score", 0)
        critique_text = score_data.get("critique", "")

        # meta
        meta = {
            "gen": gen_num, "parent": parent_gen, "score": score,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "profile_version": state.get("profile_version", 1),
            "html_size_kb": round(size_kb, 1),
        }
        (gen_dir / "meta.json").write_text(json.dumps(meta, indent=2))

        # selection
        append_csv(gen_num, score, parent_gen, critique_text)

        if score >= SCORE_THRESHOLD:
            state["survivors"].append({"gen": gen_num, "score": score})
            state["survivors"] = sorted(
                state["survivors"], key=lambda s: s["score"], reverse=True
            )[:KEEP_TOP_N]

        update_best(state, gen_num, score)
        state["current_gen"] = gen_num
        save_state(state)

        # report
        elapsed = time.time() - cycle_start
        print(f"  taste alignment: {score}/100")
        delta = score_data.get("taste_delta", {})
        if delta.get("learned"):
            print(f"  learned: {delta['learned'][:100]}")
        print(f"  best: {state['best_score']}/100 (gen {state['best_gen']})")
        print(f"  cycle: {elapsed:.1f}s\n")

    generate_summary(state)
    print("=" * 60)
    print("evolution complete.")
    print(f"best: gen {state['best_gen']} — taste alignment {state['best_score']}/100")
    print(f"taste profile evolved through {state.get('profile_version', 1)} versions")
    print(f"open history/gen_{state['best_gen']:03d}/design.html in your browser")
    print("=" * 60)


if __name__ == "__main__":
    run()
