"""
Gera os ícones PWA para o Driver Shield 360.
Execute: python gerar_icones.py
Coloca os ícones em static/icon-192.png e static/icon-512.png
"""
import os

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except ImportError:
    PIL_OK = False

def gerar_icone_pillow(tamanho, caminho):
    img = Image.new("RGBA", (tamanho, tamanho), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Fundo arredondado
    raio = tamanho // 5
    draw.rounded_rectangle([0, 0, tamanho - 1, tamanho - 1], radius=raio,
                             fill=(10, 14, 26, 255))

    # Círculo central (vermelho/SOS)
    margem = tamanho // 6
    draw.ellipse([margem, margem, tamanho - margem, tamanho - margem],
                 fill=(220, 30, 60, 255))

    # Escudo (simplificado como triângulo + base)
    cx = tamanho // 2
    topo = tamanho // 4
    base = tamanho * 3 // 4
    larg_e = tamanho // 3
    pts = [(cx, topo), (cx - larg_e, cx - tamanho//12),
           (cx - larg_e, base - tamanho//8),
           (cx, base), (cx + larg_e, base - tamanho//8),
           (cx + larg_e, cx - tamanho//12)]
    draw.polygon(pts, fill=(255, 255, 255, 255))

    img.save(caminho, "PNG")
    print(f"✅ Gerado: {caminho}")

def gerar_icone_svg_fallback(tamanho, caminho):
    """Fallback: gera PNG simples via bytes se Pillow não disponível."""
    # PNG mínimo válido 1x1 px escalado — não ideal, mas impede erro 404
    import struct, zlib

    def png_chunk(tipo, dados):
        crc = zlib.crc32(tipo + dados) & 0xffffffff
        return struct.pack(">I", len(dados)) + tipo + dados + struct.pack(">I", crc)

    w = h = tamanho
    raw = b""
    for y in range(h):
        raw += b"\x00"
        for x in range(w):
            cx, cy = w // 2, h // 2
            dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            r = min(w, h) // 2
            if dist < r * 0.85:
                raw += bytes([220, 30, 60, 255])  # vermelho
            elif dist < r:
                raw += bytes([10, 14, 26, 255])   # borda escura
            else:
                raw += bytes([0, 0, 0, 0])        # transparente

    compressed = zlib.compress(raw)
    header = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    png = header
    png += png_chunk(b"IHDR", ihdr_data)
    png += png_chunk(b"IDAT", compressed)
    png += png_chunk(b"IEND", b"")

    with open(caminho, "wb") as f:
        f.write(png)
    print(f"✅ Gerado (fallback): {caminho}")

if __name__ == "__main__":
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    os.makedirs(static_dir, exist_ok=True)

    for tamanho in [192, 512]:
        caminho = os.path.join(static_dir, f"icon-{tamanho}.png")
        if PIL_OK:
            gerar_icone_pillow(tamanho, caminho)
        else:
            gerar_icone_svg_fallback(tamanho, caminho)

    print("\n📲 Ícones prontos em /static/")
    print("Instale Pillow para ícones de qualidade: pip install Pillow")
