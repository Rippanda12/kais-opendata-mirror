"""Upload files to Cloudflare R2 (S3 API). Credentials come from the environment:
R2_ENDPOINT, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY.

    python tools/r2-upload.py <local file> <key> [<local file> <key> ...]
"""
import mimetypes
import os
import sys

import boto3
from boto3.s3.transfer import TransferConfig

TYPES = {".pmtiles": "application/vnd.pmtiles", ".parquet": "application/vnd.apache.parquet", ".json": "application/json"}


def main(args):
    s3 = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"], region_name="auto",
                      aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                      aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"])
    cfg = TransferConfig(multipart_threshold=64 * 2**20, multipart_chunksize=64 * 2**20, max_concurrency=4)
    for path, key in zip(args[::2], args[1::2]):
        ext = os.path.splitext(path)[1]
        ctype = TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
        s3.upload_file(path, os.environ["R2_BUCKET"], key, Config=cfg,
                       ExtraArgs={"ContentType": ctype, "CacheControl": "public, max-age=3600"})
        print("uploaded", key, os.path.getsize(path))


if __name__ == "__main__":
    main(sys.argv[1:])
