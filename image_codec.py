"""Pure offline image preparation for the capture-verified 552 x 768 display."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import warnings
import math
from PIL import Image, ImageOps, ImageEnhance

PALETTE = ((0,0,0),(255,255,255),(245,205,0),(200,0,0))
DITHER_ALGORITHMS = ('Atkinson','Floyd–Steinberg','关闭')


def normalize_dither(value):
    # Preserve old bool values in saved image/history metadata and API calls.
    if type(value) is bool: return 'Floyd–Steinberg' if value else '关闭'
    if value not in DITHER_ALGORITHMS: raise ValueError('Unknown dithering algorithm')
    return value


def atkinson_codes(rgb,width,height,*,palette=PALETTE):
    """Recovered integer RGB diffusion, including the app's lower-left edge rule."""
    if width<1 or height<1 or len(rgb)!=width*height*3:
        raise ValueError('Invalid RGB dimensions')
    pixels=bytearray(rgb); codes=bytearray(width*height)
    for y in range(height):
        for x in range(width):
            i=y*width+x; offset=i*3
            r,g,b=pixels[offset:offset+3]
            best=0; distance=1<<30
            for k,(pr,pg,pb) in enumerate(palette):
                d=(r-pr)**2+(g-pg)**2+(b-pb)**2
                if d<distance: best=k; distance=d
            codes[i]=best
            pr,pg,pb=palette[best]
            error=(r-pr,g-pg,b-pb)
            delta=tuple((abs(v)//8)*(1 if v>=0 else -1) for v in error)
            if delta==(0,0,0): continue
            targets=[]
            if x+1<width: targets.append(i+1)
            if x+2<width: targets.append(i+2)
            if y+1<height:
                # Original code deliberately reproduced: no x>0 guard here.
                targets.extend((i+width-1,i+width))
                if x+1<width: targets.append(i+width+1)
            if y+2<height: targets.append(i+2*width)
            for target in targets:
                for channel in range(3):
                    j=target*3+channel; value=pixels[j]+delta[channel]
                    pixels[j]=0 if value<0 else 255 if value>255 else value
    return bytes(codes)


@dataclass(frozen=True)
class ImageOptions:
    fit: str = 'contain'
    rotation: int = 0
    brightness: float = 1.0
    contrast: float = 1.0
    dither: str | bool = 'Atkinson'
    black_white: bool = False
    scale_percent: float = 100.0


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
    algorithm=normalize_dither(options.dither)
    if options.fit not in ('contain','cover') or options.rotation not in (0,90,180,270):
        raise ValueError('Invalid fit or rotation')
    if not (0.5<=options.brightness<=1.5 and 0.5<=options.contrast<=1.5):
        raise ValueError('Brightness and contrast must be within 0.5..1.5')
    if not math.isfinite(options.scale_percent) or not 10<=options.scale_percent<=300:
        raise ValueError('Scale must be within 10..300 percent')
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
    if options.scale_percent!=100:
        factor=options.scale_percent/100
        scaled=image.resize((max(1,round(552*factor)),max(1,round(768*factor))),Image.Resampling.LANCZOS)
        image=Image.new('RGB',(552,768),'white')
        image.paste(scaled,((552-scaled.width)//2,(768-scaled.height)//2))
    image=ImageEnhance.Contrast(ImageEnhance.Brightness(image).enhance(options.brightness)).enhance(options.contrast)
    palette=Image.new('P',(1,1))
    # Repeat code 0 to fill Pillow's 256-entry palette, then canonicalize duplicates.
    palette.putpalette([c for rgb in PALETTE for c in rgb]+[0]*(256*3-12))
    dither=Image.Dither.FLOYDSTEINBERG if algorithm=='Floyd–Steinberg' else Image.Dither.NONE
    if algorithm=='Atkinson':
        if options.black_white: image=image.convert('L').convert('RGB')
        codes=atkinson_codes(image.tobytes(),552,768,palette=PALETTE[:2] if options.black_white else PALETTE)
    elif options.black_white:
        monochrome=image.convert('L').convert('1',dither=dither).convert('L')
        codes=bytes(1 if v else 0 for v in monochrome.tobytes())
    else:
        quantized=image.quantize(palette=palette,dither=dither)
        codes=bytes(v if v<4 else 0 for v in quantized.tobytes())
    preview=Image.frombytes('P',(552,768),codes)
    preview.putpalette(palette.getpalette())
    return PreparedImage(source,preview.convert('RGB'),codes,pack_codes(codes),hashlib.sha256(raw).hexdigest(),options)
