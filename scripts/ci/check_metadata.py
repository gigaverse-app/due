"""Prove both release archives contain the current package README as their description."""

import email.policy
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def check_metadata(raw: bytes, readme: str) -> None:
    metadata = Parser(policy=email.policy.default).parsestr(raw.decode("utf-8"))
    assert metadata["Name"] == "django-due", "wrong distribution name"
    assert metadata["Description-Content-Type"] == "text/markdown", "README must render as Markdown"
    description = metadata.get_payload()
    assert isinstance(description, str), "expected a text description"
    assert description.strip() == readme.strip(), "distribution description differs from package/README.md"


def main() -> None:
    readme = (ROOT / "package/README.md").read_text(encoding="utf-8")
    wheels = list((ROOT / "dist").glob("*.whl"))
    sdists = list((ROOT / "dist").glob("*.tar.gz"))
    assert len(wheels) == len(sdists) == 1, "expected one wheel and one sdist"
    with zipfile.ZipFile(wheels[0]) as wheel:
        metadata = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
        assert len(metadata) == 1
        check_metadata(wheel.read(metadata[0]), readme)
    with tarfile.open(sdists[0]) as sdist:
        metadata = [member for member in sdist if member.name.endswith("/PKG-INFO") and member.name.count("/") == 1]
        assert len(metadata) == 1
        stream = sdist.extractfile(metadata[0])
        assert stream is not None
        check_metadata(stream.read(), readme)
    print("Wheel and sdist both contain the current Markdown README")


if __name__ == "__main__":
    main()
