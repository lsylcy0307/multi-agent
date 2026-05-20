"""
Filter 990-PF filings from a local folder and upload up to 50 to GCS.

Usage
-----
python upload_990pf.py \
    --src /Users/suyeonlee/Downloads/2025_TEOS_XML_01A \
    --bucket ai-agent-platform-496418-ai-documents \
    --prefix raw/irs_990_xml/2025_990PF_TEST/ \
    [--limit 50]
"""

import argparse
import sys
from pathlib import Path

from google.cloud import storage


def is_990pf(path: Path) -> bool:
    """Quick scan — reads only enough of the file to find ReturnTypeCd."""
    try:
        # ReturnTypeCd is always in the first 2KB of the file
        chunk = path.read_bytes()[:2048].decode("utf-8", errors="ignore")
        return "<ReturnTypeCd>990PF</ReturnTypeCd>" in chunk
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src",    required=True, help="Local folder of XML files")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True, help="GCS prefix, e.g. raw/irs_990_xml/2025_990PF_TEST/")
    parser.add_argument("--limit",  type=int, default=50)
    args = parser.parse_args()

    src = Path(args.src)
    if not src.is_dir():
        print(f"ERROR: {src} is not a directory")
        sys.exit(1)

    xml_files = list(src.glob("*.xml"))
    print(f"Found {len(xml_files)} XML files in {src}")

    client = storage.Client()
    bucket = client.bucket(args.bucket)

    uploaded = skipped = 0

    for path in xml_files:
        if uploaded >= args.limit:
            break

        if not is_990pf(path):
            skipped += 1
            continue

        blob_name = args.prefix + path.name
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(path))
        uploaded += 1
        print(f"  [{uploaded}/{args.limit}] uploaded {path.name}")

    print(f"\nDone — {uploaded} uploaded, {skipped} skipped (not 990PF)")
    print(f"GCS path: gs://{args.bucket}/{args.prefix}")


if __name__ == "__main__":
    main()