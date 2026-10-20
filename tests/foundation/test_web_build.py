import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"


@pytest.mark.skipif(
    shutil.which("npm") is None or not (WEB / "node_modules").exists(),
    reason="web dependencies not installed",
)
def test_build_adds_nonce_and_version_json(tmp_path: Path) -> None:
    out = tmp_path / "dist"
    subprocess.run(
        ["npm", "run", "build", "--", "--outDir", str(out)],
        cwd=WEB,
        check=True,
        capture_output=True,
        timeout=120,
    )
    html = (out / "index.html").read_text()
    tags = re.findall(r"<script\b[^>]*>", html)
    assert tags
    assert all('nonce="__CSP_NONCE__"' in tag for tag in tags)
    version = json.loads((out / "version.json").read_text())
    assert re.fullmatch(r"0\.1\.0\+([0-9a-f]{7,}|nogit)", version["app_version"])
