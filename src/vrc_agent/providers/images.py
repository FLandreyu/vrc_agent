"""仅在内存中编码 BGRA；稳定性比较继续使用原始帧，不使用有损像素。"""

import struct
import zlib
from io import BytesIO

from PIL import Image


def validate_image_encoding(image_format: str, jpeg_quality: int) -> None:
    if image_format not in ("png", "jpeg"):
        raise ValueError("image_format must be png or jpeg")
    if type(jpeg_quality) is not int or not 1 <= jpeg_quality <= 95:
        raise ValueError("jpeg_quality must be an integer between 1 and 95")


def bgra_jpeg(data: bytes, size: tuple[int, int], *, quality: int = 85) -> bytes:
    """原尺寸 BGRA→RGB JPEG q85；忽略 alpha，不缩放、不附加外部元数据。

    固定 4:2:0、非渐进、无优化遍历；不声称与 .NET 的 q85 字节等价。
    """
    validate_image_encoding("jpeg", quality)
    width, height = size
    if (
        type(width) is not int
        or type(height) is not int
        or width <= 0
        or height <= 0
        or len(data) != width * height * 4
    ):
        raise ValueError("invalid BGRA frame dimensions")
    with Image.frombytes("RGB", size, data, "raw", "BGRX") as image, BytesIO() as output:
        image.save(
            output, format="JPEG", quality=quality, subsampling=2, optimize=False, progressive=False
        )
        return output.getvalue()


def encode_bgra(
    data: bytes, size: tuple[int, int], *, image_format: str = "png", jpeg_quality: int = 85
) -> tuple[bytes, str]:
    """编码和媒体类型同源；不自动回退为其他格式，也不重编码已编码的图片。"""
    validate_image_encoding(image_format, jpeg_quality)
    if image_format == "jpeg":
        return bgra_jpeg(data, size, quality=jpeg_quality), "image/jpeg"
    return bgra_png(data, size), "image/png"


def bgra_png(data: bytes, size: tuple[int, int]) -> bytes:
    width, height = size
    if width <= 0 or height <= 0 or len(data) != width * height * 4:
        raise ValueError("invalid BGRA frame dimensions")
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = data[2::4], data[1::4], data[0::4]
    rows = b"".join(b"\x00" + rgb[y * width * 3 : (y + 1) * width * 3] for y in range(height))

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 1))
        + chunk(b"IEND", b"")
    )
