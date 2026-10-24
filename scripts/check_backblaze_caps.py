#!/usr/bin/env python3
"""Inspect Backblaze B2 usage without secrets: uses local `b2` CLI only."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str]) -> str:
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        return out.strip()
    except FileNotFoundError:
        die("Missing dependency: b2 CLI not found in PATH")
    except subprocess.CalledProcessError as exc:
        die(f"Command failed: {' '.join(cmd)}\n{exc.output}")
    return ""


def b2_authorize() -> dict:
    return json.loads(run(["b2", "authorize-account", "--json"]))


def b2_get(url: str, auth_token: str) -> dict:
    req = run(["b2", "get-file-info", "--noProgress", "--auth", auth_token, url])
    return json.loads(req)


def summarize_buckets(api_url: str, auth_token: str) -> None:
    buckets_raw = run(["b2", "list-buckets", "--json"])
    try:
        buckets = json.loads(buckets_raw)
    except json.JSONDecodeError:
        print("== Bucket list raw ==")
        print(buckets_raw)
        return

    if not buckets:
        print("No buckets found.")
        return

    print("\n== Buckets ==")
    for bucket in buckets:
        name = bucket.get("bucketName") or bucket.get("name")
        bucket_type = bucket.get("bucketType") or bucket.get("type")
        lifecycle = bucket.get("lifecycleRules")
        print(f"- {name} | type={bucket_type}")
        if lifecycle:
            print(f"    lifecycleRules: {json.dumps(lifecycle, ensure_ascii=False)}")


def summarize_usage(api_url: str, auth_token: str) -> None:
    print("\n== Usage summary ==")
    # b2 CLI does not expose storage-caps endpoints directly.
    print("Storage cap values are configured in Backblaze portal: B2 Cloud Storage > Caps & Alerts.")
    print("Open the portal and compare these bucket names with your configured daily storage cap.")


def summarize_account(auth: dict) -> None:
    print("== Account ==")
    for key in ["accountId", "apiUrl", "downloadUrl", "allowed", "storageApi"]:
        value = auth.get(key)
        if value:
            if isinstance(value, dict):
                print(f"- {key}:")
                for k, v in value.items():
                    print(f"    {k}: {v}")
            else:
                print(f"- {key}: {value}")


def main() -> int:
    if not shutil.which("b2"):
        die("Missing dependency: b2 CLI is required but not installed")
    auth = b2_authorize()
    api_url = auth.get("apiUrl") or os.environ.get("B2_API_URL") or "https://api.backblaze.com"
    auth_token = auth.get("authorizationToken")
    if not auth_token:
        die("Missing authorizationToken from b2 authorize-account")

    summarize_account(auth)
    summarize_buckets(api_url, auth_token)
    summarize_usage(api_url, auth_token)
    print("\nDica: se precisar, eu também posso te passar um comando/CLI para verificar o cap sem expor as credenciais.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
