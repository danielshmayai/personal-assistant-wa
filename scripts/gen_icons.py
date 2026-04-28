"""Generate PWA icons for the Personal Assistant app."""
import math
from PIL import Image, ImageDraw, ImageFilter

BG_DARK   = (13,  24,  31)   # #0d181f
BG_CARD   = (19,  24,  31)   # #13181f
BLUE      = (59,  158, 255)  # #3b9eff accent
BLUE_DIM  = (59,  158, 255, 40)
WHITE     = (221, 229, 240)  # --text
WHITE2    = (122, 136, 153)  # --text2


def draw_icon(size: int) -> Image.Image:
    scale = 4  # supersampling
    S = size * scale
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ── Background circle ─────────────────────────────────────────────────────
    pad = int(S * 0.04)
    d.ellipse([pad, pad, S - pad, S - pad], fill=BG_CARD)

    # Subtle radial glow behind the figure
    glow_r = int(S * 0.30)
    cx = S // 2
    cy = int(S * 0.52)
    for i in range(glow_r, 0, -1):
        alpha = int(18 * (i / glow_r) ** 2)
        d.ellipse(
            [cx - i, cy - i, cx + i, cy + i],
            fill=(*BLUE, alpha),
        )

    # ── Person silhouette ─────────────────────────────────────────────────────
    # Head
    head_r = int(S * 0.155)
    head_cx = cx
    head_cy = int(S * 0.38)
    d.ellipse(
        [head_cx - head_r, head_cy - head_r, head_cx + head_r, head_cy + head_r],
        fill=WHITE,
    )

    # Body / shoulders (arc-shaped torso clipped to bottom half)
    body_w = int(S * 0.46)
    body_h = int(S * 0.26)
    body_top = int(S * 0.555)
    body_left = cx - body_w // 2
    # Draw a filled arc (pie slice pointing upward) for the shoulder silhouette
    body_img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bd = ImageDraw.Draw(body_img)
    bd.ellipse(
        [body_left, body_top, body_left + body_w, body_top + body_h * 2],
        fill=WHITE,
    )
    # Clip so only the top-half of the ellipse shows (shoulders)
    clip = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cd = ImageDraw.Draw(clip)
    cd.rectangle([0, body_top, S, body_top + body_h], fill=(255, 255, 255, 255))
    body_img.putalpha(
        Image.fromarray(
            __import__("numpy", fromlist=["array"]).array(clip)[:, :, 3]
        )
    )
    img = Image.alpha_composite(img, body_img)
    d = ImageDraw.Draw(img)

    # ── Sparkle / AI indicator (top-right of head) ────────────────────────────
    sp_cx = head_cx + int(head_r * 0.85)
    sp_cy = head_cy - int(head_r * 0.85)
    sp_r  = int(S * 0.075)

    # Outer glow ring
    for i in range(sp_r + int(S*0.015), sp_r, -1):
        alpha = int(80 * ((i - sp_r) / (S * 0.015)))
        d.ellipse(
            [sp_cx - i, sp_cy - i, sp_cx + i, sp_cy + i],
            fill=(*BLUE, alpha),
        )

    # Sparkle circle
    d.ellipse(
        [sp_cx - sp_r, sp_cy - sp_r, sp_cx + sp_r, sp_cy + sp_r],
        fill=BLUE,
    )

    # 4-point star inside sparkle
    star_arms = [
        (0, -1), (0.22, -0.22), (1, 0), (0.22, 0.22),
        (0, 1), (-0.22, 0.22), (-1, 0), (-0.22, -0.22),
    ]
    inner = sp_r * 0.38
    outer = sp_r * 0.72
    pts = []
    for i, (sx, sy) in enumerate(star_arms):
        r = outer if i % 2 == 0 else inner
        pts.append((sp_cx + sx * r, sp_cy + sy * r))
    d.polygon(pts, fill=(255, 255, 255, 255))

    # ── Bottom circle trim (decorative) ──────────────────────────────────────
    trim_y = int(S * 0.86)
    trim_h = int(S * 0.025)
    trim_w = int(S * 0.28)
    d.rounded_rectangle(
        [cx - trim_w // 2, trim_y, cx + trim_w // 2, trim_y + trim_h],
        radius=trim_h // 2,
        fill=(*BLUE, 80),
    )

    # Downscale with anti-aliasing
    final = img.resize((size, size), Image.LANCZOS)
    # Composite onto opaque dark background so PNG has no transparency
    bg = Image.new("RGBA", (size, size), (*BG_DARK, 255))
    bg.paste(final, mask=final.split()[3])
    return bg.convert("RGB")


def main():
    import os, sys
    out_dir = os.path.join(os.path.dirname(__file__), "..", "backend", "app", "static")
    os.makedirs(out_dir, exist_ok=True)

    try:
        import numpy  # noqa: used for alpha clip above
    except ImportError:
        # Fallback: skip body clipping if numpy not available
        pass

    for size in (192, 512, 180):
        path = os.path.join(out_dir, f"icon-{size}.png")
        draw_icon(size).save(path, "PNG", optimize=True)
        print(f"  wrote {path}")

    print("Done.")


if __name__ == "__main__":
    main()
