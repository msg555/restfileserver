#!/usr/bin/env python
"""
CLI entrypoint for the restfileserver http server.
"""
import argparse
import os
import sys
from typing import Any

from tornado.httpserver import HTTPServer
from tornado.ioloop import IOLoop

from .app import make_app


def parse_args() -> Any:
    """
    Parse the command line arguments!
    """
    parser = argparse.ArgumentParser(description="run JSON file REST server")
    parser.add_argument(
        "serve_dir",
        default=os.getcwd(),
        help="directory to server data from",
    )
    parser.add_argument(
        "--port",
        default=8000,
        type=int,
        required=False,
        help="port to bind server socket to",
    )
    parser.add_argument(
        "--address",
        default=None,
        required=False,
        help="address to bind server to. Defaults to all interfaces",
    )
    parser.add_argument(
        "--debug",
        default=False,
        const=True,
        action="store_const",
        required=False,
        help="run server in debug mode",
    )
    parser.add_argument(
        "--processes",
        default=1,
        required=False,
        help="number of worker processes, defaults to 1 (no forking)",
    )
    parser.add_argument(
        "--max-size",
        default=2 ** 20,
        required=False,
        help="maximum file size to serve data, defaults to 1MiB",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8",
        required=False,
        help="encoding to use to decode text files, defaults to utf-8",
    )
    return parser.parse_args()


def main() -> None:
    """
    CLI entrypoint function that starts the web server.
    """
    args = parse_args()
    args.serve_dir = os.path.realpath(args.serve_dir)
    if not os.path.isdir(args.serve_dir):
        sys.stderr.write(
            "Requested serve directory doesn't exist or isn't a directory\n"
        )
        sys.exit(1)

    # ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    # ssl_ctx.load_cert_chain(os.path.join(data_dir, "mydomain.crt"),
    #                        os.path.join(data_dir, "mydomain.key"))
    # HTTPServer(application, ssl_options=ssl_ctx)
    app = make_app(
        args.serve_dir,
        max_size=args.max_size,
        encoding=args.encoding,
        debug=args.debug,
    )
    os.umask(0)
    if args.debug:
        app.listen(port=args.port, address=args.address)
    else:
        server = HTTPServer(app)
        server.bind(port=args.port, address=args.address)
        server.start(num_processes=args.processes)
    IOLoop.current().start()


if __name__ == "__main__":
    main()