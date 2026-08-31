"""Turn the rendered floor-plan PDF into 3D geometry.

The plans are raster renders, not vector CAD, so the geometry is recovered by
colour-segmenting each page and tracing the resulting masks:

    walls      the uniform mid-grey the plans draw every wall in
    glazing    the pale blue strips that mark windows and patio doors
    deck       the timber decking that marks balconies
    footprint  everything that is not page white

Each mask is traced to polygons, simplified, and converted from pixels to
metres with a per-page scale calibrated against the dimensions printed on the
plan.  The result is a JSON model the viewer extrudes.

Usage:  python -m tools.plan3d.extract [--pdf PATH] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image
from scipy import ndimage
from shapely.geometry import Polygon
from skimage import measure

# ---------------------------------------------------------------------------
# per-page description of the four units
# ---------------------------------------------------------------------------

WALL_HEIGHT = 2.70
PARAPET_HEIGHT = 1.05
PARAPET_THICKNESS = 0.15
SILL_HEIGHT = 0.90
HEADER_HEIGHT = 2.15
DOOR_HEIGHT = 2.40
JOINERY_HEIGHT = 0.95


@dataclass
class Unit:
    """One page of the PDF: an apartment, and how to read it."""

    page: int
    key: str
    name: str
    # pixels per metre, calibrated from the dimensions printed on the plan
    scale: float
    rooms: list[dict] = field(default_factory=list)
    note: str = ""


UNITS: list[Unit] = [
    Unit(
        page=0,
        key="A",
        name="Unit A — 2 bed + office, wrap-around balcony",
        scale=196.5,
        rooms=[
            {"name": "Master bedroom", "dims": "3.10 × 3.30 m", "area": 10.2},
            {"name": "Living room", "dims": "3.50 × 3.50 m", "area": 12.3},
            {"name": "Dining area", "dims": "3.30 × 3.50 m", "area": 11.6},
            {"name": "Kitchen", "dims": "2.90 × 2.95 m", "area": 8.6},
            {"name": "Storage / office", "dims": "4.00 × 3.60 m", "area": 14.4},
            {"name": "Bathroom", "dims": "2.40 × 1.60 m", "area": 3.8},
            {"name": "Main balcony", "dims": "3.10 m wide", "area": None},
            {"name": "Utility balcony", "dims": "3.75 × 1.00 m", "area": 3.8},
        ],
    ),
    Unit(
        page=1,
        key="B",
        name="Unit B — 1 bed, open plan, two balconies",
        scale=190.0,
        rooms=[
            {"name": "Master bedroom", "dims": "4.00 × 3.20 m", "area": 12.8},
            {"name": "Living / dining / kitchen", "dims": "5.30 × 4.50 m", "area": 23.9},
            {"name": "Bathroom", "dims": "1.70 m wide", "area": None},
            {"name": "Main balcony", "dims": "3.45 m wide", "area": None},
            {"name": "Bedroom balcony", "dims": "3.45 m wide", "area": None},
        ],
    ),
    Unit(
        page=2,
        key="C",
        name="Unit C — 1 bed, corner unit, deep balcony",
        scale=182.1,
        rooms=[
            {"name": "Master bedroom", "dims": "3.60 × 3.50 m", "area": 12.6},
            {"name": "Living / dining / kitchen", "dims": "5.50 × 5.60 m", "area": 30.8},
            {"name": "Bathroom", "dims": "2.20 × 2.20 m", "area": 4.8},
            {"name": "Main balcony", "dims": "3.55 × 5.50 m", "area": 19.5},
        ],
    ),
    Unit(
        page=3,
        key="D",
        name="Unit D — 2 bed, separate kitchen, splayed end",
        scale=200.0,
        rooms=[
            {"name": "Master bedroom", "dims": "3.60 × 3.30 m", "area": 11.9},
            {"name": "Bedroom 2", "dims": "4.20 m wide", "area": None},
            {"name": "Living / dining", "dims": "6.50 × 3.20 m", "area": 20.8},
            {"name": "Kitchen", "dims": "3.60 × 3.30 m", "area": 11.9},
            {"name": "Bathroom", "dims": "2.20 × 2.30 m", "area": 5.1},
            {"name": "Main balcony", "dims": "—", "area": None},
            {"name": "Bedroom balcony", "dims": "—", "area": None},
        ],
    ),
]


# ---------------------------------------------------------------------------
# raster + masks
# ---------------------------------------------------------------------------

WALL_RGB = np.array([114, 114, 108])


def page_raster(doc: pymupdf.Document, page_index: int) -> np.ndarray:
    """The full-resolution bitmap the page embeds, as an HxWx3 int array."""
    page = doc[page_index]
    images = page.get_images()
    if not images:
        raise ValueError(f"page {page_index + 1} embeds no raster to trace")
    blob = doc.extract_image(images[0][0])["image"]
    tmp = Path(f"/tmp/plan3d-page{page_index}.img")
    tmp.write_bytes(blob)
    arr = np.asarray(Image.open(tmp).convert("RGB")).astype(int)
    tmp.unlink(missing_ok=True)
    return arr


def masks(rgb: np.ndarray) -> dict[str, np.ndarray]:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = rgb.mean(2)
    sat = rgb.max(2) - rgb.min(2)

    wall = np.abs(rgb - WALL_RGB).max(2) < 20
    # interior finishes: pale tile, and the darker grey of the wet areas
    tile = (sat < 16) & (lum > 202) & (lum < 240)
    wet = (sat < 14) & (lum > 122) & (lum < 168)
    # pale blue glazing strips: blue-leaning, mid-light
    glass = (b - r > 8) & (b > 150) & (lum > 135) & (lum < 220) & (sat > 8)
    # timber decking: warm, red-leaning
    deck = (r - b > 22) & (r > 145) & (r < 240) & (lum > 130) & (lum < 232)
    # planting: the renders spill pots and bougainvillea over the balcony edges
    green = (sat > 30) & (lum > 45) & (lum < 220) & ~deck
    # page white
    white = (lum > 243) & (sat < 10)

    out = {"wall": wall, "glass": glass, "deck": deck, "white": white,
           "tile": tile, "wet": wet, "green": green, "void": voids(rgb)}
    for k in ("wall", "glass", "deck"):
        out[k] = clean(out[k], min_area=200)
    return out


def structural(
    mask: np.ndarray, scale: float, edge: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Split the wall-grey mask into walls, joinery, and noise to discard.

    The plans draw walls, kitchen counters, vanities and console tables in one
    grey, and outline beds, baths and chairs in it too.  Three properties tell
    them apart:

    * the outlines are hairlines, and so are the slats of the sideboards, so
      the test is mean width - four times the mean of the distance transform,
      which is the true width for a long thin shape - rather than the thickest
      point, which a comb of thin slats would pass on its spine alone;
    * every real wall run either reaches the envelope or is long, because
      partitions here run wall to wall;
    * what is left is thick, free-standing and small - worktops and cabinets,
      which belong in the model at counter height rather than full height.
    """
    lab, n = ndimage.label(mask)
    wall = np.zeros(n + 1, bool)
    joinery = np.zeros(n + 1, bool)
    if not n:
        return mask, np.zeros_like(mask)
    dt = ndimage.distance_transform_edt(mask)
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        blob = lab[sl] == i
        width = 4.0 * float(dt[sl][blob].mean()) / scale
        area = float(blob.sum()) / scale**2
        if width < 0.11 or area < 0.09:
            continue
        anchored = bool((edge[sl] & blob).any())
        if anchored or area >= 0.60:
            wall[i] = True
        else:
            joinery[i] = True
    return wall[lab], joinery[lab]


def clean(mask: np.ndarray, min_area: int = 200, close: int = 3) -> np.ndarray:
    """Close pinholes, then drop specks (furniture that shares the wall grey)."""
    m = ndimage.binary_closing(mask, np.ones((close, close), bool))
    m = ndimage.binary_fill_holes(m)
    lab, n = ndimage.label(m)
    if n:
        sizes = ndimage.sum(m, lab, range(1, n + 1))
        keep = np.zeros(n + 1, bool)
        keep[1:][sizes >= min_area] = True
        m = keep[lab]
    return m


def voids(rgb: np.ndarray) -> np.ndarray:
    """Areas the plans flat-fill in solid grey: neighbouring units and shafts.

    They sit inside the drawing outline but are not part of the apartment, so
    they must come out of the floor and out of the area figures.  A flat fill is
    told apart from a rug or a tiled floor by how completely it fills its own
    bounding box.
    """
    lum = rgb.mean(2)
    sat = rgb.max(2) - rgb.min(2)
    flat = (sat < 10) & (lum > 160) & (lum < 212)
    flat = ndimage.binary_opening(flat, np.ones((9, 9), bool))
    lab, n = ndimage.label(flat)
    out = np.zeros_like(flat)
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        blob = lab[sl] == i
        area = int(blob.sum())
        if area < 20000 or area / blob.size < 0.75:
            continue  # a rug or a tiled floor, not a flat fill
        if float(lum[sl][blob].std()) > 9.5:
            continue  # printed texture, so it is a floor finish
        out[sl] |= blob
    return ndimage.binary_fill_holes(out)


def keep_larger_than(mask: np.ndarray, min_px: float) -> np.ndarray:
    lab, n = ndimage.label(mask)
    if not n:
        return mask
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    keep = np.zeros(n + 1, bool)
    keep[1:][sizes >= min_px] = True
    return keep[lab]


def shrink(mask: np.ndarray, radius: float) -> np.ndarray:
    """Erode by a euclidean radius, via the distance transform.

    The structuring elements here are tens of pixels across, where a distance
    transform is far cheaper than the equivalent binary morphology.
    """
    return ndimage.distance_transform_edt(mask) > radius


def grow(mask: np.ndarray, radius: float) -> np.ndarray:
    return ndimage.distance_transform_edt(~mask) <= radius


def smooth(mask: np.ndarray, radius: float) -> np.ndarray:
    """Opening then closing at the same radius: shaves spurs, keeps the body."""
    return ~grow(~grow(shrink(mask, radius), radius * 2), radius)


def footprint(m: dict[str, np.ndarray], scale: float) -> np.ndarray:
    """The slab: the largest blob of drawn content, holes filled and de-fringed.

    An opening then a closing at roughly 0.13 m shaves off the planting the
    renders spill over the balcony edges without touching the slab itself, so
    the traced outline follows the structure rather than the leaves.
    """
    content = ndimage.binary_closing(~m["white"], np.ones((9, 9), bool))
    lab, n = ndimage.label(content)
    if not n:
        return content
    sizes = ndimage.sum(content, lab, range(1, n + 1))
    content = ndimage.binary_fill_holes(lab == (int(np.argmax(sizes)) + 1))

    content = smooth(content, max(2.0, 0.13 * scale))
    return ndimage.binary_fill_holes(content)


def parapets(m: dict[str, np.ndarray], slab: np.ndarray, scale: float) -> np.ndarray:
    """Balustrades: the slab edge wherever a balcony meets open air.

    Every footprint edge is either a wall (already extruded) or the open side of
    a balcony, which needs a parapet.  Restricting to deck-adjacent edges keeps
    parapets off the doorways in the interior floor.
    """
    band = PARAPET_THICKNESS * scale
    edge = slab & ~shrink(slab, band)
    near_wall = grow(m["wall"], 5)
    near_deck = grow(m["deck"], band * 2)
    return clean(edge & ~near_wall & near_deck, min_area=int(0.25 * scale * band))


# ---------------------------------------------------------------------------
# raster -> polygons
# ---------------------------------------------------------------------------


def trace(mask: np.ndarray, scale: float, origin, tol_m: float = 0.02) -> list[dict]:
    """Trace a mask to simplified polygons in metres, outers with their holes.

    skimage traces every boundary of the mask; a contour is a hole when it sits
    inside another.  Nesting is resolved by area so holes attach to the smallest
    enclosing outer, which is what the extruder needs.
    """
    padded = np.pad(mask.astype(float), 1)
    tol_px = max(1.0, tol_m * scale)
    ox, oy = origin

    rings: list[tuple[Polygon, np.ndarray]] = []
    for contour in measure.find_contours(padded, 0.5):
        pts = np.column_stack([contour[:, 1] - 1, contour[:, 0] - 1])
        if len(pts) < 4:
            continue
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
            if poly.is_empty or poly.geom_type != "Polygon":
                continue
        poly = poly.simplify(tol_px, preserve_topology=True)
        if poly.is_empty or poly.area < (0.06 * scale * scale):
            continue
        rings.append((poly, np.asarray(poly.exterior.coords)))

    rings.sort(key=lambda rp: -rp[0].area)
    outers: list[dict] = []
    for poly, coords in rings:
        centre = poly.representative_point()
        parent = None
        for cand in outers:
            if cand["_poly"].contains(centre):
                parent = cand  # rings are area-sorted, so keep the tightest
        ring = [[round((x - ox) / scale, 4), round((y - oy) / scale, 4)] for x, y in coords]
        if parent is None:
            outers.append({"_poly": poly, "outer": ring, "holes": []})
        else:
            parent["holes"].append(ring)

    for o in outers:
        o.pop("_poly")
    return outers


def strips(mask: np.ndarray, scale: float, origin, deck: np.ndarray) -> list[dict]:
    """Glazing strips as oriented rectangles, tagged window or patio door."""
    lab, n = ndimage.label(mask)
    near_deck = ndimage.binary_dilation(deck, np.ones((25, 25), bool))
    ox, oy = origin
    out = []
    for i in range(1, n + 1):
        blob = lab == i
        if blob.sum() < 150:
            continue
        ys, xs = np.nonzero(blob)
        pts = np.column_stack([xs, ys]).astype(float)
        centre = pts.mean(0)
        # principal axis, so strips in splayed walls stay aligned with the wall
        u, s, vt = np.linalg.svd(pts - centre, full_matrices=False)
        axis = vt[0]
        along = (pts - centre) @ axis
        across = (pts - centre) @ vt[1]
        length = float(along.max() - along.min()) / scale
        width = max(float(across.max() - across.min()) / scale, 0.10)
        if length < 0.45:
            continue
        is_door = bool(near_deck[blob].mean() > 0.35) and length >= 1.30
        out.append(
            {
                "centre": [round((centre[0] - ox) / scale, 4), round((centre[1] - oy) / scale, 4)],
                "angle": round(math.atan2(axis[1], axis[0]), 5),
                "length": round(length, 3),
                "width": round(min(width, 0.40), 3),
                "kind": "door" if is_door else "window",
            }
        )
    return out


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


def texture(rgb: np.ndarray, slab: np.ndarray, out: Path, max_width: int = 1500) -> str:
    """The plan itself, background removed, as the floor texture."""
    h, w = slab.shape
    alpha = (grow(slab, 3) * 255).astype("uint8")
    img = Image.fromarray(
        np.dstack([np.clip(rgb, 0, 255).astype("uint8"), alpha]), mode="RGBA"
    )
    if w > max_width:
        img = img.resize((max_width, round(h * max_width / w)), Image.LANCZOS)
    img.save(out, optimize=True)
    return out.name


def build(unit: Unit, doc: pymupdf.Document, outdir: Path) -> dict:
    rgb = page_raster(doc, unit.page)
    m = masks(rgb)
    outline = footprint(m, unit.scale)
    void = m["void"] & outline
    slab = outline & ~void
    # an opening at 0.04 m clears the hairline outlines and the slats of the
    # timber sideboards, which the trace would otherwise raise to wall height
    # because they touch the wall network they sit against
    solid = grow(shrink(m["wall"], 0.04 * unit.scale), 0.04 * unit.scale)
    m["wall"], joinery = structural(
        solid, unit.scale, slab & ~shrink(slab, 0.35 * unit.scale)
    )
    # keep only decking big enough to be a balcony: the plans also draw timber
    # wardrobes and platforms indoors in the same tone
    # planting sitting on a balcony still reads as balcony floor
    near_deck = grow(m["deck"], 0.25 * unit.scale)
    deck = (m["deck"] | (m["green"] & near_deck)) & slab
    deck = ~grow(~grow(deck, 0.10 * unit.scale), 0.10 * unit.scale)
    deck = keep_larger_than(deck & slab, 2.0 * unit.scale**2)
    # a balcony always reaches the edge of the slab; a timber wardrobe or a TV
    # unit drawn in the same tone never does
    edge = slab & ~shrink(slab, 0.30 * unit.scale)
    lab, n = ndimage.label(deck)
    keep = np.zeros(n + 1, bool)
    for i in range(1, n + 1):
        keep[i] = bool((edge & (lab == i)).sum() > 0.20 * unit.scale)
    m["deck"] = keep[lab]
    deck_only = m["deck"]
    rail = parapets(m, slab, unit.scale)

    ys, xs = np.nonzero(outline)
    origin = (float(xs.min()), float(ys.min()))
    h, w = slab.shape

    model = {
        "key": unit.key,
        "name": unit.name,
        "scale_px_per_m": unit.scale,
        "size_m": [round((xs.max() - xs.min()) / unit.scale, 3),
                   round((ys.max() - ys.min()) / unit.scale, 3)],
        "heights": {
            "wall": WALL_HEIGHT,
            "parapet": PARAPET_HEIGHT,
            "sill": SILL_HEIGHT,
            "header": HEADER_HEIGHT,
            "door": DOOR_HEIGHT,
            "joinery": JOINERY_HEIGHT,
        },
        "slab": trace(slab, unit.scale, origin, tol_m=0.07),
        "voids": trace(void, unit.scale, origin, tol_m=0.04),
        "walls": trace(m["wall"], unit.scale, origin),
        "joinery": trace(joinery, unit.scale, origin, tol_m=0.03),
        "parapets": trace(rail, unit.scale, origin, tol_m=0.03),
        "deck": trace(deck_only, unit.scale, origin, tol_m=0.05),
        "glazing": strips(m["glass"], unit.scale, origin, m["deck"]),
        "rooms": unit.rooms,
        "texture": {
            "file": texture(rgb, slab, outdir / f"unit{unit.key}.png"),
            # texture spans the full page raster; give the viewer its extent in
            # model space so it lines up with the traced geometry
            "origin_m": [round(-origin[0] / unit.scale, 4), round(-origin[1] / unit.scale, 4)],
            "size_m": [round(w / unit.scale, 4), round(h / unit.scale, 4)],
        },
    }

    inside = slab & ~m["deck"]
    model["areas_m2"] = {
        # net internal: floor you can stand on, walls and shafts excluded
        "internal_net": round(float((inside & ~m["wall"]).sum()) / unit.scale**2, 1),
        # gross internal: to the outside face of the envelope, as agents quote it
        "internal_gross": round(float(inside.sum()) / unit.scale**2, 1),
        "balcony": round(float(m["deck"].sum()) / unit.scale**2, 1),
    }
    model["areas_m2"]["total_gross"] = round(
        model["areas_m2"]["internal_gross"] + model["areas_m2"]["balcony"], 1
    )
    return model


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", type=Path, default=here / "floorplans.pdf")
    ap.add_argument("--out", type=Path, default=here.parent.parent / "viz")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(args.pdf)
    models = []
    for unit in UNITS:
        model = build(unit, doc, args.out)
        models.append(model)
        print(
            f"unit {model['key']}: {len(model['walls'])} wall shapes, "
            f"{len(model['glazing'])} openings, {len(model['parapets'])} parapets, "
            f"{len(model['joinery'])} joinery, "
            f"{model['areas_m2']['internal_net']} m2 net internal "
            f"({model['areas_m2']['internal_gross']} gross) + "
            f"{model['areas_m2']['balcony']} m2 balcony, "
            f"envelope {model['size_m'][0]}x{model['size_m'][1]} m"
        )

    target = args.out / "units.json"
    target.write_text(json.dumps({"units": models}, indent=1))
    print(f"wrote {target} ({target.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
