"""QR code encoder for short text such as the Web Admin pairing link.

Follows ISO/IEC 18004: byte mode (UTF-8, no ECI header), versions 1 to 10,
error correction levels L, M, Q and H. Standard library only. The result is a
plain matrix of booleans; drawing it (and the quiet zone) is up to the caller.
"""

from __future__ import annotations

import re

__all__ = ["MAX_VERSION", "encode", "version_for"]

MAX_VERSION = 10

Matrix = list[list[bool]]

_MODE_BYTE = 0b0100

# Error correction level -> the two level bits of the format information.
_ECC_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}

# Error correction blocks (ISO/IEC 18004 table 9).
# (version, level): (EC codewords per block, ((block count, data codewords per block), ...))
_EC_BLOCKS: dict[tuple[int, str], tuple[int, tuple[tuple[int, int], ...]]] = {
    (1, "L"): (7, ((1, 19),)),
    (1, "M"): (10, ((1, 16),)),
    (1, "Q"): (13, ((1, 13),)),
    (1, "H"): (17, ((1, 9),)),
    (2, "L"): (10, ((1, 34),)),
    (2, "M"): (16, ((1, 28),)),
    (2, "Q"): (22, ((1, 22),)),
    (2, "H"): (28, ((1, 16),)),
    (3, "L"): (15, ((1, 55),)),
    (3, "M"): (26, ((1, 44),)),
    (3, "Q"): (18, ((2, 17),)),
    (3, "H"): (22, ((2, 13),)),
    (4, "L"): (20, ((1, 80),)),
    (4, "M"): (18, ((2, 32),)),
    (4, "Q"): (26, ((2, 24),)),
    (4, "H"): (16, ((4, 9),)),
    (5, "L"): (26, ((1, 108),)),
    (5, "M"): (24, ((2, 43),)),
    (5, "Q"): (18, ((2, 15), (2, 16))),
    (5, "H"): (22, ((2, 11), (2, 12))),
    (6, "L"): (18, ((2, 68),)),
    (6, "M"): (16, ((4, 27),)),
    (6, "Q"): (24, ((4, 19),)),
    (6, "H"): (28, ((4, 15),)),
    (7, "L"): (20, ((2, 78),)),
    (7, "M"): (18, ((4, 31),)),
    (7, "Q"): (18, ((2, 14), (4, 15))),
    (7, "H"): (26, ((4, 13), (1, 14))),
    (8, "L"): (24, ((2, 97),)),
    (8, "M"): (22, ((2, 38), (2, 39))),
    (8, "Q"): (22, ((4, 18), (2, 19))),
    (8, "H"): (26, ((4, 14), (2, 15))),
    (9, "L"): (30, ((2, 116),)),
    (9, "M"): (22, ((3, 36), (2, 37))),
    (9, "Q"): (20, ((4, 16), (4, 17))),
    (9, "H"): (24, ((4, 12), (4, 13))),
    (10, "L"): (18, ((2, 68), (2, 69))),
    (10, "M"): (26, ((4, 43), (1, 44))),
    (10, "Q"): (24, ((6, 19), (2, 20))),
    (10, "H"): (28, ((6, 15), (2, 16))),
}

# Row/column centres of the alignment patterns (ISO/IEC 18004 annex E).
_ALIGNMENT_CENTRES: dict[int, tuple[int, ...]] = {
    1: (),
    2: (6, 18),
    3: (6, 22),
    4: (6, 26),
    5: (6, 30),
    6: (6, 34),
    7: (6, 22, 38),
    8: (6, 24, 42),
    9: (6, 26, 46),
    10: (6, 28, 50),
}

_FORMAT_GENERATOR = 0x537  # x^10 + x^8 + x^5 + x^4 + x^2 + x + 1
_FORMAT_MASK = 0x5412  # 101010000010010
_VERSION_GENERATOR = 0x1F25  # x^12 + x^11 + x^10 + x^9 + x^8 + x^5 + x^2 + 1

# Data masks; r = row, c = column. A module is inverted where the test is true.
_MASKS = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
)

# Penalty rule 1 (runs of five or more) and rule 3 (finder-like patterns).
_LONG_RUN = re.compile(r"0{5,}|1{5,}")
_FINDER_LIKE = ("10111010000", "00001011101")


# --- public API ---------------------------------------------------------------


def encode(text: str, ecc: str = "M") -> Matrix:
    """Square module matrix, True = dark. No quiet zone (the caller adds 4 modules).
    Byte mode, UTF-8. Picks the smallest version (1-10) that fits at this ECC level.
    Raises ValueError when the text does not fit version 10 or ecc is not L/M/Q/H.

    The matrix is indexed matrix[row][column], row 0 at the top."""
    _check_ecc(ecc)
    data = _utf8(text)
    version = _pick_version(len(data), ecc)
    codewords = _codewords(data, version, ecc)
    modules, reserved = _function_patterns(version)
    _place_data(modules, reserved, codewords)
    return _apply_best_mask(modules, reserved, ecc)


def version_for(text: str, ecc: str = "M") -> int:
    """Version (1-10) that encode() would use for this text at this ECC level.
    Raises ValueError like encode()."""
    _check_ecc(ecc)
    return _pick_version(len(_utf8(text)), ecc)


# --- data and error correction ------------------------------------------------


def _check_ecc(ecc: object) -> None:
    if not isinstance(ecc, str) or ecc not in _ECC_BITS:
        raise ValueError(f"error correction level must be L, M, Q or H, not {ecc!r}")


def _utf8(text: object) -> bytes:
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, not {type(text).__name__}")
    return text.encode("utf-8")


def _count_bits(version: int) -> int:
    """Length of the byte-mode character count field."""
    return 8 if version <= 9 else 16


def _data_capacity(version: int, ecc: str) -> int:
    """Number of data codewords (bytes) in the symbol."""
    _ec_len, groups = _EC_BLOCKS[(version, ecc)]
    return sum(count * length for count, length in groups)


def _byte_capacity(version: int, ecc: str) -> int:
    """Most text bytes that fit in byte mode."""
    return (8 * _data_capacity(version, ecc) - 4 - _count_bits(version)) // 8


def _pick_version(length: int, ecc: str) -> int:
    for version in range(1, MAX_VERSION + 1):
        if length <= _byte_capacity(version, ecc):
            return version
    raise ValueError(
        f"text is {length} bytes; a version {MAX_VERSION} QR code at level {ecc} "
        f"holds at most {_byte_capacity(MAX_VERSION, ecc)}"
    )


def _data_stream(data: bytes, version: int, capacity: int) -> list[int]:
    """Mode, count, the bytes, terminator and padding as `capacity` codewords."""
    count_bits = _count_bits(version)
    value = (_MODE_BYTE << count_bits) | len(data)
    value = (value << (8 * len(data))) | int.from_bytes(data, "big")
    nbits = 4 + count_bits + 8 * len(data)
    terminator = min(4, 8 * capacity - nbits)
    nbits += terminator
    align = -nbits % 8
    nbits += align
    value <<= terminator + align
    stream = list(value.to_bytes(nbits // 8, "big"))
    missing = capacity - len(stream)
    stream += [0xEC, 0x11] * (missing // 2) + [0xEC] * (missing % 2)
    return stream


def _gf_tables() -> tuple[list[int], list[int]]:
    """Exponent and log tables for GF(256) with the polynomial 0x11D."""
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


_EXP, _LOG = _gf_tables()
_GENERATORS: dict[int, list[int]] = {}


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree: int) -> list[int]:
    """Coefficients (highest power first, leading 1 included) of
    (x - a^0)(x - a^1)...(x - a^(degree-1))."""
    gen = _GENERATORS.get(degree)
    if gen is None:
        gen = [1]
        for i in range(degree):
            root = _EXP[i]
            # Multiply by (x - root); subtraction is XOR in GF(256).
            gen = [a ^ _gf_mul(b, root) for a, b in zip(gen + [0], [0] + gen)]
        _GENERATORS[degree] = gen
    return gen


def _rs_remainder(data: list[int], degree: int) -> list[int]:
    """Reed-Solomon error correction codewords for one block."""
    gen = _rs_generator(degree)
    rem = [0] * degree
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        if factor:
            for i in range(degree):
                rem[i] ^= _gf_mul(gen[i + 1], factor)
    return rem


def _codewords(data: bytes, version: int, ecc: str) -> list[int]:
    """Final codeword sequence: data blocks interleaved, then EC blocks interleaved."""
    ec_len, groups = _EC_BLOCKS[(version, ecc)]
    stream = _data_stream(data, version, _data_capacity(version, ecc))
    blocks = []
    pos = 0
    for count, length in groups:
        for _ in range(count):
            blocks.append(stream[pos : pos + length])
            pos += length
    ec_blocks = [_rs_remainder(block, ec_len) for block in blocks]
    result = []
    for i in range(max(len(block) for block in blocks)):
        for block in blocks:
            if i < len(block):
                result.append(block[i])
    for i in range(ec_len):
        for ec_block in ec_blocks:
            result.append(ec_block[i])
    return result


# --- symbol layout ------------------------------------------------------------


def _format_bits(ecc: str, mask: int) -> int:
    """15-bit format information: level and mask, BCH(15,5), XOR 0x5412."""
    data = (_ECC_BITS[ecc] << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * _FORMAT_GENERATOR)
    return ((data << 10) | rem) ^ _FORMAT_MASK


def _version_bits(version: int) -> int:
    """18-bit version information: version number, BCH(18,6)."""
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * _VERSION_GENERATOR)
    return (version << 12) | rem


def _function_patterns(version: int) -> tuple[Matrix, Matrix]:
    """Matrix with every function pattern drawn, and the map of modules that are
    not available for data (function patterns plus the format areas)."""
    size = 17 + 4 * version
    modules = [[False] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    def put(row: int, col: int, dark: bool) -> None:
        modules[row][col] = dark
        reserved[row][col] = True

    # Finder patterns with their light separators.
    for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                row, col = top + dr, left + dc
                if 0 <= row < size and 0 <= col < size:
                    ring = max(abs(dr - 3), abs(dc - 3))
                    put(row, col, ring not in (2, 4))

    # Timing patterns between the separators.
    for i in range(8, size - 8):
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)

    # Alignment patterns, except the three that would overlap the finder patterns.
    centres = _ALIGNMENT_CENTRES[version]
    last = len(centres) - 1
    for i, row in enumerate(centres):
        for j, col in enumerate(centres):
            if (i, j) in ((0, 0), (0, last), (last, 0)):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    put(row + dr, col + dc, max(abs(dr), abs(dc)) != 1)

    # Format information areas (written per mask later) and the dark module.
    for i in range(9):
        reserved[8][i] = True
        reserved[i][8] = True
    for i in range(8):
        reserved[8][size - 1 - i] = True
        reserved[size - 1 - i][8] = True
    put(size - 8, 8, True)

    # Version information (versions 7 and up), least significant bit first: a block of
    # 6 rows x 3 columns at the top right and the same block transposed at the bottom left.
    if version >= 7:
        bits = _version_bits(version)
        for i in range(18):
            dark = (bits >> i) & 1 == 1
            near, far = i // 3, size - 11 + i % 3
            put(near, far, dark)  # top right, left of the finder pattern
            put(far, near, dark)  # bottom left, above the finder pattern

    return modules, reserved


def _place_data(modules: Matrix, reserved: Matrix, codewords: list[int]) -> None:
    """Fill the free modules in two-column strips from the bottom right, going up
    and down in turn and skipping the vertical timing column. Leftover modules
    (remainder bits) stay light."""
    size = len(modules)
    total_bits = 8 * len(codewords)
    index = 0
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = ((right + 1) & 2) == 0
        for step in range(size):
            row = size - 1 - step if upward else step
            for col in (right, right - 1):
                if reserved[row][col]:
                    continue
                if index < total_bits:
                    modules[row][col] = ((codewords[index >> 3] >> (7 - (index & 7))) & 1) == 1
                index += 1
        right -= 2


def _draw_format(modules: Matrix, ecc: str, mask: int) -> None:
    """Write both copies of the format information; bit 0 is the least significant."""
    size = len(modules)
    bits = _format_bits(ecc, mask)
    for i in range(15):
        dark = (bits >> i) & 1 == 1
        # Copy 1, around the top-left finder pattern (skips the timing modules).
        if i < 6:
            modules[i][8] = dark
        elif i < 8:
            modules[i + 1][8] = dark
        elif i == 8:
            modules[8][7] = dark
        else:
            modules[8][14 - i] = dark
        # Copy 2, split between the top-right and bottom-left finder patterns.
        if i < 8:
            modules[8][size - 1 - i] = dark
        else:
            modules[size - 15 + i][8] = dark


def _masked(modules: Matrix, reserved: Matrix, mask: int) -> Matrix:
    """Copy of the matrix with the mask applied to every module outside `reserved`."""
    test = _MASKS[mask]
    return [
        [dark ^ (not fixed and test(r, c)) for c, (dark, fixed) in enumerate(zip(row, fixed_row))]
        for r, (row, fixed_row) in enumerate(zip(modules, reserved))
    ]


def _penalty(matrix: Matrix) -> int:
    """Mask penalty score from the four data mask evaluation rules of ISO/IEC 18004."""
    size = len(matrix)
    rows = ["".join("1" if dark else "0" for dark in row) for row in matrix]
    cols = ["".join(row[c] for row in rows) for c in range(size)]
    score = 0
    for line in rows + cols:
        # Rule 1: 3 points for a run of five of one colour, 1 more per extra module.
        for run in _LONG_RUN.finditer(line):
            score += run.end() - run.start() - 2
        # Rule 3: 40 points per 1:1:3:1:1 pattern with four light modules on one side.
        score += 40 * (line.count(_FINDER_LIKE[0]) + line.count(_FINDER_LIKE[1]))
    # Rule 2: 3 points per 2x2 block of one colour (blocks may overlap).
    for r in range(size - 1):
        upper, lower = matrix[r], matrix[r + 1]
        for c in range(size - 1):
            if upper[c] == upper[c + 1] == lower[c] == lower[c + 1]:
                score += 3
    # Rule 4: 10 points per full 5% step of the dark share away from 50%.
    dark = sum(line.count("1") for line in rows)
    total = size * size
    score += 10 * (abs(20 * dark - 10 * total) // total)
    return score


def _apply_best_mask(modules: Matrix, reserved: Matrix, ecc: str) -> Matrix:
    """Try all eight masks; the lowest penalty wins, the lower mask number on a tie."""
    best_score = -1
    best: Matrix = []
    for mask in range(8):
        candidate = _masked(modules, reserved, mask)
        _draw_format(candidate, ecc, mask)
        score = _penalty(candidate)
        if best_score < 0 or score < best_score:
            best_score, best = score, candidate
    return best
