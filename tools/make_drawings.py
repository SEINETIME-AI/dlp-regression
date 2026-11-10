"""Generates synthetic AutoCAD drawings in data/drawings/ (git-ignored) and a manifest of what each holds.

Six variants (spec 2026-11-10-electrical-drawing-classification, section 4): single-line diagrams that
keep or lose what the agent can read (text, named blocks, Unicode names), and two non-electrical controls.
Each DXF is also turned into a DWG with LibreDWG's dxf2dwg.exe and read back with dwg2dxf.exe; the
manifest records whether layer, block and insert names survived that round trip. Existing files are kept
(their bytes would change on every run); pass --force to rewrite them all.

  python tools/make_drawings.py [--force] [--seeds 3] [--libredwg C:\\libredwg\\unpacked-0.13.4]
"""
import argparse
import json
import random
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import ezdxf

OUT = Path(__file__).resolve().parent.parent / "data" / "drawings"

VARIANTS = {  # variant -> electrical
    "sld-clean": True, "sld-notext": True, "sld-exploded": True, "sld-tcvn3": True,
    "mech-control": False, "plan-control": False,
}
CLEAN_NAMES = {"mba": "MBA", "mc": "MC", "dcl": "DCL", "ct": "CT", "csv": "CSV"}
# TCVN3 bytes read as code page 1252, as a pre-Unicode drawing stores them:
# "m¸y biÕn ¸p" = máy biến áp, "dao c¸ch ly" = dao cách ly.
TCVN3_NAMES = {"mba": "m¸y biÕn ¸p", "mc": "MC_1", "dcl": "dao c¸ch ly", "ct": "TI", "csv": "CSV_1"}


def draw_symbol(target, kind, x, y):
    """One electrical symbol's lines centred on (x, y), in a layout or a block."""
    if kind == "mba":
        target.add_circle((x, y + 2), 3)
        target.add_circle((x, y - 2), 3)
    elif kind == "mc":
        target.add_lwpolyline([(x - 2, y - 2), (x + 2, y - 2), (x + 2, y + 2), (x - 2, y + 2)], close=True)
        target.add_line((x - 2, y - 2), (x + 2, y + 2))
    elif kind == "dcl":
        target.add_line((x, y - 3), (x, y - 1))
        target.add_line((x, y - 1), (x + 2, y + 2))
        target.add_line((x, y + 3), (x, y + 1.5))
    elif kind == "ct":
        target.add_circle((x, y), 1.5)
        target.add_circle((x, y), 0.8)
    else:  # csv, surge arrester
        target.add_lwpolyline([(x - 1, y - 3), (x + 1, y - 3), (x + 1, y + 1), (x - 1, y + 1)], close=True)
        target.add_line((x, y - 3), (x, y - 5))
        target.add_line((x - 1.5, y - 5), (x + 1.5, y - 5))


def title_block(msp, x, y, lines, layer):
    msp.add_lwpolyline([(x, y), (x + 70, y), (x + 70, y + 30), (x, y + 30)], close=True, dxfattribs={"layer": layer})
    for i, line in enumerate(lines):
        msp.add_text(line, height=3.5 if i == 0 else 2.5, dxfattribs={"layer": layer, "insert": (x + 3, y + 24 - 7 * i)})


def single_line_diagram(doc, rng, variant):
    msp = doc.modelspace()
    if variant == "sld-exploded":
        names, power, anno = {}, "E-POWR", "E-ANNO"
    elif variant == "sld-tcvn3":
        names, power, anno = TCVN3_NAMES, "DIEN-HA-THE", "CHU"
    else:
        names, power, anno = CLEAN_NAMES, "E-POWR", "E-ANNO"
    doc.layers.add(power)
    doc.layers.add(anno)
    for kind, name in names.items():
        draw_symbol(doc.blocks.new(name=name), kind, 0, 0)
    feeders = rng.randint(3, 8)
    msp.add_lwpolyline([(0, 0), (feeders * 20 + 10, 0)], dxfattribs={"layer": power, "const_width": 0.8})
    for i in range(feeders):
        x, top = 10 + 20 * i, 0.0
        last = "mba" if rng.random() < 0.8 else "csv"
        for kind, y in (("dcl", -8), ("mc", -18), ("ct", -26), (last, -38)):
            msp.add_line((x, top), (x, y + 3), dxfattribs={"layer": power})
            if names:
                msp.add_blockref(names[kind], (x, y), dxfattribs={"layer": power})
            else:
                draw_symbol(msp, kind, x, y)
            top = y - 3
        if variant != "sld-notext":
            msp.add_text(f"MC {471 + i}", height=1.8, dxfattribs={"layer": anno, "insert": (x + 4, -18)})
    if variant in ("sld-clean", "sld-exploded"):
        station = rng.choice(["Thanh Xuân", "Gia Lâm", "Đông Anh", "Hòa Khánh", "Long Biên"])
        title_block(msp, feeders * 20 - 50, -80, [
            "SƠ ĐỒ NGUYÊN LÝ NHẤT THỨ", f"TRẠM BIẾN ÁP 110/22kV {station}", f"Bản vẽ số: E-{rng.randint(100, 999)}",
        ], anno)


def mechanical(doc, rng):
    msp = doc.modelspace()
    for layer in ("M-PART", "M-DIM"):
        doc.layers.add(layer)
    length, radius = rng.randint(80, 160), rng.randint(8, 20)
    msp.add_lwpolyline([(0, -radius), (length, -radius), (length, radius), (0, radius)], close=True,
                       dxfattribs={"layer": "M-PART"})
    for i in range(rng.randint(2, 5)):
        msp.add_circle((15 + 20 * i, 0), rng.uniform(2, 5), dxfattribs={"layer": "M-PART"})
    hatch = msp.add_hatch(dxfattribs={"layer": "M-PART"})
    hatch.set_pattern_fill("ANSI31", scale=0.5)
    hatch.paths.add_polyline_path([(length - 20, -radius), (length, -radius), (length, radius), (length - 20, radius)])
    msp.add_linear_dim(base=(0, radius + 10), p1=(0, radius), p2=(length, radius), dxfattribs={"layer": "M-DIM"}).render()
    doc.blocks.new(name="TITLE").add_lwpolyline([(0, 0), (60, 0), (60, 20), (0, 20)], close=True)
    msp.add_blockref("TITLE", (length - 60, -radius - 40))
    part = rng.choice(["TRỤC TRUYỀN ĐỘNG", "BÁNH RĂNG CÔN", "GỐI ĐỠ", "MẶT BÍCH"])
    msp.add_text(f"BẢN VẼ CHI TIẾT - {part}", height=3, dxfattribs={"layer": "M-PART", "insert": (length - 57, -radius - 30)})
    msp.add_text(f"Vật liệu: thép C45 - Tỷ lệ 1:{rng.choice([1, 2, 5])}", height=2,
                 dxfattribs={"layer": "M-PART", "insert": (length - 57, -radius - 36)})


def floor_plan(doc, rng):
    msp = doc.modelspace()
    for layer in ("A-WALL", "A-DOOR", "A-ANNO"):
        doc.layers.add(layer)
    door = doc.blocks.new(name="A-DOOR")
    door.add_line((0, 0), (9, 0))
    door.add_arc((0, 0), 9, 0, 90)
    width, depth = rng.randint(80, 140), rng.randint(60, 100)
    msp.add_lwpolyline([(0, 0), (width, 0), (width, depth), (0, depth)], close=True,
                       dxfattribs={"layer": "A-WALL", "const_width": 2})
    for i, room in enumerate(rng.sample(["PHÒNG KHÁCH", "PHÒNG NGỦ 1", "PHÒNG NGỦ 2", "BẾP", "WC", "SÂN PHƠI"], 4)):
        x = width / 4 * i
        msp.add_line((x, 0), (x, depth), dxfattribs={"layer": "A-WALL"})
        msp.add_blockref("A-DOOR", (x + 3, 0), dxfattribs={"layer": "A-DOOR"})
        msp.add_text(room, height=2.5, dxfattribs={"layer": "A-ANNO", "insert": (x + 5, depth / 2)})
    msp.add_text("MẶT BẰNG TẦNG 1 - NHÀ Ở GIA ĐÌNH", height=4, dxfattribs={"layer": "A-ANNO", "insert": (0, -15)})


def build(variant, seed):
    rng = random.Random(f"{variant}-{seed}")
    # sld-tcvn3 is a pre-Unicode drawing: R2000, code page 1252, names in TCVN3 bytes.
    doc = ezdxf.new("R2000" if variant == "sld-tcvn3" else "R2018")
    if variant.startswith("sld-"):
        single_line_diagram(doc, rng, variant)
    elif variant == "mech-control":
        mechanical(doc, rng)
    else:
        floor_plan(doc, rng)
    return doc


def contents(path):
    """Layers, named blocks, named inserts with counts, and whether any text entity exists."""
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    inserts = Counter(e.dxf.name for e in msp.query("INSERT") if not e.dxf.name.startswith("*"))
    has_text = any(True for _ in msp.query("TEXT MTEXT ATTRIB ATTDEF")) or any(
        True for b in doc.blocks if not b.name.startswith("*") for _ in b.query("TEXT MTEXT ATTDEF"))
    return {
        "layers": [layer.dxf.name for layer in doc.layers],
        "blocks": [b.name for b in doc.blocks if not b.name.startswith("*")],
        "inserts": dict(sorted(inserts.items())),
        "has_text": has_text,
    }


def scan_names(path):
    """Layer, block and insert names by walking the group-code pairs, as the agent reads a DXF. LibreDWG's
    output has duplicate or zero handles that ezdxf refuses, so the round trip is not read with ezdxf."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    section = entity = None
    layers, blocks, inserts = [], [], Counter()
    for code, value in zip(lines[0::2], lines[1::2]):
        code, value = code.strip(), value.strip()
        if code == "0":
            entity = value
        elif code == "2" and entity == "SECTION":
            section = value
        elif code == "2" and section == "TABLES" and entity == "LAYER":
            layers.append(value)
        elif code == "2" and section == "BLOCKS" and entity == "BLOCK" and not value.startswith("*"):
            blocks.append(value)
        elif code == "2" and section == "ENTITIES" and entity == "INSERT" and not value.startswith("*"):
            inserts[value] += 1
    return {"layers": layers, "blocks": blocks, "inserts": dict(sorted(inserts.items()))}


def round_trip(dxf, libredwg, original, force):
    """DXF -> DWG -> DXF with LibreDWG; which names survived, or why there is no DWG. An existing DWG is
    kept unless `force`, like the DXF."""
    dwg = dxf.with_suffix(".dwg")
    row = {"dwg": None, "dwg_error": None, "layers_kept": None, "blocks_kept": None, "inserts_kept": None}
    try:
        if force or not dwg.exists():
            done = subprocess.run([str(libredwg / "dxf2dwg.exe"), "-y", "-o", str(dwg), str(dxf)],
                                  capture_output=True, timeout=120)
            if done.returncode != 0 or not dwg.exists() or dwg.stat().st_size == 0:
                row["dwg_error"] = f"dxf2dwg exit {done.returncode}"
                dwg.unlink(missing_ok=True)
                return row
        with tempfile.TemporaryDirectory() as tmp:
            back = Path(tmp) / "back.dxf"
            done = subprocess.run([str(libredwg / "dwg2dxf.exe"), "-y", "-o", str(back), str(dwg)],
                                  capture_output=True, timeout=120)
            if done.returncode != 0 or not back.exists():
                row["dwg"], row["dwg_error"] = dwg.name, f"dwg2dxf exit {done.returncode}"
                return row
            again = scan_names(back)
    except (OSError, subprocess.TimeoutExpired) as error:
        row["dwg_error"] = f"{type(error).__name__}: {error}"
        return row
    row["dwg"] = dwg.name
    row["layers_kept"] = set(original["layers"]) <= set(again["layers"])
    row["blocks_kept"] = set(original["blocks"]) <= set(again["blocks"])
    row["inserts_kept"] = original["inserts"] == again["inserts"]
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="rewrite existing files")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--libredwg", type=Path, default=Path(r"C:\libredwg\unpacked-0.13.4"))
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / ".gitignore").write_text("*\n", encoding="utf-8")  # generated data stays out of git
    convert = (args.libredwg / "dxf2dwg.exe").exists() and (args.libredwg / "dwg2dxf.exe").exists()
    manifest = []
    for variant, electrical in VARIANTS.items():
        for seed in range(args.seeds):
            path = OUT / f"{variant}-{seed:02d}.dxf"
            if args.force or not path.exists():
                build(variant, seed).saveas(path)
            row = {"file": path.name, "variant": variant, "electrical": electrical, "seed": seed, **contents(path)}
            if convert:
                row.update(round_trip(path, args.libredwg, row, args.force))
            else:
                row.update({"dwg": None, "dwg_error": f"no LibreDWG in {args.libredwg}"})
            manifest.append(row)
            print(f"{path.name}: dwg={row['dwg']} {row.get('dwg_error') or ''}")
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
