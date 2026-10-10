"""Selected A vector mark and in-memory browser/PWA assets; no image files written."""
from functools import lru_cache
from io import BytesIO

from fastapi import HTTPException
from fastapi.responses import Response
from PIL import Image, ImageDraw

VERSION = 'a2'
ICON_URL = '/icons/icon-192.png?v=' + VERSION
MARK = '''<g fill="white" opacity=".96"><rect x="126" y="130" width="74" height="74" rx="14"/><rect x="219" y="130" width="74" height="74" rx="14"/><rect x="312" y="130" width="74" height="74" rx="14"/><rect x="126" y="223" width="74" height="74" rx="14"/><rect x="219" y="223" width="74" height="74" rx="14"/><rect x="312" y="223" width="74" height="74" rx="14"/></g><path d="M98 330h316" fill="none" stroke="white" stroke-width="24" stroke-linecap="round" opacity=".82"/><path d="M116 374h90l25-35 35 62 29-45 21 18h80" fill="none" stroke="white" stroke-width="19" stroke-linecap="round" stroke-linejoin="round"/>'''
SVG = '''<svg viewBox="0 0 512 512" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Docker Monitor"><defs><linearGradient id="brand-bg" x1="0" y1="0" x2=".85" y2="1"><stop stop-color="#53a4ff"/><stop offset=".5" stop-color="#147be9"/><stop offset="1" stop-color="#0753be"/></linearGradient><linearGradient id="brand-shine" x2="0" y2="1"><stop stop-color="white" stop-opacity=".22"/><stop offset="1" stop-color="white" stop-opacity="0"/></linearGradient></defs><rect x="6" y="6" width="500" height="500" rx="114" fill="url(#brand-bg)"/><rect x="7" y="7" width="498" height="498" rx="113" fill="none" stroke="white" stroke-opacity=".25" stroke-width="3"/><path d="M120 8h272c62 0 112 49 112 112v110C362 169 152 150 8 239V120C8 58 58 8 120 8Z" fill="url(#brand-shine)"/>''' + MARK + '</svg>'


def raster(size, maskable=False):
    # Draw at twice the target resolution, then antialias the small-size mark.
    n = size * 2
    factor = n / 512
    image = Image.new('RGBA', (n, n))
    stops = [(83, 164, 255), (20, 123, 233), (7, 83, 190)]
    pixels = []
    for y in range(n):
        for x in range(n):
            t = max(0, min(1, (.85 * x / n + y / n) / 1.7225))
            lo, hi = (stops[0], stops[1]) if t < .5 else (stops[1], stops[2])
            blend = t * 2 if t < .5 else (t - .5) * 2
            color = tuple(round(a + (b - a) * blend) for a, b in zip(lo, hi))
            # A quiet highlight on the upper part of the rounded tile.
            shine = .16 * max(0, 1 - y / (n * .48))
            pixels.append(tuple(round(c + (255 - c) * shine) for c in color) + (255,))
    image.putdata(pixels)
    if not maskable:
        mask = Image.new('L', (n, n))
        ImageDraw.Draw(mask).rounded_rectangle(tuple(v * factor for v in (6, 6, 506, 506)), radius=114 * factor, fill=255)
        image.putalpha(mask)
    draw = ImageDraw.Draw(image)
    def line(points, width, opacity=1):
        coords = [(x * factor, y * factor) for x, y in points]
        color = (255, 255, 255, round(255 * opacity))
        draw.line(coords, fill=color, width=max(1, round(width * factor)), joint='curve')
        r = width * factor / 2
        for x, y in coords:
            draw.ellipse((x-r, y-r, x+r, y+r), fill=color)
    for y in (130, 223):
        for x in (126, 219, 312):
            draw.rounded_rectangle(tuple(v * factor for v in (x, y, x+74, y+74)), radius=14*factor, fill=(255,255,255,245))
    line([(98,330),(414,330)], 24, .82)
    line([(116,374),(206,374),(231,339),(266,401),(295,356),(316,374),(396,374)], 19)
    return image.resize((size, size), Image.Resampling.LANCZOS)


@lru_cache(maxsize=10)
def asset(name):
    if name == 'icon.svg':
        return SVG.encode(), 'image/svg+xml'
    buffer = BytesIO()
    if name == 'favicon.ico':
        raster(64).save(buffer, format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64)])
        return buffer.getvalue(), 'image/x-icon'
    sizes = {f'icon-{size}.png': size for size in (16,32,180,192,512)}
    sizes['icon-maskable-512.png'] = 512
    if name not in sizes:
        raise HTTPException(404, '图标不存在')
    raster(sizes[name], maskable=name == 'icon-maskable-512.png').save(buffer, format='PNG')
    return buffer.getvalue(), 'image/png'


def install_routes(app):
    def icon_response(name):
        content, media_type = asset(name)
        return Response(content, media_type=media_type, headers={'Cache-Control':'public, max-age=3600'})
    @app.api_route('/favicon.ico', methods=['GET', 'HEAD'], include_in_schema=False)
    def favicon():
        return icon_response('favicon.ico')
    @app.api_route('/icons/{name}', methods=['GET', 'HEAD'], include_in_schema=False)
    def icon(name: str):
        return icon_response(name)
