import boto3
from urllib.parse import urlparse
from botocore.config import Config

from utils.config import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    AWS_SESSION_TOKEN,
    AWS_REGION,
    AWS_S3_BUCKET_NAME
)

client_kwargs = {
    "region_name": AWS_REGION,
    # S3 checks are request-time enrichment, not a reason to keep a web
    # request (and its database session) blocked indefinitely.  Bound socket
    # waits and retries so a transient S3 outage degrades an audio indicator
    # instead of exhausting the shared SQLAlchemy pool.
    "config": Config(
        signature_version="s3v4",
        connect_timeout=5,
        read_timeout=10,
        retries={"max_attempts": 2, "mode": "standard"},
    ),
}
if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
    client_kwargs["aws_access_key_id"] = AWS_ACCESS_KEY_ID
    client_kwargs["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY
    if AWS_SESSION_TOKEN:
        client_kwargs["aws_session_token"] = AWS_SESSION_TOKEN

s3_client = boto3.client("s3", **client_kwargs)


class S3Service:

    def __init__(self):
        self.client = s3_client
        self.bucket_name = AWS_S3_BUCKET_NAME

    def _parse_s3_uri(self, s3_uri: str) -> tuple:
        """
        Parse an s3:// URI into (bucket, key).
        Also handles plain keys (uses default bucket).
        """
        s3_uri = s3_uri.strip()

        if s3_uri.startswith("s3://"):
            parsed = urlparse(s3_uri)
            bucket = parsed.netloc
            key = parsed.path.lstrip("/")
            return bucket, key
        else:
            # Just a key, use default bucket
            return self.bucket_name, s3_uri

    def _normalize_key(self, s3_key: str) -> str:
        """Legacy method for backward compatibility."""
        s3_key = s3_key.strip()

        if s3_key.startswith("s3://"):
            s3_key = urlparse(s3_key).path.lstrip("/")

        return s3_key

    def upload_file(self, file_path: str, s3_key: str):
        bucket, key = self._parse_s3_uri(s3_key) if s3_key.startswith("s3://") else (self.bucket_name, self._normalize_key(s3_key))

        self.client.upload_file(
            Filename=file_path,
            Bucket=bucket,
            Key=key
        )

        return f"s3://{bucket}/{key}"

    def download_file(
        self,
        s3_uri: str,
        local_path: str = "storage/audio/temp.mp3"
    ):
        bucket, key = self._parse_s3_uri(s3_uri)

        print(f"Bucket: {bucket}")
        print(f"Key: '{key}'")

        # Check if object exists and get metadata first
        try:
            head = self.client.head_object(Bucket=bucket, Key=key)
            size = head['ContentLength']
            print(f"Object size: {size} bytes")
            
            if size < 1024:
                raise ValueError(f"Audio file is corrupted/empty ({size} bytes) — likely a failed recording")
                
        except self.client.exceptions.ClientError as e:
            error_code = e.response['Error']['Code']
            raise ValueError(f"S3 access error ({error_code}): {e}")

        self.client.download_file(
            Bucket=bucket,
            Key=key,
            Filename=local_path
        )

        # Verify downloaded file
        import os
        actual_size = os.path.getsize(local_path)
        print(f"Downloaded file size: {actual_size} bytes")

        return local_path

    def get_file_size(self, s3_uri: str) -> int:
        """Get file size in bytes without downloading."""
        bucket, key = self._parse_s3_uri(s3_uri)
        head = self.client.head_object(Bucket=bucket, Key=key)
        return head['ContentLength']

    def delete_file(self, s3_key: str):
        bucket, key = self._parse_s3_uri(s3_key) if s3_key.startswith("s3://") else (self.bucket_name, self._normalize_key(s3_key))

        self.client.delete_object(
            Bucket=bucket,
            Key=key
        )

    def get_file_url(self, s3_key: str):
        bucket, key = self._parse_s3_uri(s3_key) if s3_key.startswith("s3://") else (self.bucket_name, self._normalize_key(s3_key))

        return (
            f"https://{bucket}.s3."
            f"{AWS_REGION}.amazonaws.com/{key}"
        )

    def generate_presigned_url(self, s3_uri: str, expires_in: int = 300, response_content_disposition: str = None):
        bucket, key = self._parse_s3_uri(s3_uri)

        params = {"Bucket": bucket, "Key": key}
        if response_content_disposition:
            # Overrides the filename a browser suggests when the presigned
            # URL is downloaded directly, without touching the object itself.
            params["ResponseContentDisposition"] = response_content_disposition

        return self.client.generate_presigned_url(
            ClientMethod="get_object",
            Params=params,
            ExpiresIn=expires_in,
        )

    def get_object_stream(self, s3_uri: str):
        bucket, key = self._parse_s3_uri(s3_uri)
        response = self.client.get_object(Bucket=bucket, Key=key)
        body = response.get("Body")
        content_type = response.get("ContentType")
        if body is None:
            raise ValueError("S3 object body was empty")
        return body, content_type

    def list_files(self, bucket_name: str = None):
        bucket = bucket_name or self.bucket_name
        
        response = self.client.list_objects_v2(
            Bucket=bucket
        )

        for obj in response.get("Contents", []):
            print(obj["Key"])
