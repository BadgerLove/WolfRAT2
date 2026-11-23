"""QR code encoder for the Web Admin pairing link (wolfrat/qr_code.py).

The structure tests need only pytest. The round-trip tests draw the matrix with
a 4-module quiet zone at 8 px per module and read it back with OpenCV."""

import ast
import sys
from functools import lru_cache
from pathlib import Path

import pytest

from wolfrat import qr_code as qr

PAIR_URL = "http://192.168.1.20:8070/#pair=K7M3QX"
WIDE_URL = "http://203.0.113.7:65535/#pair=ABCDEF"
FILLER = "WolfRAT2 pairing link 0123456789 http://192.168.1.20:8070/#pair=K7M3QX "

# Byte-mode capacity in bytes for versions 1-10 (ISO/IEC 18004 table 7).
BYTE_CAPACITY = {
    "L": (17, 32, 53, 78, 106, 134, 154, 192, 230, 271),
    "M": (14, 26, 42, 62, 84, 106, 122, 152, 180, 213),
    "Q": (11, 20, 32, 46, 60, 74, 86, 108, 130, 151),
    "H": (7, 14, 24, 34, 44, 58, 64, 84, 98, 119),
}

# Remainder bits after the last codeword for versions 1-10 (ISO/IEC 18004 table 1).
REMAINDER_BITS = (0, 7, 7, 7, 7, 7, 0, 0, 0, 0)

# Format information as drawn, for mask patterns 0-7 (ISO/IEC 18004 annex C).
# The XOR mask is 101010000010010: level M with mask 0 has all-zero data and BCH bits.
FORMAT_WORDS = {
    "L": (
        "111011111000100", "111001011110011", "111110110101010", "111100010011101",
        "110011000101111", "110001100011000", "110110001000001", "110100101110110",
    ),
    "M": (
        "101010000010010", "101000100100101", "101111001111100", "101101101001011",
        "100010111111001", "100000011001110", "100111110010111", "100101010100000",
    ),
    "Q": (
        "011010101011111", "011000001101000", "011111100110001", "011101000000110",
        "010010010110100", "010000110000011", "010111011011010", "010101111101101",
    ),
    "H": (
        "001011010001001", "001001110111110", "001110011100111", "001100111010000",
        "000011101100010", "000001001010101", "000110100001100", "000100000111011",
    ),
}  # fmt: skip

# Version information for versions 7-10 (ISO/IEC 18004 annex D).
VERSION_WORDS = {7: 0x07C94, 8: 0x085BC, 9: 0x09A99, 10: 0x0A4D3}

# Alignment pattern centres (ISO/IEC 18004 annex E).
ALIGNMENT_CENTRES = {
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

FINDER = ("#######", "#.....#", "#.###.#", "#.###.#", "#.###.#", "#.....#", "#######")
ALIGNMENT = ("#####", "#...#", "#.#.#", "#...#", "#####")

# PAIR_URL at level M: version 3, mask 3.
PAIR_URL_SYMBOL = (
    "#######.#...#####..#..#######",
    "#.....#.#...#.#.#.#.#.#.....#",
    "#.###.#...#.##..#.##..#.###.#",
    "#.###.#.#..#...####.#.#.###.#",
    "#.###.#...#..##.#####.#.###.#",
    "#.....#..###..#....#..#.....#",
    "#######.#.#.#.#.#.#.#.#######",
    "........##.#.###.#..#........",
    "#.##.###...####..#..#.#..#.##",
    ".#####....###....###..#.#...#",
    "..#..##.#..##.#.#.#.##....##.",
    "###.##.#...####...#......#..#",
    "...#.##.####.....#####.#.##..",
    "...#...###...#..##.#####...##",
    ".#..#.#.##....#..#.#....#####",
    "####.#..#...##..#....#.#...#.",
    ".###.###....#...#.#....##..#.",
    ".#...#.....#.#.##.#.......##.",
    "#.###.#.###.#..##.#...#..##..",
    ".........##.#..#.#.##.##..#..",
    ".###..##.##..#.############..",
    "........#.##.####.###...#####",
    "#######.####.#..##.##.#.##.#.",
    "#.....#.#...#.....###...##.##",
    "#.###.#..###..#.##..#######..",
    "#.###.#.##..####........#.#.#",
    "#.###.#.#..##...#...##......#",
    "#.....#.....##......##.##..#.",
    "#######.#.##..#.#.#.##.###.#.",
)


def _text(length):
    """ASCII text of exactly `length` bytes."""
    return (FILLER * (length // len(FILLER) + 1))[:length]


@lru_cache(maxsize=None)
def _full_symbol(version, ecc="M"):
    """Matrix for a text that fills this version at this level exactly."""
    return qr.encode(_text(BYTE_CAPACITY[ecc][version - 1]), ecc)


def _rows(matrix, top, left, height, width):
    return tuple(
        "".join("#" if matrix[r][c] else "." for c in range(left, left + width))
        for r in range(top, top + height)
    )


def _format_copies(matrix):
    """Both copies of the format information, most significant bit first."""
    size = len(matrix)
    first = [(8, c) for c in (0, 1, 2, 3, 4, 5, 7, 8)] + [(r, 8) for r in (7, 5, 4, 3, 2, 1, 0)]
    second = [(r, 8) for r in range(size - 1, size - 8, -1)] + [
        (8, c) for c in range(size - 8, size)
    ]
    return tuple(
        "".join("1" if matrix[r][c] else "0" for r, c in cells) for cells in (first, second)
    )


# --- structure -------------------------------------------------------------------


@pytest.mark.parametrize("version", range(1, 11))
def test_size_is_17_plus_4_per_version(version):
    matrix = _full_symbol(version)
    assert qr.version_for(_text(BYTE_CAPACITY["M"][version - 1])) == version
    assert len(matrix) == 17 + 4 * version
    assert all(len(row) == len(matrix) for row in matrix)
    assert all(type(module) is bool for row in matrix for module in row)


def test_short_texts_pick_small_versions():
    assert qr.version_for("") == 1
    assert len(qr.encode("")) == 21
    assert qr.version_for("hello") == 1
    assert len(qr.encode("hello")) == 21
    assert qr.version_for(PAIR_URL) == 3
    assert len(qr.encode(PAIR_URL)) == 29


@pytest.mark.parametrize("version", range(1, 11))
def test_finder_patterns_and_separators(version):
    m = _full_symbol(version)
    size = len(m)
    for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
        assert _rows(m, top, left, 7, 7) == FINDER
    for i in range(8):
        assert not m[7][i] and not m[i][7]  # top left
        assert not m[7][size - 1 - i] and not m[i][size - 8]  # top right
        assert not m[size - 8][i] and not m[size - 1 - i][7]  # bottom left


@pytest.mark.parametrize("version", range(1, 11))
def test_timing_patterns_alternate(version):
    m = _full_symbol(version)
    for i in range(8, len(m) - 8):
        assert m[6][i] == (i % 2 == 0)
        assert m[i][6] == (i % 2 == 0)


@pytest.mark.parametrize("version", range(1, 11))
def test_dark_module(version):
    assert _full_symbol(version)[4 * version + 9][8] is True


@pytest.mark.parametrize("version", range(2, 11))
def test_alignment_patterns(version):
    m = _full_symbol(version)
    centres = ALIGNMENT_CENTRES[version]
    first, last = centres[0], centres[-1]
    for row in centres:
        for col in centres:
            if (row, col) in ((first, first), (first, last), (last, first)):
                continue  # would overlap a finder pattern
            assert _rows(m, row - 2, col - 2, 5, 5) == ALIGNMENT


def test_format_words_match_the_standard_table():
    for ecc, words in FORMAT_WORDS.items():
        for mask, word in enumerate(words):
            assert format(qr._format_bits(ecc, mask), "015b") == word


@pytest.mark.parametrize("ecc", "LMQH")
def test_format_information_both_copies(ecc):
    first, second = _format_copies(qr.encode(PAIR_URL, ecc))
    assert first == second
    assert first in FORMAT_WORDS[ecc]


@pytest.mark.parametrize("version", range(7, 11))
def test_version_information_both_blocks(version):
    m = _full_symbol(version)
    size = len(m)
    top_right = sum(m[i // 3][size - 11 + i % 3] << i for i in range(18))
    bottom_left = sum(m[size - 11 + i % 3][i // 3] << i for i in range(18))
    assert qr._version_bits(version) == VERSION_WORDS[version]
    assert top_right == bottom_left == VERSION_WORDS[version]


@pytest.mark.parametrize("version", range(1, 11))
def test_block_table_fills_the_data_area(version):
    _modules, reserved = qr._function_patterns(version)
    free = sum(row.count(False) for row in reserved)
    for ecc in "LMQH":
        ec_len, groups = qr._EC_BLOCKS[(version, ecc)]
        codewords = sum(count * (data + ec_len) for count, data in groups)
        assert 8 * codewords + REMAINDER_BITS[version - 1] == free
        if len(groups) == 2:
            assert groups[1][1] == groups[0][1] + 1


@pytest.mark.parametrize("ecc", "LMQH")
def test_byte_capacity_matches_the_standard_table(ecc):
    for version, capacity in enumerate(BYTE_CAPACITY[ecc], start=1):
        assert qr.version_for(_text(capacity), ecc) == version
        if version < qr.MAX_VERSION:
            assert qr.version_for(_text(capacity + 1), ecc) == version + 1


def test_length_counts_utf8_bytes():
    assert qr.version_for("é" * 7) == 1  # 14 bytes, the 1-M limit
    assert qr.version_for("é" * 7 + "a") == 2
    assert qr.version_for("€" * 71) == 10  # 213 bytes, the 10-M limit
    with pytest.raises(ValueError):
        qr.encode("€" * 71 + "a")


def test_data_codewords_for_hello():
    # Mode 0100, count 00000101, "hello", terminator 0000, then 0xEC/0x11 padding.
    assert qr._data_stream(b"hello", 1, 16) == [
        0x40, 0x56, 0x86, 0x56, 0xC6, 0xC6, 0xF0,
        0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC,
    ]  # fmt: skip


def test_count_field_is_16_bits_from_version_10():
    # 180 bytes at 9-M: 8-bit count 0xB4. 181 bytes at 10-M: 16-bit count 0x00B5.
    assert qr._data_stream(b"A" * 180, 9, 182)[:3] == [0x4B, 0x44, 0x14]
    assert qr._data_stream(b"A" * 181, 10, 216)[:4] == [0x40, 0x0B, 0x54, 0x14]


def test_reed_solomon_known_blocks():
    # "HELLO WORLD" at 1-M (thonky.com QR tutorial).
    data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
    assert qr._rs_remainder(data, 10) == [196, 35, 39, 119, 235, 215, 231, 226, 93, 23]
    # "01234567" at 1-M (ISO/IEC 18004 annex I).
    data = [0x10, 0x20, 0x0C, 0x56, 0x61, 0x80] + [0xEC, 0x11] * 5
    assert qr._rs_remainder(data, 10) == [
        0xA5, 0x24, 0xD4, 0xC1, 0xED, 0x36, 0xC7, 0x87, 0x2C, 0x55,
    ]  # fmt: skip


def test_reed_solomon_blocks_have_zero_syndromes():
    data = [(i * 37 + 11) % 256 for i in range(44)]
    for degree in sorted({ec_len for ec_len, _groups in qr._EC_BLOCKS.values()}):
        block = data + qr._rs_remainder(data, degree)
        for i in range(degree):
            value = 0
            for byte in block:
                value = qr._gf_mul(value, qr._EXP[i]) ^ byte
            assert value == 0, (degree, i)


def test_blocks_are_interleaved():
    # 5-Q: two blocks of 15 data codewords, two of 16, 18 EC codewords each.
    data = bytes(range(60))
    stream = qr._data_stream(data, 5, 62)
    blocks = [stream[0:15], stream[15:30], stream[30:46], stream[46:62]]
    ec_blocks = [qr._rs_remainder(block, 18) for block in blocks]
    out = qr._codewords(data, 5, "Q")
    assert len(out) == 134
    assert out[:4] == [block[0] for block in blocks]
    assert out[60:62] == [blocks[2][15], blocks[3][15]]
    assert out[62:66] == [ec[0] for ec in ec_blocks]
    assert out[-4:] == [ec[17] for ec in ec_blocks]


def test_text_too_long_raises():
    with pytest.raises(ValueError, match="at most 213"):
        qr.encode(_text(214))
    with pytest.raises(ValueError):
        qr.version_for(_text(214))
    for ecc in "LMQH":
        with pytest.raises(ValueError):
            qr.encode(_text(BYTE_CAPACITY[ecc][-1] + 1), ecc)


@pytest.mark.parametrize("ecc", ["", "X", "m", "LM", None, 1])
def test_bad_ecc_raises(ecc):
    with pytest.raises(ValueError):
        qr.encode("hello", ecc)
    with pytest.raises(ValueError):
        qr.version_for("hello", ecc)


def test_pairing_url_symbol_is_fixed():
    """Same input, same matrix: a change in layout or mask choice shows up here."""
    matrix = qr.encode(PAIR_URL, "M")
    assert _rows(matrix, 0, 0, 29, 29) == PAIR_URL_SYMBOL
    assert qr.encode(PAIR_URL, "M") == matrix


def test_encoder_uses_only_the_standard_library():
    tree = ast.parse(Path(qr.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").partition(".")[0])
    assert imported <= set(sys.stdlib_module_names)


# --- round trip through OpenCV ----------------------------------------------------


@pytest.fixture(scope="module")
def opencv():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    return cv2, np


def _image(np, matrix, scale=8, quiet=4):
    """Greyscale image: white (255) background and quiet zone, dark modules 0."""
    grid = np.pad(np.array(matrix, dtype=bool), quiet, constant_values=False)
    image = np.where(grid, 0, 255).astype(np.uint8)
    return image.repeat(scale, axis=0).repeat(scale, axis=1)


def _decode(opencv, matrix):
    cv2, np = opencv
    text, _points, _straight = cv2.QRCodeDetector().detectAndDecode(_image(np, matrix))
    return text


@pytest.mark.parametrize("text", ["hello", PAIR_URL, WIDE_URL])
def test_round_trip(opencv, text):
    assert _decode(opencv, qr.encode(text)) == text


@pytest.mark.parametrize("ecc", "LMQH")
def test_round_trip_each_level(opencv, ecc):
    matrix = qr.encode(PAIR_URL, ecc)
    assert len(matrix) == 17 + 4 * qr.version_for(PAIR_URL, ecc)
    assert _decode(opencv, matrix) == PAIR_URL


@pytest.mark.parametrize(("length", "version"), [(115, 7), (200, 10)])
def test_round_trip_with_version_information(opencv, length, version):
    text = _text(length)
    assert qr.version_for(text) == version
    assert _decode(opencv, qr.encode(text)) == text


@pytest.mark.parametrize("ecc", "LMQH")
@pytest.mark.parametrize("version", range(1, 11))
def test_round_trip_every_version_and_level(opencv, version, ecc):
    """Each symbol filled to capacity, so every block table entry gets decoded.

    OpenCV's classic detector fails to locate a few valid symbols (here 8-M; it
    misses OpenCV's own encoder output for the same text too), so any it misses
    go to the ArUco-based detector."""
    cv2, np = opencv
    text = _text(BYTE_CAPACITY[ecc][version - 1])
    image = _image(np, _full_symbol(version, ecc))
    decoded = cv2.QRCodeDetector().detectAndDecode(image)[0]
    if not decoded and hasattr(cv2, "QRCodeDetectorAruco"):
        decoded = cv2.QRCodeDetectorAruco().detectAndDecode(image)[0]
    assert decoded == text
