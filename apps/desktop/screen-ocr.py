"""Read a single desktop screenshot in memory and return its visible text boxes."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from guizhi.infrastructure.documents import ocr_engine


def main():
    limit = 28 << 20
    data = sys.stdin.buffer.read(limit + 1)
    if len(data) > limit or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Invalid screen capture")
    result, _ = ocr_engine()(data, use_cls=False)
    boxes = [{"quad": quad, "text": text, "confidence": float(score)}
             for quad, text, score in result or [] if score >= 0.78]
    sys.stdout.buffer.write(json.dumps(boxes, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
