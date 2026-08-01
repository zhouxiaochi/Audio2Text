import re
import shutil
from abc import ABC, abstractmethod
from pathlib import Path


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")
_ARTIFACT_FORMATS = {"json", "md", "docx"}


def _validate_identifier(value: str, name: str) -> str:
    if not value or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid {name}")
    return value


def job_prefix(user_id: str, job_id: str) -> str:
    """Return the only prefix under which a user's job may store objects."""
    return (
        f"v1/users/{_validate_identifier(user_id, 'user_id')}/jobs/"
        f"{_validate_identifier(job_id, 'job_id')}/"
    )


def source_object_key(user_id: str, job_id: str, extension: str) -> str:
    extension = extension.lower()
    if not re.fullmatch(r"\.[a-z0-9]+", extension):
        raise ValueError("invalid source extension")
    return f"{job_prefix(user_id, job_id)}source/original{extension}"


def artifact_object_key(user_id: str, job_id: str, format: str) -> str:
    if format not in _ARTIFACT_FORMATS:
        raise ValueError("invalid artifact format")
    return f"{job_prefix(user_id, job_id)}artifacts/transcript.{format}"


def validate_job_key(key: str, user_id: str, job_id: str) -> str:
    prefix = job_prefix(user_id, job_id)
    if not key.startswith(prefix) or ".." in key or "\\" in key:
        raise ValueError("object key is outside the job prefix")
    allowed = {
        artifact_object_key(user_id, job_id, format) for format in _ARTIFACT_FORMATS
    }
    source_pattern = re.compile(rf"^{re.escape(prefix)}source/original\.[a-z0-9]+$")
    if key not in allowed and not source_pattern.fullmatch(key):
        raise ValueError("object key is not a supported job object")
    return key


class Storage(ABC):
    """Private object storage used for source files and final job artifacts."""

    persistent = False
    name = "local"

    @abstractmethod
    def put_file(self, key: str, source: Path, user_id: str, job_id: str) -> None: ...

    @abstractmethod
    def put_bytes(self, key: str, data: bytes, user_id: str, job_id: str) -> None: ...

    @abstractmethod
    def get_bytes(self, key: str, user_id: str, job_id: str) -> bytes: ...

    @abstractmethod
    def download_file(self, key: str, destination: Path, user_id: str, job_id: str) -> None: ...

    @abstractmethod
    def exists(self, key: str, user_id: str, job_id: str) -> bool: ...

    @abstractmethod
    def delete_prefix(self, user_id: str, job_id: str) -> None: ...


class LocalStorage(Storage):
    """Filesystem implementation for local development and tests."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str, user_id: str, job_id: str) -> Path:
        validate_job_key(key, user_id, job_id)
        path = (self.root / Path(*key.split("/"))).resolve()
        if self.root not in path.parents:
            raise ValueError("object key escapes storage root")
        return path

    def put_file(self, key: str, source: Path, user_id: str, job_id: str) -> None:
        destination = self._path(key, user_id, job_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    def put_bytes(self, key: str, data: bytes, user_id: str, job_id: str) -> None:
        destination = self._path(key, user_id, job_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    def get_bytes(self, key: str, user_id: str, job_id: str) -> bytes:
        return self._path(key, user_id, job_id).read_bytes()

    def download_file(self, key: str, destination: Path, user_id: str, job_id: str) -> None:
        source = self._path(key, user_id, job_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    def exists(self, key: str, user_id: str, job_id: str) -> bool:
        return self._path(key, user_id, job_id).is_file()

    def delete_prefix(self, user_id: str, job_id: str) -> None:
        prefix = job_prefix(user_id, job_id)
        shutil.rmtree(self.root / Path(*prefix.rstrip("/").split("/")), ignore_errors=True)


class SpacesStorage(Storage):
    """S3-compatible private storage for DigitalOcean Spaces."""

    persistent = True
    name = "spaces"

    def __init__(
        self,
        endpoint: str,
        region: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
    ):
        import boto3

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    def put_file(self, key: str, source: Path, user_id: str, job_id: str) -> None:
        validate_job_key(key, user_id, job_id)
        self.client.upload_file(str(source), self.bucket, key)

    def put_bytes(self, key: str, data: bytes, user_id: str, job_id: str) -> None:
        validate_job_key(key, user_id, job_id)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get_bytes(self, key: str, user_id: str, job_id: str) -> bytes:
        validate_job_key(key, user_id, job_id)
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def download_file(self, key: str, destination: Path, user_id: str, job_id: str) -> None:
        validate_job_key(key, user_id, job_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(destination))

    def exists(self, key: str, user_id: str, job_id: str) -> bool:
        validate_job_key(key, user_id, job_id)
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def delete_prefix(self, user_id: str, job_id: str) -> None:
        prefix = job_prefix(user_id, job_id)
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if objects:
                response = self.client.delete_objects(
                    Bucket=self.bucket, Delete={"Objects": objects}
                )
                errors = response.get("Errors", [])
                if errors:
                    raise RuntimeError(
                        f"failed to delete {len(errors)} object(s) from job prefix"
                    )
