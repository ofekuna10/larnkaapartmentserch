"""Furniture read off the plan sheets, and the volumes each piece becomes.

The plans draw furniture as photo-realistic top views, so colour segmentation
cannot tell a white sofa from a white bath from the pale travertine floor
underneath both.  The pieces are therefore listed explicitly: each entry is the
footprint the plan draws, in that sheet's own pixel coordinates, which the
extractor converts to metres with the same origin and scale it uses for the
walls.  Height and form come from TYPES.

Box coordinates are (x0, y0, x1, y1) in page pixels, top-left origin.
`facing` is the direction the piece fronts onto - the side a sofa's seat or a
bed's foot points towards - as a compass letter, and places the backrest or
headboard on the opposite side.
"""

from __future__ import annotations

# form:  box     a plain volume
#        soft    upholstery, with a backrest on the side away from `facing`
#        bed     divan, mattress, headboard, pillows
#        table   a top on legs
#        round   a cylinder (the box gives the diameter)
#        plant   a pot with foliage above it
#        basin   a pedestal with a bowl on top
#        wc      a pan with a cistern behind it
#        tub     a rim with the water recessed inside
TYPES: dict[str, dict] = {
    "bed_double":   {"h": 0.55, "form": "bed",   "mat": "linen"},
    "bed_single":   {"h": 0.55, "form": "bed",   "mat": "linen"},
    "wardrobe":     {"h": 2.10, "form": "box",   "mat": "timber"},
    "nightstand":   {"h": 0.50, "form": "box",   "mat": "timber"},
    "sofa":         {"h": 0.42, "form": "soft",  "mat": "upholstery", "back": 0.78},
    "sofa_outdoor": {"h": 0.38, "form": "soft",  "mat": "cushion",    "back": 0.66},
    "armchair":     {"h": 0.40, "form": "soft",  "mat": "accent",     "back": 0.74},
    "chair":        {"h": 0.45, "form": "soft",  "mat": "timber",     "back": 0.88},
    "coffee_table": {"h": 0.38, "form": "table", "mat": "stone"},
    "side_table":   {"h": 0.50, "form": "round", "mat": "timber"},
    "coffee_round": {"h": 0.38, "form": "round", "mat": "stone"},
    "office_chair": {"h": 0.46, "form": "round", "mat": "upholstery"},
    "console":      {"h": 0.78, "form": "box",   "mat": "timber"},
    "tv_unit":      {"h": 0.45, "form": "box",   "mat": "timber"},
    "dining_table": {"h": 0.74, "form": "table", "mat": "timber"},
    "table_round":  {"h": 0.74, "form": "round", "mat": "timber"},
    "desk":         {"h": 0.74, "form": "table", "mat": "timber"},
    "shelving":     {"h": 1.90, "form": "box",   "mat": "timber"},
    "bathtub":      {"h": 0.56, "form": "tub",   "mat": "ceramic"},
    "wc":           {"h": 0.40, "form": "wc",    "mat": "ceramic"},
    "basin":        {"h": 0.85, "form": "basin", "mat": "ceramic"},
    "fridge":       {"h": 1.85, "form": "box",   "mat": "metal"},
    "counter":      {"h": 0.92, "form": "box",   "mat": "stone"},
    "bbq":          {"h": 0.92, "form": "box",   "mat": "metal"},
    "lounger":      {"h": 0.36, "form": "soft",  "mat": "cushion",    "back": 0.62},
    "planter":      {"h": 0.45, "form": "plant", "mat": "greenery"},
    "plant":        {"h": 0.35, "form": "plant", "mat": "greenery"},
}

# (type, x0, y0, x1, y1, facing)  -  facing is optional, default "n"
UNIT_C = [
    # master bedroom
    ("bed_double", 1643, 175, 1997, 484, "s"),
    ("wardrobe", 1390, 157, 1500, 552, "e"),
    # bathroom
    ("bathtub", 1254, 177, 1377, 477, "w"),
    ("wc", 995, 225, 1111, 307, "s"),
    ("basin", 1002, 402, 1090, 491, "s"),
    # kitchen
    ("fridge", 620, 579, 729, 709, "e"),
    # living / dining
    ("dining_table", 348, 1063, 573, 1377, "n"),
    ("chair", 313, 1078, 397, 1180, "e"),
    ("chair", 313, 1190, 397, 1292, "e"),
    ("chair", 313, 1300, 397, 1392, "e"),
    ("chair", 470, 1078, 560, 1180, "w"),
    ("chair", 470, 1190, 560, 1292, "w"),
    ("chair", 470, 1300, 560, 1392, "w"),
    ("sofa", 770, 1213, 1200, 1377, "n"),
    ("coffee_table", 941, 1063, 1077, 1179, "n"),
    ("side_table", 852, 1043, 927, 1104, "n"),
    ("console", 668, 784, 927, 828, "s"),
    # balcony
    ("bbq", 954, 804, 1309, 913, "s"),
    ("table_round", 1813, 954, 1963, 1131, "n"),
    ("chair", 1740, 960, 1815, 1050, "e"),
    ("chair", 1740, 1055, 1815, 1140, "e"),
    ("chair", 1960, 960, 2035, 1050, "w"),
    ("chair", 1960, 1055, 2035, 1140, "w"),
    ("sofa_outdoor", 1384, 1247, 1725, 1404, "n"),
    ("side_table", 1772, 1329, 1847, 1390, "n"),
    ("planter", 2236, 375, 2304, 532, "w"),
    ("planter", 2236, 777, 2304, 927, "w"),
    ("plant", 2100, 1290, 2320, 1440, "w"),
]

UNIT_A = [
    # master bedroom
    ("bed_double", 1298, 214, 1619, 534, "s"),
    # bathroom
    ("bathtub", 1766, 214, 1873, 526, "e"),
    ("wc", 2021, 255, 2136, 370, "s"),
    ("basin", 2062, 435, 2169, 542, "s"),
    # storage / office
    ("shelving", 2450, 265, 3000, 330, "s"),
    ("desk", 3007, 600, 3105, 805, "w"),
    ("office_chair", 2950, 665, 3007, 731, "w"),
    # living room
    ("sofa", 1224, 1314, 1627, 1487, "n"),
    ("coffee_table", 1298, 1175, 1487, 1298, "n"),
    ("armchair", 1618, 1150, 1750, 1281, "w"),
    ("side_table", 1471, 1150, 1569, 1240, "n"),
    # dining area
    ("console", 2037, 887, 2382, 937, "s"),
    ("dining_table", 1873, 1158, 2070, 1446, "n"),
    ("chair", 1824, 1166, 1939, 1273, "e"),
    ("chair", 1824, 1281, 1939, 1380, "e"),
    ("chair", 1824, 1388, 1939, 1487, "e"),
    ("chair", 1996, 1166, 2111, 1273, "w"),
    ("chair", 1996, 1281, 2111, 1380, "w"),
    ("chair", 1996, 1388, 2111, 1487, "w"),
    # kitchen
    ("fridge", 2300, 1659, 2432, 1840, "w"),
    # main balcony
    ("bbq", 657, 181, 1084, 296, "s"),
    ("armchair", 378, 320, 526, 435, "s"),
    ("lounger", 197, 476, 444, 830, "e"),
    ("coffee_table", 419, 542, 575, 657, "n"),
    ("armchair", 493, 764, 641, 887, "w"),
    ("side_table", 320, 854, 411, 920, "n"),
    ("dining_table", 616, 1093, 970, 1315, "n"),
    ("chair", 649, 1050, 737, 1120, "s"),
    ("chair", 760, 1050, 848, 1120, "s"),
    ("chair", 866, 1050, 954, 1120, "s"),
    ("chair", 649, 1288, 737, 1358, "n"),
    ("chair", 760, 1288, 848, 1358, "n"),
    ("chair", 866, 1288, 954, 1358, "n"),
    ("plant", 214, 99, 542, 197, "s"),
    ("plant", 526, 1339, 1068, 1520, "n"),
]

UNIT_B = [
    # bathroom
    ("bathtub", 721, 185, 984, 315, "s"),
    ("wc", 721, 327, 824, 437, "e"),
    ("basin", 730, 449, 793, 543, "e"),
    ("wardrobe", 1034, 162, 1130, 568, "e"),
    # master bedroom
    ("bed_double", 1286, 181, 1660, 549, "s"),
    # bedroom balcony
    ("side_table", 1783, 225, 1860, 300, "n"),
    ("lounger", 1873, 225, 1998, 487, "w"),
    # living / dining / kitchen
    ("sofa", 335, 799, 793, 999, "s"),
    ("coffee_round", 499, 992, 630, 1092, "n"),
    ("fridge", 322, 1330, 437, 1473, "e"),
    ("dining_table", 992, 1099, 1173, 1411, "n"),
    ("chair", 949, 1111, 1049, 1198, "e"),
    ("chair", 949, 1205, 1049, 1298, "e"),
    ("chair", 949, 1305, 1049, 1398, "e"),
    ("chair", 1105, 1111, 1198, 1198, "w"),
    ("chair", 1105, 1205, 1198, 1298, "w"),
    ("chair", 1105, 1305, 1198, 1398, "w"),
    # main balcony
    ("sofa_outdoor", 1355, 799, 1710, 968, "s"),
    ("side_table", 1758, 874, 1835, 949, "n"),
    ("lounger", 1717, 1017, 1860, 1136, "w"),
    ("bbq", 1267, 1286, 1373, 1473, "e"),
    ("plant", 1748, 624, 2060, 799, "w"),
    ("plant", 1548, 1436, 2022, 1573, "n"),
]

UNIT_D = [
    # bedroom 2 (the splayed end; the plan draws the bed on a slant, so the
    # footprint here is the upright box that covers it)
    ("bed_double", 323, 307, 661, 504, "w"),
    ("desk", 708, 181, 984, 315, "s"),
    ("office_chair", 795, 307, 881, 370, "s"),
    # bathroom
    ("wc", 1204, 213, 1338, 315, "s"),
    ("basin", 1212, 386, 1306, 488, "s"),
    ("bathtub", 1464, 189, 1605, 488, "w"),
    # master bedroom
    ("bed_double", 1818, 181, 2235, 504, "s"),
    # balcony off the kitchen
    ("side_table", 2440, 228, 2518, 307, "n"),
    ("sofa_outdoor", 2534, 189, 2880, 338, "s"),
    # kitchen
    ("counter", 2534, 504, 2880, 614, "s"),
    ("counter", 2825, 818, 2927, 1133, "w"),
    # living / dining
    ("sofa", 1739, 842, 2180, 1023, "s"),
    ("coffee_round", 1889, 1031, 2046, 1133, "n"),
    ("armchair", 2141, 1039, 2282, 1165, "w"),
    ("plant", 2204, 818, 2306, 960, "w"),
    ("dining_table", 1228, 1188, 1519, 1346, "n"),
    ("chair", 1243, 1133, 1330, 1196, "s"),
    ("chair", 1338, 1133, 1425, 1196, "s"),
    ("chair", 1425, 1133, 1511, 1196, "s"),
    ("chair", 1243, 1338, 1330, 1401, "n"),
    ("chair", 1338, 1338, 1425, 1401, "n"),
    ("chair", 1425, 1338, 1511, 1401, "n"),
    ("console", 1794, 1346, 2156, 1385, "n"),
    # main balcony
    ("table_round", 755, 928, 976, 1117, "n"),
    ("chair", 700, 940, 790, 1040, "e"),
    ("chair", 700, 1050, 790, 1140, "e"),
    ("chair", 960, 940, 1050, 1040, "w"),
    ("chair", 960, 1050, 1050, 1140, "w"),
    ("bbq", 755, 1369, 1070, 1463, "n"),
    ("plant", 519, 834, 692, 1102, "e"),
]

BY_UNIT: dict[str, list] = {
    "A": UNIT_A,
    "B": UNIT_B,
    "C": UNIT_C,
    "D": UNIT_D,
}


def items_for(key: str) -> list[dict]:
    """The listed pieces for a unit, as dicts the extractor can place."""
    out = []
    for entry in BY_UNIT.get(key, []):
        kind, x0, y0, x1, y1 = entry[:5]
        facing = entry[5] if len(entry) > 5 else "n"
        spec = TYPES[kind]
        out.append(
            {
                "kind": kind,
                "box_px": [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                "facing": facing,
                "h": spec["h"],
                "form": spec["form"],
                "mat": spec["mat"],
                "back": spec.get("back", 0.0),
            }
        )
    return out
