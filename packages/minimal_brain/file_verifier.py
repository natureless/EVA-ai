"""Bounded, read-only file-content checks. A match is not a code-quality claim."""

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PureWindowsPath
import stat
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FileExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")

    @field_validator("path")
    @classmethod
    def relative_path(cls, value):
        parts = value.split("/")
        if (
            any(c in value for c in '\\:*?"<>|\x00')
            or PureWindowsPath(value).is_absolute()
            or any(
                not p
                or p.startswith(".")
                or p.endswith((".", " "))
                or PureWindowsPath(p).is_reserved()
                for p in parts
            )
        ):
            raise ValueError(
                "file path must use non-hidden relative workspace components"
            )
        return value

    @field_validator("sha256")
    @classmethod
    def normalize_digest(cls, value):
        return value.lower()


class FileVerificationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["workspace_files_sha256_v1"]
    files: list[FileExpectation] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_paths(self):
        if len({f.path.casefold() for f in self.files}) != len(self.files):
            raise ValueError("duplicate file paths")
        return self


class WorkspaceFileVerifier:
    verifier_id = "workspace_files_sha256_v1"
    max_file_bytes = 2 * 1024 * 1024

    def __init__(self, workspace_root: Path):
        self.root = Path(workspace_root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("verification workspace must be a directory")
        self.workspace_hash = hashlib.sha256(
            os.path.normcase(str(self.root)).encode("utf-8")
        ).hexdigest()

    def capture_write(self, args: dict) -> dict | None:
        """Mirror FileExecutor's UTF-8 text write, storing only path and digest.

        Unsupported/outside/hidden paths retain receipts without a checker.
        This describes expected bytes, not authorization or an actual effect.
        """
        try:
            if not isinstance(args.get("path"), str) or not args["path"]:
                return None
            target = Path(args["path"]).resolve()
            relative = target.relative_to(self.root).as_posix()
            content = (
                str(args.get("content", "")).replace("\n", os.linesep).encode("utf-8")
            )
            if len(content) > self.max_file_bytes:
                return None
            return self.validate_spec(
                {
                    "kind": self.verifier_id,
                    "files": [
                        {
                            "path": relative,
                            "sha256": hashlib.sha256(content).hexdigest(),
                        }
                    ],
                }
            )
        except (ValueError, OSError, UnicodeError):
            return None

    def validate_spec(self, spec: dict) -> dict:
        return FileVerificationSpec.model_validate(spec).model_dump()

    @staticmethod
    def _linked(info):
        return stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )

    def _path(self, relative):
        current = self.root
        if self._linked(current.lstat()):
            raise ValueError("unsafe_path")
        for part in relative.split("/"):
            current = current / part
            info = current.lstat()
            if self._linked(info):
                raise ValueError("unsafe_path")
        if not current.resolve(strict=True).is_relative_to(self.root):
            raise ValueError("unsafe_path")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("not_regular_file")
        return current, info

    @staticmethod
    def _identity(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    def _digest(self, relative):
        target, before = self._path(relative)
        if before.st_size > self.max_file_bytes:
            raise ValueError("file_too_large")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_BINARY", 0)
        )
        fd = os.open(target, flags)
        try:
            opened = os.fstat(fd)
            if self._identity(before) != self._identity(opened) or not stat.S_ISREG(
                opened.st_mode
            ):
                raise ValueError("file_changed")
            digest, size = hashlib.sha256(), 0
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > self.max_file_bytes:
                    raise ValueError("file_too_large")
                digest.update(chunk)
            _, final = self._path(relative)
            if (
                self._identity(opened) != self._identity(os.fstat(fd))
                or self._identity(opened) != self._identity(final)
                or size != opened.st_size
            ):
                raise ValueError("file_changed")
            return digest.hexdigest(), size
        finally:
            os.close(fd)

    def run(self, spec: dict) -> dict:
        parsed = FileVerificationSpec.model_validate(spec)
        started_at = datetime.now(timezone.utc).isoformat()
        results = []
        for item in parsed.files:
            result = {
                "path": item.path,
                "expected_sha256": item.sha256,
                "observed_sha256": None,
            }
            try:
                observed, size = self._digest(item.path)
                result.update(
                    observed_sha256=observed,
                    size_bytes=size,
                    outcome="match" if observed == item.sha256 else "mismatch",
                )
            except (FileNotFoundError, NotADirectoryError):
                result.update(outcome="missing")
            except ValueError as exc:
                result.update(outcome="unknown", reason=str(exc))
            except OSError:
                result.update(outcome="unknown", reason="read_error")
            result["observed_at"] = datetime.now(timezone.utc).isoformat()
            results.append(result)
        unknown = any(item["outcome"] == "unknown" for item in results)
        matched = all(item["outcome"] == "match" for item in results)
        return {
            "verifier_id": self.verifier_id,
            "scope": "file_content_samples",
            "started_at": started_at,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "outcome": "unknown" if unknown else "passed" if matched else "mismatch",
            "checks": {} if unknown else {"files_match": matched},
            "files": results,
        }
