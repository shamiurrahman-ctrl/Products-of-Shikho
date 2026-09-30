#!/usr/bin/env python3
"""Make web-weight copies of every raster image index.html uses, and point the deck at them.

The source PNGs (Figma exports, up to 13 MB each) stay untouched in assets/. For each
PNG/JPG referenced from index.html this writes assets/web/<same path>.webp and rewrites the
reference. Photos and UI screens become lossy WebP at high quality (transparency kept
exactly); scribble pen-order maps are stored lossless because the deck reads their pixels.

Re-run after adding or replacing images (and after tools/build-scribble-maps.py, which
writes PNG map paths back into index.html). Sources that haven't changed are skipped.
"""
import os, re
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index.html")
MAX_SIDE = 2560      # the stage is 1920px wide; this leaves headroom for high-DPI screens
QUALITY = 88

REF = re.compile(r'assets/(?!web/)[A-Za-z0-9_\-./ ]+?\.(?:png|jpg|jpeg)(?:\?v=\d+)?')


def convert(src_rel):
    src = os.path.join(ROOT, src_rel)
    out_rel = "assets/web/" + os.path.splitext(src_rel[len("assets/"):])[0] + ".webp"
    out = os.path.join(ROOT, out_rel)
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(src):
        return out_rel, None
    os.makedirs(os.path.dirname(out), exist_ok=True)
    im = Image.open(src)
    im.load()
    if src_rel.startswith("assets/scribble-maps/"):
        im.convert("RGBA").save(out, "WEBP", lossless=True, exact=True, method=6)
    else:
        if max(im.size) > MAX_SIDE:
            s = MAX_SIDE / max(im.size)
            im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
        has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
        im = im.convert("RGBA" if has_alpha else "RGB")
        if has_alpha and im.getchannel("A").getextrema()[0] == 255:
            im = im.convert("RGB")
        im.save(out, "WEBP", quality=QUALITY, method=6)
    return out_rel, (os.path.getsize(src), os.path.getsize(out))


def main():
    html = open(INDEX).read()
    refs = sorted(set(REF.findall(html)), key=len, reverse=True)   # longest first so ?v= variants win
    before = after = 0
    for ref in refs:
        src_rel = ref.split("?")[0]
        if not os.path.exists(os.path.join(ROOT, src_rel)):
            print("missing:", src_rel)
            continue
        out_rel, sizes = convert(src_rel)
        html = html.replace(ref, out_rel)
        if sizes:
            before += sizes[0]; after += sizes[1]
            print(f"{src_rel}: {sizes[0]/1e6:.2f} MB -> {sizes[1]/1e6:.2f} MB")
    open(INDEX, "w").write(html)
    if before:
        print(f"converted {before/1e6:.1f} MB -> {after/1e6:.1f} MB")


if __name__ == "__main__":
    main()
