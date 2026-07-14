"""
WolfRAT 2.0 — Sound Effects (Sirius Cybernetics Edition)
Generates Hitchhiker's Guide style door sounds on first run,
then plays them via PyQt6 QSoundEffect.
"""

import os
import struct
import math
import wave

SAMPLE_RATE = 22050
SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")


def _generate_tone(filename, freq_start, freq_end, duration_ms, volume=0.3):
    """Generate a WAV file with a frequency sweep."""
    os.makedirs(SOUNDS_DIR, exist_ok=True)
    filepath = os.path.join(SOUNDS_DIR, filename)

    if os.path.exists(filepath):
        return filepath

    num_samples = int(SAMPLE_RATE * duration_ms / 1000)
    samples = []

    for i in range(num_samples):
        t = i / SAMPLE_RATE
        progress = i / num_samples

        # Linear frequency sweep
        freq = freq_start + (freq_end - freq_start) * progress

        # Phase accumulation (prevents clicks at freq transitions)
        phase = 2 * math.pi * freq * t

        # Sine wave with fade in/out envelope
        envelope = 1.0
        fade_samples = int(num_samples * 0.05)  # 5% fade
        if i < fade_samples:
            envelope = i / fade_samples
        elif i > num_samples - fade_samples:
            envelope = (num_samples - i) / fade_samples

        value = int(32767 * volume * envelope * math.sin(phase))
        samples.append(max(-32768, min(32767, value)))

    with wave.open(filepath, 'w') as f:
        f.setnchannels(1)
        f.setsampwidth(2)  # 16-bit
        f.setframerate(SAMPLE_RATE)
        f.writeframes(struct.pack(f'<{len(samples)}h', *samples))

    return filepath


def _generate_click(filename, freq=800, duration_ms=40, volume=0.15):
    """Generate a short click/blip sound."""
    os.makedirs(SOUNDS_DIR, exist_ok=True)
    filepath = os.path.join(SOUNDS_DIR, filename)

    if os.path.exists(filepath):
        return filepath

    num_samples = int(SAMPLE_RATE * duration_ms / 1000)
    samples = []

    for i in range(num_samples):
        t = i / SAMPLE_RATE
        # Sharp attack, quick decay
        progress = i / num_samples
        envelope = max(0, 1.0 - progress * 3)  # Fast decay
        if progress < 0.05:
            envelope = progress / 0.05  # Very short attack

        value = int(32767 * volume * envelope * math.sin(2 * math.pi * freq * t))
        samples.append(max(-32768, min(32767, value)))

    with wave.open(filepath, 'w') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(struct.pack(f'<{len(samples)}h', *samples))

    return filepath


def generate_all_sounds():
    """Generate all sound effects. Returns dict of {name: filepath}."""
    sounds = {}

    # Button click — short, crisp
    sounds["click"] = _generate_click("click.wav", freq=800, duration_ms=40, volume=0.15)

    # Connect — happy ascending "ahhhh" (like a cheerful door)
    sounds["connect"] = _generate_tone(
        "connect.wav",
        freq_start=300, freq_end=900,
        duration_ms=450, volume=0.25
    )

    # Disconnect — sad descending "awww" (like a disappointed door)
    sounds["disconnect"] = _generate_tone(
        "disconnect.wav",
        freq_start=700, freq_end=250,
        duration_ms=500, volume=0.25
    )

    # Error/warning — two-tone alarm
    sounds["warning"] = _generate_tone(
        "warning.wav",
        freq_start=600, freq_end=400,
        duration_ms=300, volume=0.2
    )

    return sounds
