import os
import uuid
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is missing")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY is missing")

# Initialize Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def upload_file(file, folder="uploads"):
    """
    Upload a Flask FileStorage object to the 'uploads' bucket in Supabase.

    Returns:
        str: Path of the uploaded file inside the bucket.
    """

    if not file:
        raise ValueError("No file provided")

    if not file.filename:
        raise ValueError("Filename is empty")

    extension = file.filename.rsplit(".", 1)[-1].lower()
    unique_name = f"{uuid.uuid4()}.{extension}"
    storage_path = f"{folder}/{unique_name}"

    file_bytes = file.read()

    print("=" * 50)
    print("Uploading to Supabase...")
    print("Bucket:", "uploads")
    print("Storage path:", storage_path)
    print("Content-Type:", file.content_type)
    print("File Size:", len(file_bytes), "bytes")

    try:
        result = supabase.storage.from_("uploads").upload(
            path=storage_path,
            file=file_bytes,
            file_options={
                "content-type": file.content_type
            }
        )

        print("Upload successful!")
        print(result)
        print("=" * 50)

        return storage_path

    except Exception as e:
        print("=" * 50)
        print("SUPABASE UPLOAD FAILED")
        print(type(e).__name__)
        print(str(e))
        print("=" * 50)
        raise


def get_public_url(storage_path):
    """
    Returns the public URL for a file stored in the uploads bucket.
    """

    if not storage_path:
        return ""

    return supabase.storage.from_("uploads").get_public_url(storage_path)