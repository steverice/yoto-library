import base64
import struct
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_openai_client():
    """A mock OpenAI client whose images.generate() returns fake PNG bytes."""
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    fake_b64 = base64.b64encode(fake_png).decode()

    mock_image_data = MagicMock()
    mock_image_data.b64_json = fake_b64

    mock_response = MagicMock()
    mock_response.data = [mock_image_data]

    mock_client = MagicMock()
    mock_client.images.generate.return_value = mock_response
    mock_client.images.edit.return_value = mock_response
    return mock_client


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


def _ffmpeg_encode(wav_path: Path, output_path: Path, codec_args: list[str]) -> Path:
    """Helper: encode a WAV to another format via ffmpeg."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), *codec_args, str(output_path)],
        capture_output=True,
        check=True,
    )
    return output_path


def _longer_wav(tmp_path: Path, name: str = "tone.wav") -> Path:
    """Generate a short WAV with actual audio content (1 second sine wave).

    Some encoders need more than 1 sample to produce valid output.
    """
    wav_path = tmp_path / name
    sample_rate = 44100
    num_channels = 1
    bits_per_sample = 16
    duration_samples = sample_rate  # 1 second
    import math

    samples = []
    for i in range(duration_samples):
        # 440 Hz sine wave at ~50% volume
        val = int(16000 * math.sin(2 * math.pi * 440 * i / sample_rate))
        samples.append(struct.pack("<h", val))
    audio_data = b"".join(samples)
    data_size = len(audio_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        num_channels,
        sample_rate,
        sample_rate * num_channels * bits_per_sample // 8,
        num_channels * bits_per_sample // 8,
        bits_per_sample,
        b"data",
        data_size,
    )
    wav_path.write_bytes(header + audio_data)
    return wav_path


@pytest.fixture
def longer_wav(tmp_path):
    """A 1-second WAV with actual audio content."""
    return _longer_wav(tmp_path)


@pytest.fixture
def sample_mp3(tmp_path, longer_wav):
    """Create a sample MP3 file."""
    out = tmp_path / "sample.mp3"
    return _ffmpeg_encode(longer_wav, out, ["-c:a", "libmp3lame", "-b:a", "128k"])


@pytest.fixture
def sample_m4a(tmp_path, longer_wav):
    """Create a sample M4A (AAC) file."""
    out = tmp_path / "sample.m4a"
    return _ffmpeg_encode(longer_wav, out, ["-c:a", "aac", "-b:a", "128k"])


@pytest.fixture
def sample_flac(tmp_path, longer_wav):
    """Create a sample FLAC file."""
    out = tmp_path / "sample.flac"
    return _ffmpeg_encode(longer_wav, out, ["-c:a", "flac"])


@pytest.fixture
def sample_ogg(tmp_path, longer_wav):
    """Create a sample OGG (Vorbis) file."""
    out = tmp_path / "sample.ogg"
    return _ffmpeg_encode(longer_wav, out, ["-c:a", "libvorbis"])


@pytest.fixture
def sample_alac(tmp_path, longer_wav):
    """Create a sample ALAC (Apple Lossless) M4A file."""
    out = tmp_path / "sample_alac.m4a"
    return _ffmpeg_encode(longer_wav, out, ["-c:a", "alac"])
