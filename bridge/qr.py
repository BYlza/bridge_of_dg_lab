"""二维码渲染：终端 ASCII + 保存 SVG（不依赖 Pillow）。"""

from __future__ import annotations

import os

import qrcode
import qrcode.image.svg


def show_qr(url: str, out_dir: str | None = None, name: str = "qr.svg") -> str:
    qr = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(url)
    qr.make(fit=True)

    print()
    try:
        qr.print_ascii(invert=True)
    except Exception:  # noqa: BLE001
        pass

    path = "(未保存)"
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, name)
        try:
            img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
            img.save(path)
        except Exception:  # noqa: BLE001
            path = "(保存失败)"

    print(f"\n二维码内容：\n  {url}\n")
    if path != "(未保存)":
        print(f"终端二维码看不清时，用浏览器打开：{path}")
    return path
