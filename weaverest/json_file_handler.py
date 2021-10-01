"""
Module defining the JSON/REST file handler.
"""
import contextlib
from datetime import datetime
import functools
import os
import stat

from tornado.web import RequestHandler

assert os.sep == "/", "expecting '/' directory separator (e.g. POSIX environment)"


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
        except ErrorResponse as exc:
            self.set_status(exc.status)
            self.write({"message": exc.message})
        except Exception:
            self.set_status(500)
            self.write({"message": "internal server error"})
            raise

    return invoke


@contextlib.contextmanager
def posix_open(path: str, flags: int):
    """
    Contest manager wrapper around os.open.
    """
    fd = os.open(path, flags)
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

    def get_full_path(self, obj_path: str) -> str:
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
        return os.path.join(self.serve_dir, obj_path[1:])

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
        contain the file contents. If either the file was too long or the
        contents could not be decoded "data" will be null and a "message"
        field will be included with a description of why "data" is null.
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
                    try:
                        result["data"] = data.decode(self.encoding)
                    except UnicodeDecodeError:
                        result["data"] = None
                        result["message"] = "could not decode file data"
            elif stat.S_ISDIR(file_stat.st_mode):
                result["children"] = os.listdir(fd)

        self.set_status(200)
        self.write(result)

    @error_handler
    def post(self, obj_path: str) -> None:
        """
        TODO
        """
        full_path = self.get_full_path(obj_path)
        with posix_open(full_path, os.O_WRONLY | os.O_APPEND) as fd:
            os.write(fd, self.request.body)
        self.set_status(200)
        self.write({"message": "data written"})

    @error_handler
    def put(self, obj_path: str) -> None:
        """
        TODO
        """
        full_path = self.get_full_path(obj_path)
        with posix_open(full_path, os.O_WRONLY | os.O_CREAT) as fd:
            os.write(fd, self.request.body)
        self.set_status(200)
        self.write({"message": "data written"})

    @error_handler
    def delete(self, obj_path: str) -> None:
        """
        Deletes the requested file. If the file refers to a non-empty directory
        a 400 will be returned.
        """
        full_path = self.get_full_path(obj_path)
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
