from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess  # nosec B404
import tempfile
import time
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve an AIO fleet automation token."
    )
    parser.add_argument("--app-id-env", default="AIO_FLEET_APP_ID")
    parser.add_argument(
        "--installation-id-env", default="AIO_FLEET_APP_INSTALLATION_ID"
    )
    parser.add_argument("--private-key-env", default="AIO_FLEET_APP_PRIVATE_KEY")
    parser.add_argument("--fallback-env", action="append", default=[])
    args = parser.parse_args()

    app_id = os.environ.get(args.app_id_env, "").strip()
    installation_id = os.environ.get(args.installation_id_env, "").strip()
    private_key = os.environ.get(args.private_key_env, "").strip()
    if app_id and installation_id and private_key:
        print(create_installation_token(app_id, installation_id, private_key))
        return 0

    for env_name in args.fallback_env:
        value = os.environ.get(env_name, "").strip()
        if value:
            print(value)
            return 0
    return 1


def create_installation_token(
    app_id: str, installation_id: str, private_key: str
) -> str:
    jwt = _create_jwt(app_id, private_key)
    request = urllib.request.Request(  # nosec B310
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        data=b"{}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {jwt}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
        payload = json.loads(response.read().decode("utf-8"))
    token = str(payload.get("token", ""))
    if not token:
        raise RuntimeError(
            "GitHub App installation token response did not include token"
        )
    return token


def _create_jwt(app_id: str, private_key: str) -> str:
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("openssl is required to sign GitHub App JWTs")

    now = int(time.time())
    header = _base64url_json({"alg": "RS256", "typ": "JWT"})
    payload = _base64url_json({"iat": now - 60, "exp": now + 540, "iss": app_id})
    signing_input = f"{header}.{payload}".encode()
    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = Path(tmpdir) / "app-private-key.pem"
        key_path.write_text(private_key.replace("\\n", "\n"))
        result = subprocess.run(  # nosec B603
            [openssl, "dgst", "-sha256", "-sign", str(key_path)],
            input=signing_input,
            check=False,
            capture_output=True,
        )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
    signature = _base64url_bytes(result.stdout)
    return f"{header}.{payload}.{signature}"


def _base64url_json(payload: dict[str, object]) -> str:
    return _base64url_bytes(json.dumps(payload, separators=(",", ":")).encode())


def _base64url_bytes(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


if __name__ == "__main__":
    raise SystemExit(main())
