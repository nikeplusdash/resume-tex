"""Interactive `recompile -i`: merge optional sections into a saved application's JSON.

No model call. No compile. Pulls a section from the package's master content.json
when present, otherwise prompts for `a | b | c` lines. The caller (recompile.py)
recompiles afterward.
"""
import json
from pathlib import Path

import ui

_SKIP_HINT = "  (skipped {label}: nothing to add — put it in content.json first)"

_SECTIONS = {
    "Education": ("education", None),
    "Certifications": ("certifications", ("name", "issuer", "date")),
    "Awards": ("awards", ("title", "body", "year")),
    "Publications": ("publications", ("title", "venue", "year")),
    "Languages": ("languages", ("language", "proficiency")),
    "Recommendations": ("recommendations", None),
}


def _parse_lines(text, fields):
    out = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == len(fields) and any(parts):
            out.append(dict(zip(fields, parts)))
    return out


def run_menu(app_dir: Path, *, root: Path, mode: str) -> None:
    app_dir = Path(app_dir)
    src = app_dir / ("content.cv.json" if mode == "cv" else "content.resume.json")
    content = json.loads(src.read_text())

    master = {}
    mp = Path(root) / "content.json"
    if mp.exists():
        master = json.loads(mp.read_text())
    recs_path = Path(root) / "recommendations.json"
    coursework_path = Path(root) / "coursework.json"

    picks = ui.checkbox("Add sections to this application:", list(_SECTIONS))
    if not picks:
        return

    for label in picks:
        key, fields = _SECTIONS[label]
        if label == "Recommendations":
            if recs_path.exists():
                content[key] = json.loads(recs_path.read_text())
            else:
                print(_SKIP_HINT.format(label=label))
            continue
        if label == "Education":
            if master.get("education"):
                entries = [dict(e) for e in master["education"]]
                if coursework_path.exists():
                    coursework = json.loads(coursework_path.read_text())
                    for entry in entries:
                        courses = coursework.get(entry.get("institution"))
                        if courses:
                            entry["coursework"] = courses
                content[key] = entries
            else:
                print(_SKIP_HINT.format(label=label))
            continue
        if master.get(key):
            content[key] = master[key]
        elif fields:
            hint = " | ".join(fields)
            parsed = _parse_lines(ui.editor(f"{label} — one per line: {hint}"), fields)
            if parsed:
                content[key] = parsed
            else:
                print(_SKIP_HINT.format(label=label))
        else:
            print(_SKIP_HINT.format(label=label))

    src.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")
