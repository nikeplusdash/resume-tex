#!/usr/bin/env python3
import json
import sys
from pathlib import Path

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    print("Error: jinja2 not installed. Run: pip install jinja2")
    sys.exit(1)

ROOT = Path(__file__).parent
OUTPUT = ROOT / "doc_output.tex"

with open(ROOT / "constant.json") as f:
    data = json.load(f)

with open(ROOT / "content.json") as f:
    data.update(json.load(f))

env = Environment(
    loader=FileSystemLoader(ROOT),
    variable_start_string="((",
    variable_end_string="))",
    block_start_string="(%",
    block_end_string="%)",
    comment_start_string="(#",
    comment_end_string="#)",
    trim_blocks=True,
    lstrip_blocks=True,
)

template = env.get_template("template.tex")
OUTPUT.write_text(template.render(**data))
print(f"Written: {OUTPUT}")
