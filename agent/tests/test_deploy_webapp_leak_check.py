"""scripts/deploy_webapp.sh --check: the gate between a web build and the box.

/app is public, and Expo inlines every EXPO_PUBLIC_* value the code reads into
the bundle. The one time this went wrong would be the one time a credential
was served to anyone who loads the page, so the gate is pinned on the bytes:
a planted secret must fail the run, and the failure must name the variable
without echoing its value into a terminal or CI log.

The opposite failure matters too. A gate that cries wolf gets bypassed, so the
two legitimate cases that broke the first draft are pinned as passes: the
settings screen tells the operator "DEV_API_TOKEN lives in secrets.env", and
Firebase's EXPO_PUBLIC_ web key is public by design.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy_webapp.sh"
SECRET = "sk-test-0123456789abcdef"
DEV_TOKEN = "devtok-fedcba9876543210"
FIREBASE_KEY = "AIzaPublicWebKey123456789"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture()
def env_file(tmp_path: Path) -> Path:
    f = tmp_path / "agent.env"
    f.write_text(
        f'ANTHROPIC_API_KEY="{SECRET}"\n'
        f"EXPO_PUBLIC_DEV_API_TOKEN={DEV_TOKEN}\n"
        f"EXPO_PUBLIC_FIREBASE_API_KEY={FIREBASE_KEY}\n"
        "SHORT_TOKEN=abc\n"
        "LOG_LEVEL=info\n",
        encoding="utf-8",
    )
    return f


@pytest.fixture()
def build(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    js = root / "_expo" / "static" / "js" / "web"
    js.mkdir(parents=True)
    (root / "index.html").write_text("<html><body>abc</body></html>", encoding="utf-8")
    (js / "entry-abc.js").write_text(
        "const API='https://trader.fusapp.com';"
        "const hint='secrets.env içindeki DEV_API_TOKEN';"
        f"const fb={{apiKey:'{FIREBASE_KEY}'}};",
        encoding="utf-8",
    )
    return root


def _check(build: Path, env_file: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), "--check", str(build)],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(build.parent),
            "WEBAPP_SECRET_ENV_FILES": str(env_file),
            **extra,
        },
        timeout=60,
    )


def _plant(build: Path, text: str) -> None:
    js = next(build.rglob("entry-*.js"))
    js.write_text(js.read_text(encoding="utf-8") + text, encoding="utf-8")


def test_clean_build_passes(build: Path, env_file: Path) -> None:
    r = _check(build, env_file)
    assert r.returncode == 0, r.stderr
    assert "clean" in r.stdout


@pytest.mark.parametrize(
    ("value", "name"), [(SECRET, "ANTHROPIC_API_KEY"), (DEV_TOKEN, "EXPO_PUBLIC_DEV_API_TOKEN")]
)
def test_planted_secret_value_fails_and_is_not_echoed(
    build: Path, env_file: Path, value: str, name: str
) -> None:
    _plant(build, f"const t='{value}';")
    r = _check(build, env_file)
    assert r.returncode != 0
    assert f"value of {name}" in r.stderr
    assert value not in r.stdout + r.stderr


def test_client_code_reading_the_dev_token_var_fails(build: Path, env_file: Path) -> None:
    _plant(build, "const t=process.env.EXPO_PUBLIC_DEV_API_TOKEN;")
    r = _check(build, env_file)
    assert r.returncode != 0
    assert "EXPO_PUBLIC_DEV_API_TOKEN is referenced" in r.stderr


def test_short_values_are_ignored(build: Path, env_file: Path) -> None:
    # "abc" is in index.html; a 3-char value would match almost any bundle.
    assert _check(build, env_file).returncode == 0


def test_bundle_built_against_another_api_fails(build: Path, env_file: Path) -> None:
    r = _check(build, env_file, EXPECT_API_URL="https://trader-stg.fusapp.tech")
    assert r.returncode != 0
    assert "WRONG TARGET" in r.stderr


def test_not_a_build_directory_fails(tmp_path: Path, env_file: Path) -> None:
    r = _check(tmp_path, env_file)
    assert r.returncode != 0
    assert "no index.html" in r.stderr
