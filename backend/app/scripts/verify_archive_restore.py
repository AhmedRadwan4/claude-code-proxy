"""Check that every file in the Borg repo restores byte-identical to its MinIO object.

Run before setting ARCHIVE_RETENTION_DELETE_MINIO=true:
    docker compose exec archive-retention uv run python -m app.scripts.verify_archive_restore
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tarfile

import boto3
from botocore.config import Config


def main() -> int:
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["ARCHIVE_S3_ENDPOINT_URL"],
        aws_access_key_id=os.environ["ARCHIVE_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["ARCHIVE_S3_SECRET_ACCESS_KEY"],
        config=Config(s3={"addressing_style": "path"}),
    )
    bucket = os.environ["ARCHIVE_S3_BUCKET"]
    prefix = os.environ.get("ARCHIVE_S3_PREFIX", "").strip("/")
    repo = os.environ.get("ARCHIVE_BORG_REPOSITORY", "/borg/repository")
    marker = "/batch/"
    archives = subprocess.run(["borg", "list", "--short", repo], check=True, capture_output=True, text=True).stdout.split()
    ok = bad = 0
    for name in archives:
        proc = subprocess.Popen(["borg", "export-tar", f"{repo}::{name}", "-"], stdout=subprocess.PIPE)
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                rel = member.name.split(marker, 1)[1]
                key = f"{prefix}/{rel}" if prefix else rel
                restored = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
                original = hashlib.sha256(s3.get_object(Bucket=bucket, Key=key)["Body"].read()).hexdigest()
                if restored == original:
                    ok += 1
                else:
                    bad += 1
                    print(f"MISMATCH {name} {key}", flush=True)
        if proc.wait() != 0:
            print(f"export-tar failed: {name}", flush=True)
            bad += 1
    print(f"archives={len(archives)} files_ok={ok} mismatches={bad}", flush=True)
    return 1 if bad or not ok else 0


if __name__ == "__main__":
    sys.exit(main())
