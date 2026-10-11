"""Pure offline image preparation for the capture-verified 552 x 768 display."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import warnings
from PIL import Image, ImageOps, ImageEnhance

PALETTE = ((0,0,0),(255,255,255),(245,205,0),(200,0,0))


@dataclass(frozen=True)
class ImageOptions:
    fit: str = 'contain'
    rotation: int = 0
    brightness: float = 1.0
    contrast: float = 1.0
    dither: bool = False


@dataclass(frozen=True)
class PreparedImage:
    source_rgb: Image.Image
    preview_rgb: Image.Image
    codes: bytes
    packed: bytes
    source_sha256: str
    options: ImageOptions


def pack_codes(codes: bytes, width=552, height=768) -> bytes:
    if width<=0 or height<=0 or height%4 or len(codes)!=width*height or any(v>3 for v in codes):
        raise ValueError('Invalid 2-bit image dimensions or color codes')
    result=bytearray()
    for x in range(width-1,-1,-1):
        for y in range(height-1,-1,-4):
            result.append(sum(codes[(y-i)*width+x] << (6-2*i) for i in range(4)))
    return bytes(result)


def prepare_image(path: Path, options: ImageOptions) -> PreparedImage:
    if options.fit not in ('contain','cover') or options.rotation not in (0,90,180,270):
        raise ValueError('Invalid fit or rotation')
    if not (0.5<=options.brightness<=1.5 and 0.5<=options.contrast<=1.5):
        raise ValueError('Brightness and contrast must be within 0.5..1.5')
    raw=Path(path).read_bytes()
    # Decode the exact bytes whose hash is recorded, even if source changes on disk.
    from io import BytesIO
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(BytesIO(raw)) as image:
            if image.format not in ('PNG','JPEG','BMP'):
                raise ValueError('Only PNG, JPEG and BMP are supported')
            image=ImageOps.exif_transpose(image).convert('RGBA')
            white=Image.new('RGBA',image.size,'white')
            source=Image.alpha_composite(white,image).convert('RGB')
    image=source.rotate(options.rotation,expand=True)
    if options.fit=='cover':
        image=ImageOps.fit(image,(552,768),method=Image.Resampling.LANCZOS)
    else:
        small=ImageOps.contain(image,(552,768),method=Image.Resampling.LANCZOS)
        image=Image.new('RGB',(552,768),'white')
        image.paste(small,((552-small.width)//2,(768-small.height)//2))
    image=ImageEnhance.Contrast(ImageEnhance.Brightness(image).enhance(options.brightness)).enhance(options.contrast)
    palette=Image.new('P',(1,1))
    # Repeat code 0 to fill Pillow's 256-entry palette, then canonicalize duplicates.
    palette.putpalette([c for rgb in PALETTE for c in rgb]+[0]*(256*3-12))
    quantized=image.quantize(palette=palette,dither=Image.Dither.FLOYDSTEINBERG if options.dither else Image.Dither.NONE)
    codes=bytes(v if v<4 else 0 for v in quantized.tobytes())
    preview=Image.frombytes('P',(552,768),codes)
    preview.putpalette(palette.getpalette())
    return PreparedImage(source,preview.convert('RGB'),codes,pack_codes(codes),hashlib.sha256(raw).hexdigest(),options)
