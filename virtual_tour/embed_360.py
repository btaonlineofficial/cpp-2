import base64, io
import os

# ── Paths (all files now live inside virtual_tour/) ──
BASE = os.path.dirname(os.path.abspath(__file__))
INPUT_IMG  = os.path.join(BASE, 'Chad__1.jpg.jpeg')
OUTPUT_B64 = os.path.join(BASE, '360_resized_b64.txt')

from PIL import Image

img = Image.open(INPUT_IMG).convert('RGB')
img = img.resize((4096, 2048), Image.LANCZOS)
buf = io.BytesIO()
img.save(buf, format='JPEG', quality=82)
b64 = base64.b64encode(buf.getvalue()).decode('ascii')

with open(OUTPUT_B64, 'w') as f:
    f.write(b64)

print(f'Written! Size: {round(len(b64)/1024/1024,2)} MB')
