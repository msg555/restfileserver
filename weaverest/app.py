"""
Module for creating the weaverest tornado application object.
"""
import os

from tornado.web import Application

from .json_file_handler import JsonFileHandler


def make_app(
    serve_dir: str,
    *,
    max_size: int = 2 ** 20,
    encoding: str = "utf-8",
    debug: bool = False,
) -> Application:
    """
    Create the torando application (response handler).

    Arguments:
        serve_dir: The directory to serve files from.
        max_size: The maximum file size to serve data, larger file's data
                  will be inaccessible.
        encoding: The encoding to use to interpret file content.
        debug: Passes the debug flag to the tornado Application.
    """
    return Application(
        [
            (
                r"/(.*)",
                JsonFileHandler,
                {
                    "serve_dir": os.path.realpath(serve_dir),
                    "max_size": max_size,
                    "encoding": encoding,
                },
            )
        ],
        debug=debug,
    )
