#!/usr/bin/env python3
"""Build "pen order" maps for every scribble SVG used by index.html.

Each scribble is a hand-drawn brush stroke exported as ~150 filled texture
fragments, so it has no single path to animate with stroke-dashoffset. Instead
we rasterise it, walk through its ink from one end of the stroke to the other
(geodesic distance, so the walk follows the stroke around its curves), and
store "when does the pen reach this pixel" in the alpha channel of a PNG:
alpha 255 = start of the stroke, alpha 1 = end. RGB flags real ink (white) vs
empty space (black); the deck uses it to spend the draw time only on the part
of a stroke that is inside the slide. Separate strokes are walked one
after another with a short gap. The deck masks the untouched original SVG
with this map and sweeps a threshold across it (see drawScribble in index.html).

Writes assets/scribble-maps/<name>.png and the SCRIBBLE_MAPS block in index.html.
Needs Google Chrome (to rasterise the SVGs) and Pillow.
"""
import heapq, json, os, re, subprocess, tempfile
from collections import deque
from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index.html")
OUT_DIR = os.path.join(ROOT, "assets", "scribble-maps")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
LONG_SIDE = 800          # raster size of the longest side
CLOSE_R = 3              # bridges gaps in the brush texture so a stroke stays one piece
MIN_STROKE = 0.03        # components smaller than this share of the ink are texture specks
GAP = 0.06               # pause between separate strokes, as a share of the total length


def scribble_svgs(html):
    found = re.findall(r'assets/[A-Za-z0-9_\-./]*(?:decor|scribble|decorative)[A-Za-z0-9_\-./]*\.svg', html)
    return sorted(set(found))


def raster_alpha(svg_path, w, h):
    with tempfile.TemporaryDirectory() as tmp:
        page = os.path.join(tmp, "r.html")
        png = os.path.join(tmp, "r.png")
        with open(page, "w") as f:
            f.write(f'<html><body style="margin:0;background:transparent">'
                    f'<img src="file://{svg_path}" style="display:block;width:{w}px;height:{h}px"></body></html>')
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--allow-file-access-from-files", "--default-background-color=00000000",
                        f"--window-size={w},{h}", f"--screenshot={png}", f"file://{page}"],
                       check=True, capture_output=True)
        return Image.open(png).convert("RGBA").getchannel("A").crop((0, 0, w, h))


N8 = [(-1, -1, 1.4142), (0, -1, 1), (1, -1, 1.4142), (-1, 0, 1), (1, 0, 1), (-1, 1, 1.4142), (0, 1, 1), (1, 1, 1.4142)]


def dijkstra(src, pixels, w):
    dist = {src: 0.0}
    heap = [(0.0, src)]
    while heap:
        d, p = heapq.heappop(heap)
        if d > dist[p]:
            continue
        x, y = p % w, p // w
        for dx, dy, c in N8:
            q = (y + dy) * w + (x + dx)
            if q in pixels:
                nd = d + c
                if nd < dist.get(q, 1e18):
                    dist[q] = nd
                    heapq.heappush(heap, (nd, q))
    return dist


def components(mask, w, h):
    seen, comps = set(), []
    for p in mask:
        if p in seen:
            continue
        comp, dq = {p}, deque([p])
        seen.add(p)
        while dq:
            c = dq.popleft()
            x, y = c % w, c // w
            for dx, dy, _ in N8:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    q = ny * w + nx
                    if q in mask and q not in seen:
                        seen.add(q); comp.add(q); dq.append(q)
        comps.append(comp)
    return comps


def build(rel):
    svg_path = os.path.join(ROOT, rel)
    svg = open(svg_path).read()
    vb = [float(v) for v in re.search(r'viewBox="([^"]+)"', svg).group(1).replace(",", " ").split()]
    vbx, vby, vbw, vbh = vb
    s = LONG_SIDE / max(vbw, vbh)
    w, h = max(1, round(vbw * s)), max(1, round(vbh * s))

    alpha = raster_alpha(svg_path, w, h)
    k = CLOSE_R * 2 + 1
    closed = alpha.point(lambda a: 255 if a > 60 else 0).filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k))
    data = closed.getdata()
    ink = {i for i, v in enumerate(data) if v}
    xs = [p % w for p in ink]; ys = [p // w for p in ink]
    horizontal = (max(xs) - min(xs)) >= (max(ys) - min(ys))
    primary = (lambda p: p % w) if horizontal else (lambda p: p // w)

    strokes = []
    for comp in components(ink, w, h):
        if len(comp) < MIN_STROKE * len(ink):
            continue
        d0 = dijkstra(next(iter(comp)), comp, w)
        e1 = max(d0, key=d0.get)
        d1 = dijkstra(e1, comp, w)
        e2 = max(d1, key=d1.get)
        start = e1 if primary(e1) <= primary(e2) else e2
        dist = d1 if start == e1 else dijkstra(e2, comp, w)
        end = max(dist, key=dist.get)
        strokes.append((primary(start), start, end, dist, dist[end]))
    strokes.sort(key=lambda t: t[0])

    total_len = sum(t[4] for t in strokes)
    gap = GAP * total_len if len(strokes) > 1 else 0
    T = total_len + gap * (len(strokes) - 1)
    time = [None] * (w * h)
    offset = 0.0
    for _, _, _, dist, length in strokes:
        for p, d in dist.items():
            time[p] = (offset + d) / T
        offset += length + gap

    # every other pixel (texture specks, anti-aliased edges) takes the time of the nearest timed pixel
    dq = deque(i for i, t in enumerate(time) if t is not None)
    while dq:
        p = dq.popleft()
        x, y = p % w, p // w
        for dx, dy, _ in N8:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                q = ny * w + nx
                if time[q] is None:
                    time[q] = time[p]; dq.append(q)

    raw_ink = alpha.getdata()
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.putdata([((255, 255, 255) if raw_ink[i] > 60 else (0, 0, 0)) + (255 - round(254 * t),)
                 for i, t in enumerate(time)])
    name = os.path.splitext(rel[len("assets/"):])[0].replace("/", "-")
    map_rel = f"assets/scribble-maps/{name}.png"
    out.save(os.path.join(ROOT, map_rel), optimize=True)

    def vb_pt(p):
        return [round(vbx + (p % w + 0.5) / s, 2), round(vby + (p // w + 0.5) / s, 2)]
    return {
        "map": map_rel,
        "vb": [vbx, vby, vbw, vbh],
        "a": vb_pt(strokes[0][1]),      # where the pen starts
        "b": vb_pt(strokes[-1][2]),     # where the pen lifts for the last time
        "len": round(T / s, 1),         # drawn length in viewBox units
        "strokes": len(strokes),
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    html = open(INDEX).read()
    maps = {}
    for rel in scribble_svgs(html):
        maps[rel] = build(rel)
        print(f'{rel}: {maps[rel]["strokes"]} stroke(s), length {maps[rel]["len"]}')
    block = "const SCRIBBLE_MAPS = " + json.dumps(maps, indent=1) + ";"
    html = re.sub(r"/\*SCRIBBLE-MAPS\*/.*?/\*END-SCRIBBLE-MAPS\*/",
                  lambda m: "/*SCRIBBLE-MAPS*/\n" + block + "\n/*END-SCRIBBLE-MAPS*/", html, flags=re.S)
    open(INDEX, "w").write(html)


if __name__ == "__main__":
    main()
