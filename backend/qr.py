"""QR codes for an address (the phone remote's, on the settings page), as SVG: byte mode, error correction level M,
versions 1 to 10 (up to 213 bytes). Python's standard library only, as the rest of the backend (ISO/IEC 18004)."""

# version: (error correction codewords per block, [(blocks, data codewords per block), …]) at level M
BLOCKS = {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
          6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
          10: (26, [(4, 43), (1, 44)])}
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
         9: [6, 26, 46], 10: [6, 28, 50]}
LEVEL_M = 0  # the format bits' two for level M

# GF(256) with the polynomial x^8 + x^4 + x^3 + x^2 + 1, for the Reed-Solomon codes
EXP, LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    EXP[_i], LOG[_x] = _x, _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def _mul(a, b):
    return 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]


def _rs(data, n):
    """The n error correction codewords of data."""
    gen = [1]
    for i in range(n):  # (x - a^0)(x - a^1)…(x - a^(n-1)), highest power first
        gen = [a ^ _mul(b, EXP[i]) for a, b in zip(gen + [0], [0] + gen)]
    rem = [0] * n
    for byte in data:
        f = byte ^ rem[0]
        rem = rem[1:] + [0]
        for j in range(n):
            rem[j] ^= _mul(gen[j + 1], f)
    return rem


def _codewords(text, version):
    ec, groups = BLOCKS[version]
    capacity = sum(c * d for c, d in groups)
    data = text.encode()
    bits = "0100" + format(len(data), "016b" if version >= 10 else "08b") + "".join(format(b, "08b") for b in data)
    bits += "0" * min(4, capacity * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    out = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pad = 0
    while len(out) < capacity:
        out.append((0xEC, 0x11)[pad % 2])
        pad += 1
    blocks, at = [], 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(out[at:at + size])
            at += size
    eccs = [_rs(b, ec) for b in blocks]
    mixed = [b[i] for i in range(max(map(len, blocks))) for b in blocks if i < len(b)]
    return mixed + [e[i] for i in range(ec) for e in eccs]


def _version_for(text):
    n = len(text.encode())
    for v in range(1, 11):
        capacity = sum(c * d for c, d in BLOCKS[v][1])
        if 4 + (16 if v >= 10 else 8) + 8 * n <= capacity * 8:
            return v
    raise ValueError("too long for a QR code of version 10")


def _penalty(m):
    size, score = len(m), 0
    lines = m + [list(col) for col in zip(*m)]
    for line in lines:  # runs of five or more alike
        run = 1
        for a, b in zip(line, line[1:]):
            if a == b:
                run += 1
            else:
                score += run - 2 if run >= 5 else 0
                run = 1
        score += run - 2 if run >= 5 else 0
        s = "".join("1" if x else "0" for x in line)  # what looks like a finder
        for pat in ("10111010000", "00001011101"):
            i = s.find(pat)
            while i != -1:
                score += 40
                i = s.find(pat, i + 1)
    for y in range(size - 1):  # 2×2 alike
        for x in range(size - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark, total = sum(map(sum, m)), size * size
    return score + ((abs(dark * 20 - total * 10) + total - 1) // total - 1) * 10


MASKS = [lambda y, x: (x + y) % 2 == 0, lambda y, x: y % 2 == 0, lambda y, x: x % 3 == 0,
         lambda y, x: (x + y) % 3 == 0, lambda y, x: (y // 2 + x // 3) % 2 == 0,
         lambda y, x: x * y % 2 + x * y % 3 == 0, lambda y, x: (x * y % 2 + x * y % 3) % 2 == 0,
         lambda y, x: ((x + y) % 2 + x * y % 3) % 2 == 0]


def matrix(text):
    """The QR code of text: rows of True (dark) and False."""
    version = _version_for(text)
    size = 17 + 4 * version
    m = [[False] * size for _ in range(size)]
    fixed = [[False] * size for _ in range(size)]

    def put(x, y, dark):
        m[y][x], fixed[y][x] = dark, True

    for i in range(size):  # timing patterns
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):  # finders with their light borders
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    d = max(abs(dx), abs(dy))
                    put(x, y, d not in (2, 4))
    pos = ALIGN[version]
    for cy in pos:
        for cx in pos:
            if (cx, cy) in ((6, 6), (6, pos[-1]), (pos[-1], 6)):
                continue  # where a finder is
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    put(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)

    def format_bits(mask):
        data = LEVEL_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 == 1
        for i in range(6):
            put(8, i, bit(i))
        put(8, 7, bit(6))
        put(8, 8, bit(7))
        put(7, 8, bit(8))
        for i in range(9, 15):
            put(14 - i, 8, bit(i))
        for i in range(8):
            put(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            put(8, size - 15 + i, bit(i))
        put(8, size - 8, True)  # the dark module

    format_bits(0)  # its places taken before the data is put in
    if version >= 7:
        rem = version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = version << 12 | rem
        for i in range(18):
            dark = (bits >> i) & 1 == 1
            a, b = size - 11 + i % 3, i // 3
            put(a, b, dark)
            put(b, a, dark)

    data = _codewords(text, version)
    i, right = 0, size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                y = size - 1 - vert if ((right + 1) & 2) == 0 else vert
                if not fixed[y][x] and i < len(data) * 8:
                    m[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
        right -= 2

    best = None
    for mask in range(8):
        trial = [[m[y][x] ^ (not fixed[y][x] and MASKS[mask](y, x)) for x in range(size)] for y in range(size)]
        saved = [row[:] for row in m]
        m[:] = trial
        format_bits(mask)
        score = _penalty(m)
        if best is None or score < best[0]:
            best = (score, [row[:] for row in m])
        m[:] = saved
    return best[1]


def svg(text, dark="#000", light="#fff"):
    """The QR code of text as an SVG of its own, with the quiet zone of four modules around it."""
    rows = matrix(text)
    n = len(rows) + 8
    path = "".join(f"M{x + 4} {y + 4}h1v1h-1z" for y, row in enumerate(rows) for x, d in enumerate(row) if d)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="{light}"/><path d="{path}" fill="{dark}"/></svg>')
