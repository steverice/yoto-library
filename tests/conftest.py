import struct
from pathlib import Path

import pytest


@pytest.fixture
def tmp_playlist(tmp_path):
    """Create a temporary playlist folder with sample files."""
    playlist_dir = tmp_path / "Test Playlist"
    playlist_dir.mkdir()
    return playlist_dir


@pytest.fixture
def sample_wav(tmp_path):
    """Create a minimal valid WAV file (1 sample of silence)."""
    wav_path = tmp_path / "silence.wav"
    # Minimal WAV: 44-byte header + 2 bytes of audio data (one 16-bit sample)
    sample_rate = 44100
    num_channels = 1
    bits_per_sample = 16
    data_size = 2  # 1 sample * 2 bytes
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,  # fmt chunk size
        1,  # PCM format
        num_channels,
        sample_rate,
        sample_rate * num_channels * bits_per_sample // 8,
        num_channels * bits_per_sample // 8,
        bits_per_sample,
        b"data",
        data_size,
    )
    wav_path.write_bytes(header + b"\x00\x00")
    return wav_path
