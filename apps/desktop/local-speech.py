"""Offline speech worker. Standard input contains one recording or one speech fragment."""
from io import BytesIO
from pathlib import Path
import sys
import wave


def wav(samples, sample_rate):
    import numpy

    frames = (numpy.clip(samples, -1, 1) * 32767).astype("<i2")
    result = BytesIO()
    with wave.open(result, "wb") as stream:
        stream.setparams((1, 2, sample_rate, len(frames), "NONE", "not compressed"))
        stream.writeframes(frames.tobytes())
    return result.getvalue()


def recognize(sherpa, model, data):
    import numpy

    with wave.open(BytesIO(data), "rb") as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, 16000):
            raise ValueError("Expected mono 16 kHz PCM")
        frames = stream.readframes(stream.getnframes())
    samples = numpy.frombuffer(frames, dtype="<i2").astype(numpy.float32) / 32768
    engine = sherpa.OfflineRecognizer.from_sense_voice(
        model=str(model / "model.int8.onnx"), tokens=str(model / "tokens.txt"),
        language="zh", use_itn=True, num_threads=2, provider="cpu",
    )
    stream = engine.create_stream()
    stream.accept_waveform(16000, samples)
    engine.decode_stream(stream)
    return stream.result.text.strip().encode("utf-8")


def synthesize(sherpa, model, data):
    config = sherpa.OfflineTtsConfig(
        model=sherpa.OfflineTtsModelConfig(
            vits=sherpa.OfflineTtsVitsModelConfig(model=str(model / "model.int8.onnx"),
                lexicon=str(model / "lexicon.txt"), tokens=str(model / "tokens.txt"), dict_dir=str(model / "dict")),
            provider="cpu", num_threads=2, debug=False,
        ),
        rule_fsts=",".join(str(model / name) for name in ("phone.fst", "date.fst", "number.fst", "new_heteronym.fst")),
        max_num_sentences=2,
    )
    if not config.validate():
        raise ValueError("Invalid local speech model configuration")
    audio = sherpa.OfflineTts(config).generate(data.decode("utf-8"), sid=0, speed=1.0)
    if len(audio.samples) == 0:
        raise ValueError("Local speech returned no samples")
    return wav(audio.samples, audio.sample_rate)


def main():
    root, kind = Path(sys.argv[1]), sys.argv[2]
    sys.path.insert(0, str(root / "engine"))
    import sherpa_onnx

    action, limit = {"asr": (recognize, 961000), "tts": (synthesize, 2000)}[kind]
    data = sys.stdin.buffer.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Local speech input too large")
    sys.stdout.buffer.write(action(sherpa_onnx, root / "models" / kind, data))


if __name__ == "__main__":
    main()
