"""
Module defining the JSON/REST file handler.
"""
import cgi
import contextlib
from datetime import datetime
import functools
import json
import logging
import os
import stat
from typing import Any

import jsonschema  # type: ignore
from tornado.web import RequestHandler

assert os.sep == "/", "expecting '/' directory separator (e.g. POSIX environment)"

LOGGER = logging.getLogger(__name__)


class ErrorResponse(Exception):
    """
    Exception raised that will be handled by error_handler to generate
    a response.
    """

    def __init__(self, message: str, *, status: int = 500) -> None:
        super().__init__()
        self.message = message
        self.status = status


def error_handler(func):
    """
    Decorator around handler methods that will write an appropriate response
    based on uncaught exceptions.
    """

    @functools.wraps(func)
    def invoke(self, *args, **kwargs):
        try:
            func(self, *args, **kwargs)
        except FileNotFoundError:
            self.set_status(404)
            self.write({"message": "file not found"})
        except PermissionError:
            self.set_status(422)
            self.write({"message": "permission denied"})
        except ErrorResponse as exc:
            self.set_status(exc.status)
            self.write({"message": exc.message})
        except Exception:  # pylint: disable=broad-except
            LOGGER.exception("unexpected exception")
            self.set_status(500)
            self.write({"message": "internal server error"})

    return invoke


@contextlib.contextmanager
def posix_open(path: bytes, flags: int, *, mode=0o777):
    """
    Contest manager wrapper around os.open.
    """
    fd = os.open(path, flags, mode=mode)
    try:
        yield fd
    finally:
        os.close(fd)


# pylint gets confused about tornado's RequetHandler
# pylint: disable=abstract-method
class JsonFileHandler(RequestHandler):
    """
    The tornado handler definition for accessing files.
    """

    # We really only support directories and files (and symlinks to those objects).
    # Other objects could cause the current implementation to block unexpectedly.
    FILE_TYPES = {
        stat.S_IFDIR: "directory",
        stat.S_IFCHR: "character device",
        stat.S_IFBLK: "block device",
        stat.S_IFREG: "file",
        stat.S_IFIFO: "named pipe",
        stat.S_IFLNK: "symbolic link",  # We should be resolving symbolic links
        stat.S_IFSOCK: "socket file",
    }

    def initialize(self, *, serve_dir: str, max_size: int, encoding: str) -> None:
        """
        Sets keyword arguments configured by the Application.
        """
        self.serve_dir = serve_dir
        self.max_size = max_size
        self.encoding = encoding

    def fs_encode(self, data: str) -> bytes:
        """
        Encode `data` with the configured encoding, allowing surrogate characters
        to be embedded into the produced bytes.
        """
        return data.encode(self.encoding, errors="surrogateescape")

    def fs_decode(self, data: bytes) -> str:
        """
        Decode `data` with the configured encoding, using surrogate characters
        to replace decodable bytes.
        """
        return data.decode(self.encoding, errors="surrogateescape")

    def get_full_path(self, obj_path: str) -> bytes:
        """
        Return the normed absolute path of the object. This collapses "."
        and ".." before joining with the serving directory to ensure only
        paths within the serving directory are accessible.
        """
        # Join with root direcotory, this prevents superfluous back directory
        # (i.e. "..") links from seeing higher in the directory tree than we
        # want. Doing it this way also makes sure if their path starts with
        # "/" they don't access an absolute file path.
        obj_path = os.path.normpath(os.path.join("/", obj_path))
        assert obj_path[0] == "/"
        try:
            if obj_path == "/":
                return self.fs_encode(self.serve_dir)
            return self.fs_encode(os.path.join(self.serve_dir, obj_path[1:]))
        except UnicodeEncodeError as exc:
            raise ErrorResponse(
                "cannot encode character in path",
                status=400,
            ) from exc

    def get_json_data(self, schema: Any) -> Any:
        """
        Get the JSON payload from the request body. Ensures that the content
        type is application/json while also handling request decoding and
        schema validation.
        """
        content_type, params = cgi.parse_header(
            self.request.headers.get("Content-Type", "none")
        )
        if content_type != "application/json":
            raise ErrorResponse(
                "expecting JSON data",
                status=400,
            )

        encoding = params.get("charset", "utf-8").strip("'\"")
        try:
            content = self.request.body.decode(encoding)
        except (UnicodeDecodeError, LookupError) as exc:
            raise ErrorResponse(
                "could not decode request body",
                status=400,
            ) from exc

        try:
            json_data = json.loads(content)
        except json.decoder.JSONDecodeError as exc:
            raise ErrorResponse(
                "could not parse JSON request body",
                status=400,
            ) from exc

        try:
            jsonschema.validate(json_data, schema)
        except jsonschema.exceptions.ValidationError as exc:
            raise ErrorResponse(
                "data does not match expected schema",
                status=400,
            ) from exc

        return json_data

    @error_handler
    def get(self, obj_path: str) -> None:
        """
        Writes a JSON response related to the requested file, following
        any symlinks.

        Fields include:
            mode: The permission bits of the file as an octal string
            uid: The file user owner ID number
            gid: The file group owner ID number
            mtime: The file modification time in ISO-8601 format with second resolution.
            ctime: The file creation time in same format
            type: A textual description of the object type, e.g. "directory" or "file"

        For directories there will be an additional "children" field which will
        be a list of child path names (not including "." or "..").

        For files there will be an additional "data" field that will normal
        contain the file contents. If the file was too longer "data" will be
        null and there will be a "message" field indicating this. Data will be
        decoded using the default application encoding using the surrogate escape
        error handler.
        """
        full_path = self.get_full_path(obj_path)

        # Make sure to close file descriptor
        with posix_open(full_path, os.O_RDONLY) as fd:
            file_stat = os.fstat(fd)
            mtime = datetime.utcfromtimestamp(file_stat.st_mtime).isoformat(
                timespec="seconds"
            )
            ctime = datetime.utcfromtimestamp(file_stat.st_ctime).isoformat(
                timespec="seconds"
            )
            result = {
                "mode": f"{stat.S_IMODE(file_stat.st_mode):03o}",
                "uid": file_stat.st_uid,
                "gid": file_stat.st_gid,
                "mtime": mtime,
                "ctime": ctime,
                "type": self.FILE_TYPES.get(stat.S_IFMT(file_stat.st_mode), "unknown"),
            }

            if stat.S_ISREG(file_stat.st_mode):
                if file_stat.st_size > self.max_size:
                    result["data"] = None
                    result["message"] = "file too long"
                else:
                    data = os.read(fd, file_stat.st_size)
                    result["data"] = self.fs_decode(data)
            elif stat.S_ISDIR(file_stat.st_mode):
                # Undo the default fs-encoding and apply the configured one.
                # Unfortunately it seems python always returns str objects when
                # listing a file descriptor.
                result["children"] = sorted(
                    self.fs_decode(os.fsencode(path)) for path in os.listdir(fd)
                )

        self.set_status(200)
        self.write(result)

    POST_SCHEMA = {
        "type": "object",
        "properties": {
            "data": {
                "type": "string",
            }
        },
        "required": [
            "data",
        ],
    }

    @error_handler
    def post(self, obj_path: str) -> None:
        """
        Append data to a file. This endpoint can only operate on files.

        Expects a JSON object with a single key, "data".
        """
        data = self.get_json_data(self.POST_SCHEMA)
        try:
            write_data = self.fs_encode(data["data"])
        except UnicodeEncodeError as exc:
            raise ErrorResponse(
                "cannot encode 'data'",
                status=400,
            ) from exc

        full_path = self.get_full_path(obj_path)
        try:
            with posix_open(full_path, os.O_WRONLY | os.O_APPEND) as fd:
                os.write(fd, write_data)
        except IsADirectoryError as exc:
            raise ErrorResponse(
                "cannot append to directory",
                status=400,
            ) from exc

        self.set_status(200)
        self.write({"message": "data written"})

    PUT_SCHEMA = {
        "type": "object",
        "properties": {
            "directory": {
                "type": "boolean",
            },
            "mode": {
                "type": "string",
            },
            "data": {
                "type": "string",
            },
        },
    }

    @error_handler
    def put(self, obj_path: str) -> None:
        """
        Create or update a file or directory.

        Expects a JSON object with a the below keys:
        - "directory" (bool): Set to true if creating a directory, defaults to false.
        - "mode" (string): Creation mode flags in octal format. These set the created
                           objects mode and are ignored if the file already exists.
                           This defaults to '775' for directories and '664' for files.
        - "data" (string): Data to write to the file, replacing its existing contents.
                           This will be ignored if operating on a directory.
        """
        json_data = self.get_json_data(self.PUT_SCHEMA)

        directory = json_data.get("directory", False)
        str_mode = json_data.get("mode", "775" if directory else "664")
        data = json_data.get("data", "")
        mode = stat.S_IMODE(int(str_mode, 8))

        full_path = self.get_full_path(obj_path)
        if directory:
            try:
                os.mkdir(full_path, mode=mode)
            except FileExistsError as exc:
                raise ErrorResponse(
                    "file already exists",
                    status=400,
                ) from exc
        else:
            try:
                write_data = self.fs_encode(data)
            except UnicodeEncodeError as exc:
                raise ErrorResponse(
                    "cannot encode 'data'",
                    status=400,
                ) from exc

            try:
                with posix_open(
                    full_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode=mode
                ) as fd:
                    os.write(fd, write_data)
            except IsADirectoryError as exc:
                raise ErrorResponse(
                    "cannot write to directory",
                    status=400,
                ) from exc

        self.set_status(200)
        self.write({"message": "object updated"})

    @error_handler
    def delete(self, obj_path: str) -> None:
        """
        Deletes the requested file. If the file refers to a non-empty directory
        a 400 will be returned.
        """
        full_path = self.get_full_path(obj_path)
        if full_path == self.fs_encode(self.serve_dir):
            raise ErrorResponse(
                "refusing to delete root directory",
                status=400,
            )
        try:
            os.unlink(full_path)
        except IsADirectoryError:
            try:
                os.rmdir(full_path)
            except OSError as exc:
                raise ErrorResponse(
                    "directory not empty",
                    status=400,
                ) from exc

        self.set_status(200)
        self.write({"message": "file deleted"})
