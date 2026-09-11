#!/usr/bin/env python3
"""Recompile a saved application from its edited JSON. No model call.

    python3 recompile.py <Company>/<Role>/          # re-render from content.resume.json
    python3 recompile.py <folder> --cv              # from content.cv.json
    python3 recompile.py <folder> -i                # menu to merge in optional sections

Identity (name, contact, doc basename) comes from the package's constant.json via
Profile; the LaTeX template always comes from the package directory (through tailor).
Everything else is read verbatim from the folder's own content.<mode>.json, so hand
edits to that file are exactly what gets rendered.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from profile import Profile
from tailor import compile_single_page, compile_to_dir


def _folder_json_name(mode: str) -> str:
    return "content.cv.json" if mode == "cv" else "content.resume.json"


def recent_apps(base) -> list:
    """Saved applications under `base`, newest first. One dict per
    <Company>/<Role> folder that holds a content.resume.json or content.cv.json."""
    base = Path(base).expanduser()
    if not base.is_dir():
        return []
    out = []
    for role_dir in base.glob("*/*/"):
        if not role_dir.is_dir():
            continue
        modes = [m for m, fn in (("resume", "content.resume.json"),
                                 ("cv", "content.cv.json"))
                 if (role_dir / fn).is_file()]
        if not modes:
            continue
        mtime = max((role_dir / ("content.%s.json" % m)).stat().st_mtime for m in modes)
        ref = f"{role_dir.parent.name} · {role_dir.name}".replace("_", " ")
        out.append({"dir": role_dir, "ref": ref, "mtime": mtime, "modes": modes})
    out.sort(key=lambda a: a["mtime"], reverse=True)
    return out


def recompile_folder(app_dir: Path, *, root: Path = ROOT, mode: str = "resume") -> Path:
    """Render <app_dir>/content.<mode>.json merged with root constant.json identity.

    `root` locates constant.json + content.json only (Profile.load). The template is
    tailor's own. No model is called on any path.
    """
    app_dir = Path(app_dir).expanduser().resolve()
    src = app_dir / _folder_json_name(mode)
    if not src.exists():
        print(f"Error: {src.name} not found in {app_dir}")
        print("  Run tailor.py first, or pass the application folder that holds it.")
        sys.exit(1)

    profile = Profile.load(root, mode=mode)
    content = json.loads(src.read_text())
    content.pop("company_name", None)
    content.pop("job_title", None)

    data = dict(profile.identity)
    data["location"] = content.get("location") or profile.location
    data["skills_title"] = profile.skills_title
    data["projects_title"] = profile.projects_title
    data.update(content)

    filename = f"{profile.doc_basename}_{'CV' if mode == 'cv' else 'Resume'}.pdf"
    if mode == "resume":
        pages, _ = compile_single_page(data, app_dir, filename)
    else:
        pages = compile_to_dir(data, app_dir, filename)

    pdf = app_dir / filename
    print(f"Saved PDF : {pdf}")
    print("Pages     : 1 (single page OK)" if pages == 1 else f"Warning   : {pages} pages")
    return pdf


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", default=".", help="application folder (default: CWD)")
    ap.add_argument("--cv", action="store_true", help="use content.cv.json")
    ap.add_argument("-i", "--interactive", action="store_true",
                    help="menu to merge in optional sections before recompiling")
    args = ap.parse_args()

    mode = "cv" if args.cv else "resume"
    app_dir = Path(args.folder).expanduser().resolve()

    if args.interactive:
        from recompile_menu import run_menu   # Task 11 — not present yet
        run_menu(app_dir, root=ROOT, mode=mode)

    recompile_folder(app_dir, root=ROOT, mode=mode)


if __name__ == "__main__":
    main()
