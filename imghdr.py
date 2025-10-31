"""
Custom imghdr module for Python 3.13 compatibility
This replaces the removed imghdr module
"""

import struct

def what(file, h=None):
    if h is None:
        with open(file, 'rb') as f:
            h = f.read(32)
    for tf in tests:
        res = tf(h, file)
        if res:
            return res
    return None

def test_jpeg(h, f):
    """JPEG data in JFIF format"""
    if h[6:10] in (b'JFIF', b'Exif'):
        return 'jpeg'

def test_png(h, f):
    if h.startswith(b'\211PNG\r\n\032\n'):
        return 'png'

def test_gif(h, f):
    """GIF ('87 and '89 variants)"""
    if h[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'

def test_tiff(h, f):
    """TIFF (can be in Motorola or Intel byte order)"""
    if h[:2] in (b'MM', b'II'):
        return 'tiff'

def test_rgb(h, f):
    """SGI image library"""
    if h.startswith(b'\001\332'):
        return 'rgb'

def test_pbm(h, f):
    """PBM (portable bitmap)"""
    if len(h) >= 3 and h[0] == ord(b'P') and h[1] in b'14' and h[2] in b' \t\n\r':
        return 'pbm'

def test_pgm(h, f):
    """PGM (portable graymap)"""
    if len(h) >= 3 and h[0] == ord(b'P') and h[1] in b'25' and h[2] in b' \t\n\r':
        return 'pgm'

def test_ppm(h, f):
    """PPM (portable pixmap)"""
    if len(h) >= 3 and h[0] == ord(b'P') and h[1] in b'36' and h[2] in b' \t\n\r':
        return 'ppm'

def test_rast(h, f):
    """Sun raster file"""
    if h.startswith(b'\x59\xA6\x6A\x95'):
        return 'rast'

def test_xbm(h, f):
    """X bitmap (X10 or X11)"""
    if h.startswith(b'#define '):
        return 'xbm'

def test_bmp(h, f):
    if h.startswith(b'BM'):
        return 'bmp'

def test_webp(h, f):
    if h.startswith(b'RIFF') and h[8:12] == b'WEBP':
        return 'webp'

def test_exr(h, f):
    if h.startswith(b'\x76\x2f\x31\x01'):
        return 'exr'

# List of tests
tests = [
    test_jpeg,
    test_png,
    test_gif,
    test_tiff,
    test_rgb,
    test_pbm,
    test_pgm,
    test_ppm,
    test_rast,
    test_xbm,
    test_bmp,
    test_webp,
    test_exr,
]
