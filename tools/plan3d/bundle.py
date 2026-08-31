"""Bundle the viewer into a single self-contained page.

The published page has to carry everything it needs, so the floor textures are
re-encoded as WebP and inlined as data URIs alongside the geometry.

Usage:  python -m tools.plan3d.bundle
"""

from __future__ import annotations

import argparse
import base64
import io
import json
from pathlib import Path

from PIL import Image

TEXTURE_WIDTH = 1100
TEXTURE_QUALITY = 78


def inline_textures(model: dict, src: Path) -> dict:
    tex = model["texture"]
    img = Image.open(src / tex.pop("file"))
    scaled = img.resize(
        (TEXTURE_WIDTH, round(img.height * TEXTURE_WIDTH / img.width)), Image.LANCZOS
    )
    buf = io.BytesIO()
    scaled.save(buf, "WEBP", quality=TEXTURE_QUALITY, method=4)
    tex["data"] = "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()
    return model


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--viz", type=Path, default=here.parent.parent / "viz")
    args = ap.parse_args()

    src = args.viz / "src"
    data = json.loads((args.viz / "units.json").read_text())
    data["units"] = [inline_textures(u, args.viz) for u in data["units"]]

    page = "\n".join(
        [
            (src / "head.html").read_text(),
            (src / "body.html").read_text(),
            "<script>window.__UNITS__=" + json.dumps(data, separators=(",", ":")) + ";</script>",
            "<script>",
            (src / "viewer.js").read_text(),
            "</script>",
        ]
    )
    out = args.viz / "index.html"
    out.write_text(page)
    print(f"wrote {out} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    main()
