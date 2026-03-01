import os
import re
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
CARS_MD_DIR = REPO_ROOT / "_cars"
SECRETS_DIR = REPO_ROOT / ".secrets"
WORK_DIR = REPO_ROOT / ".work"
#GDRIVE_SA_PATH = SECRETS_DIR / "gdrive-sa.json"

#R2_ACCOUNT_ID = os.environ["R2_ACCOUNT_ID"]
#R2_BUCKET = os.environ["R2_BUCKET"]
#R2_PUBLIC_BASE_URL = os.environ["R2_PUBLIC_BASE_URL"].rstrip("/")
GDRIVE_FOLDER_ID = os.environ["GDRIVE_FOLDER_ID"]

#R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

def run(cmd: list[str], *, check=True, capture=True, text=True, env=None, cwd=None) -> subprocess.CompletedProcess:
    print(">>", " ".join(cmd))
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=text,
        env=env,
        cwd=cwd,
    )

def slugify_for_filename(name: str) -> str:
    # Keep readable filenames, but safe for git files
    s = name.strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-_.]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "car"

def ensure_dirs():
    CARS_MD_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

def write_rclone_config() -> Path:
    """
    Create a minimal rclone config using Google Drive service account.
    We'll use the Drive API via service account; folder must be shared with SA email.
    """
    cfg_path = WORK_DIR / "rclone.conf"
    cfg = f"""
[gdrive]
type = drive
scope = drive.readonly
service_account_file = {GDRIVE_SA_PATH.as_posix()}
    """.strip() + "\n"
    cfg_path.write_text(cfg, encoding="utf-8")
    return cfg_path

def list_gdrive_car_folders(rclone_conf: Path) -> list[str]:
    """
    Expects structure:
    <GDRIVE_FOLDER_ID>/
      cars/
        'BMW5 tdi2.0 supreme'/
          Photo1.JPG
    We list folders under cars/.
    """
    # Find the "cars" folder under the provided root folder
    # 1) list children of root
    res = run([
        "rclone", "--config", rclone_conf.as_posix(),
        "lsjson", "--dirs-only", f"gdrive:/{GDRIVE_FOLDER_ID}"
    ])
    items = json.loads(res.stdout or "[]")
    cars_dir = next((x for x in items if x.get("Name") == "cars" and x.get("IsDir")), None)
    if not cars_dir:
        raise RuntimeError("Could not find a 'cars' directory under the provided GDRIVE_FOLDER_ID.")

    # 2) list folders under cars
    res2 = run([
        "rclone", "--config", rclone_conf.as_posix(),
        "lsjson", "--dirs-only", f"gdrive:/{GDRIVE_FOLDER_ID}/cars"
    ])
    cars = json.loads(res2.stdout or "[]")
    names = sorted([x["Name"] for x in cars if x.get("IsDir")])
    return names

def list_r2_car_folders() -> list[str]:
    """
    List prefixes (folders) under s3://bucket/cars/
    We use aws s3api list-objects-v2 with Delimiter='/'
    """
    res = run([
        "aws", "--endpoint-url", R2_ENDPOINT,
        "s3api", "list-objects-v2",
        "--bucket", R2_BUCKET,
        "--prefix", "cars/",
        "--delimiter", "/",
    ])
    data = json.loads(res.stdout or "{}")
    prefixes = data.get("CommonPrefixes", []) or []
    # prefix looks like "cars/BMW5 tdi2.0 supreme/"
    names = []
    for p in prefixes:
        pref = p.get("Prefix", "")
        if pref.startswith("cars/"):
            rest = pref[len("cars/"):]
            rest = rest.rstrip("/")
            if rest:
                names.append(rest)
    return sorted(set(names))

def list_gdrive_photos_for_folder(rclone_conf: Path, folder_name: str) -> list[str]:
    # list files directly inside that folder (no recursion)
    res = run([
        "rclone", "--config", rclone_conf.as_posix(),
        "lsjson", f"gdrive:/{GDRIVE_FOLDER_ID}/cars/{folder_name}"
    ])
    items = json.loads(res.stdout or "[]")
    files = []
    for x in items:
        if x.get("IsDir"):
            continue
        n = x.get("Name", "")
        if n:
            files.append(n)
    return sorted(files)

def copy_gdrive_folder_local(rclone_conf: Path, folder_name: str, dst: Path):
    # copies folder contents
    dst.mkdir(parents=True, exist_ok=True)
    run([
        "rclone", "--config", rclone_conf.as_posix(),
        "copy",
        f"gdrive:/{GDRIVE_FOLDER_ID}/cars/{folder_name}",
        dst.as_posix(),
        "--checksum",
        "--transfers", "8",
        "--checkers", "16",
    ])

def sync_local_to_r2(local_folder: Path, folder_name: str):
    # Upload to s3://bucket/cars/<folder_name>/
    run([
        "aws", "--endpoint-url", R2_ENDPOINT,
        "s3", "sync",
        local_folder.as_posix(),
        f"s3://{R2_BUCKET}/cars/{folder_name}/",
        "--no-progress",
    ])

def make_photo_url(folder_name: str, filename: str) -> str:
    # URL-encode per path segment to keep spaces safe
    return f"{R2_PUBLIC_BASE_URL}/{quote(folder_name)}/{quote(filename)}"

def create_md(folder_name: str, photo_files: list[str]) -> Path:
    now = datetime.now(timezone.utc)
    # Example datetime in filename: 20260301T193010Z
    dt = now.strftime("%Y%m%dT%H%M%SZ")
    slug = slugify_for_filename(folder_name)
    md_path = CARS_MD_DIR / f"{slug}-{dt}.md"

    urls = [make_photo_url(folder_name, f) for f in photo_files]

    # YAML front matter (simple + compatible with Jekyll/Eleventy)
    lines = []
    lines.append("---")
    lines.append(f'title: "{folder_name.replace(chr(34), r"\"")}"')
    lines.append("photos:")
    for u in urls:
        lines.append(f'  - "{u}"')
    lines.append("---")
    lines.append("")  # content body empty; add later if you want
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path

def main():
    ensure_dirs()

    if not GDRIVE_SA_PATH.exists():
        raise RuntimeError("Missing .secrets/gdrive-sa.json (did the workflow step write it?).")

    # Clean working folder each run
    if WORK_DIR.exists():
        for p in WORK_DIR.iterdir():
            if p.is_file():
                p.unlink()
            else:
                shutil.rmtree(p, ignore_errors=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    rclone_conf = write_rclone_config()

    gdrive_folders = list_gdrive_car_folders(rclone_conf)
    r2_folders = list_r2_car_folders()

    gdrive_set = set(gdrive_folders)
    r2_set = set(r2_folders)

    missing_on_r2 = sorted(list(gdrive_set - r2_set))
    print(f"GDrive folders: {len(gdrive_folders)}")
    print(f"R2 folders: {len(r2_folders)}")
    print(f"Missing on R2: {len(missing_on_r2)}")
    for x in missing_on_r2:
        print("  -", x)

    # For each missing folder:
    # 1) download locally
    # 2) upload to R2
    # 3) generate md with URLs
    for folder_name in missing_on_r2:
        local_dst = WORK_DIR / "cars" / folder_name
        copy_gdrive_folder_local(rclone_conf, folder_name, local_dst)
        sync_local_to_r2(local_dst, folder_name)

        photo_files = list_gdrive_photos_for_folder(rclone_conf, folder_name)
        md = create_md(folder_name, photo_files)
        print("Created:", md.relative_to(REPO_ROOT))

if __name__ == "__main__":
    main()