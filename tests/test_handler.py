# -*- coding: utf-8 -*-
"""
Test cases for the json file handler module.
"""

import cgi
from datetime import datetime
import json
import os
import stat
import tempfile
from typing import Any, Optional

from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application

from restfileserver.app import make_app


class FileHandlerTestBase(AsyncHTTPTestCase):
    """
    Base class defining helper methods used for the different test classes.
    """

    encoding = "utf-8"
    unicode_paths = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.uid = os.getuid()
        cls.gid = os.getgid()
        os.umask(0)

        with tempfile.TemporaryDirectory() as tempdir:
            try:
                # Test if file system is unicode based or byte based
                with open(
                    os.path.join(os.fsencode(tempdir.encode()), b"\xff"),
                    "w",
                    encoding=cls.encoding,
                ):
                    pass
            except OSError:
                cls.unicode_paths = True
        # cls.unicode_paths = True

    def setUp(self) -> None:
        # pylint: disable=consider-using-with
        self.tempdir = tempfile.TemporaryDirectory()
        super().setUp()

    def tearDown(self) -> None:
        super().tearDown()
        self.tempdir.cleanup()

    def get_app(self) -> Application:
        return make_app(
            self.tempdir.name,
            encoding=self.encoding,
        )

    def get_json(self, response) -> Any:
        """
        Verifies the content type and decodes the body into JSON data.
        """
        content_type, params = cgi.parse_header(response.headers.get("Content-Type"))
        self.assertEqual(content_type, "application/json")
        return json.loads(response.body.decode(params.get("charset", "utf-8")))

    def make_test(self, mode, testdata, path: Optional[bytes] = None) -> None:
        """
        Create a file hierarchy for testing.

        Arguments:
            mode: the access mode that the root file should be created with.
            testdata:
                - If a dictionary then the root file will be a directory
                  with children file names given by the keys of the dictionary
                  and the values being tuples of access modes and recursive
                  testdata structures that indicate how to create those sub files.
                - Otherwise should be a 'bytes' object giving the data to write
                  to the file.
            path: The path to write the file hiearchy. If `path` is None
                  it will use the tempdir path.
        """
        if isinstance(testdata, dict):
            if path is None:
                path = self.tempdir.name.encode(self.encoding)
                os.chmod(path, mode)
            else:
                os.mkdir(path, mode)

            path = path or self.tempdir.name
            for subfile, subfile_data in testdata.items():
                self.make_test(*subfile_data, path=os.path.join(path, subfile))
        else:
            assert path is not None
            assert isinstance(testdata, bytes)
            with open(os.open(path, os.O_CREAT | os.O_WRONLY, mode), "wb") as fout:
                fout.write(testdata)

    def check_get_result(self, actual: dict, expected: dict) -> None:
        """
        Check that the actual response from a 'get' request matches
        the values in 'expected'. This automatically pulls out and verifies
        ctime, mtime, uid, and gid. All other keys should be present and directly
        matched.
        """
        # Ensure ctime/mtime exist and parse in ISO format.
        datetime.fromisoformat(actual.pop("ctime"))
        datetime.fromisoformat(actual.pop("mtime"))

        # Make sure permissions are what we expect
        self.assertEqual(actual.pop("uid"), self.uid)
        self.assertEqual(actual.pop("gid"), self.gid)

        self.assertEqual(actual, expected)


class TestHandlerUtf8(FileHandlerTestBase):
    """
    Tests the JsonFileHandler methods using utf-8 fs encoding.
    """

    def test_get(self):
        """Test the `get` endpoint"""
        test_structure = {
            b"foo": (0o456, b"bl\xf0\x9f\x92\xa9a\xfeh"),
            b"sub dir": (
                0o723,
                {
                    b"bigfile": (0o400, b"a" * 300),
                },
            ),
            b"unreadable": (
                0o000,
                b"you'll never find me",
            ),
        }
        if not self.unicode_paths:
            test_structure[b"hi\xffhello"] = (
                0o723,
                {
                    b"\xff": (0o400, b"abc"),
                },
            )
            test_structure[b"\xf0\x9f\x92\xa9"] = (
                0o444,
                b"emoji!",
            )
        self.make_test(0o777, test_structure)

        # Note: Tornado doesn't allow surrogates in the request URL so there's no way
        # to access files that have invalid utf-8 encodings.
        root_children = sorted(
            name.decode(self.encoding, errors="surrogateescape")
            for name in test_structure
        )
        test_okay = {
            "/": {
                "mode": "777",
                "type": "directory",
                "children": root_children,
            },
            "/..": {
                "mode": "777",
                "type": "directory",
                "children": root_children,
            },
            "/fakedir/..": {
                "mode": "777",
                "type": "directory",
                "children": root_children,
            },
            "/foo": {
                "mode": "456",
                "type": "file",
                "data": "bl💩a\udcfeh",  # \xfe is replaced with \udcfe surrogate
                "size": 9,
            },
            "/foo/": {
                "mode": "456",
                "type": "file",
                "data": "bl💩a\udcfeh",
                "size": 9,
            },
            "/sub%20dir": {
                "mode": "723",
                "type": "directory",
                "children": ["bigfile"],
            },
            "/sub%20dir/": {
                "mode": "723",
                "type": "directory",
                "children": ["bigfile"],
            },
            "/sub%20dir/bigfile": {
                "mode": "400",
                "type": "file",
                "data": "a" * 300,
                "size": 300,
            },
        }
        if not self.unicode_paths:
            test_okay["/%F0%9F%92%A9"] = {
                "mode": "444",
                "type": "file",
                "data": "emoji!",
                "size": 6,
            }

        for path, expect_data in test_okay.items():
            response = self.fetch(path)
            self.assertEqual(response.code, 200)
            self.check_get_result(self.get_json(response), expect_data)

        test_fail = {
            "/bar": {
                "status": 404,
                "message": "file not found",
            },
            "/unreadable": {
                "status": 422,
                "message": "permission denied",
            },
        }
        for path, expect_data in test_fail.items():
            response = self.fetch(path)
            self.assertEqual(response.code, expect_data["status"])
            self.assertEqual(
                self.get_json(response),
                {"message": expect_data["message"]},
            )

    def test_post(self):
        """Test the `post` endpoint"""
        self.make_test(
            0o777,
            {
                b"foo": (0o656, b"hello"),
                b"oth": (0o600, b"hi"),
                b"sub dir": (0x777, {}),
            },
        )

        # Make a write to foo
        response = self.fetch(
            "/foo",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"data": "testwrite"}),
        )
        self.assertEqual(response.code, 200)
        self.assertEqual(self.get_json(response), {"message": "data written"})

        # Check the write was completed
        with open(os.path.join(self.tempdir.name, "foo"), "rb") as fin:
            self.assertEqual(fin.read(), b"hellotestwrite")

        # Test write to directory fails
        response = self.fetch(
            "/sub%20dir",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"data": "testwrite"}),
        )
        self.assertEqual(response.code, 422)
        self.assertEqual(
            self.get_json(response), {"message": "cannot append to directory"}
        )

        # Test write to non-existant file
        response = self.fetch(
            "/bar",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"data": "testwrite"}),
        )
        self.assertEqual(response.code, 404)
        self.assertEqual(self.get_json(response), {"message": "file not found"})

        # Test write unicode with surrogates
        response = self.fetch(
            "/foo",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"data": "surrogate=\udcff unicode=💩"}),
        )
        self.assertEqual(response.code, 200)
        self.assertEqual(self.get_json(response), {"message": "data written"})

        # Check the write was completed
        with open(os.path.join(self.tempdir.name, "foo"), "rb") as fin:
            self.assertEqual(
                fin.read(), b"hellotestwritesurrogate=\xff unicode=\xf0\x9f\x92\xa9"
            )

        # Test wrong content type
        response = self.fetch(
            "/foo",
            method="POST",
            headers={"Content-Type": "text/plain"},
            body=json.dumps({"data": "testwrite"}),
        )
        self.assertEqual(response.code, 400)
        self.assertEqual(self.get_json(response), {"message": "expecting JSON data"})

        # Test malformed json
        response = self.fetch(
            "/foo",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=b'{"data": "testwrite"',
        )
        self.assertEqual(response.code, 400)
        self.assertEqual(
            self.get_json(response), {"message": "could not parse JSON request body"}
        )

        # Test header parsing
        response = self.fetch(
            "/oth",
            method="POST",
            headers={
                "Content-Type": "application/json; charset=latin1; other=something"
            },
            body=b'{"data": "more\xffdata"}',
        )
        self.assertEqual(response.code, 200)
        self.assertEqual(self.get_json(response), {"message": "data written"})

        # Check the write was completed
        with open(os.path.join(self.tempdir.name, "oth"), "r", encoding="utf-8") as fin:
            self.assertEqual(fin.read(), "himoreÿdata")

        # Test invalid charset
        response = self.fetch(
            "/oth",
            method="POST",
            headers={
                "Content-Type": "application/json; charset=badcharset; other=something"
            },
            body=b'{"data": "moredata"}',
        )
        self.assertEqual(response.code, 400)
        self.assertEqual(
            self.get_json(response), {"message": "could not decode request body"}
        )

        # Test invalid schema
        response = self.fetch(
            "/oth",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"data": 123}).encode(),
        )
        self.assertEqual(response.code, 400)
        self.assertEqual(
            self.get_json(response), {"message": "data does not match expected schema"}
        )

        # Test invalid schema
        response = self.fetch(
            "/oth",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps({}).encode(),
        )
        self.assertEqual(response.code, 400)
        self.assertEqual(
            self.get_json(response), {"message": "data does not match expected schema"}
        )

    def test_put(self):
        """Test the `put` endpoint"""
        self.make_test(
            0o777,
            {
                b"foo": (0o656, b"hello"),
                b"oth": (0o600, b"hi"),
                b"sub dir": (0x777, {}),
            },
        )

        # Make a write to foo
        response = self.fetch(
            "/foo",
            method="PUT",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"data": "testwrite"}),
        )
        self.assertEqual(response.code, 200)
        self.assertEqual(self.get_json(response), {"message": "object updated"})

        # Check the write was completed
        with open(os.path.join(self.tempdir.name, "foo"), "rb") as fin:
            self.assertEqual(fin.read(), b"testwrite")

        # Test write to directory fails
        response = self.fetch(
            "/sub%20dir",
            method="PUT",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"data": "testwrite"}),
        )
        self.assertEqual(response.code, 422)
        self.assertEqual(
            self.get_json(response), {"message": "cannot write to directory"}
        )

        # Test write to non-existant file
        response = self.fetch(
            "/bar",
            method="PUT",
            headers={"Content-Type": "application/json"},
            body=json.dumps(
                {
                    "data": "testwrite",
                    "mode": "634",
                }
            ),
        )
        self.assertEqual(response.code, 200)
        self.assertEqual(self.get_json(response), {"message": "object updated"})

        # Check the write was completed
        with open(os.path.join(self.tempdir.name, "bar"), "rb") as fin:
            self.assertEqual(fin.read(), b"testwrite")

        # Check file mode was set
        st = os.stat(os.path.join(self.tempdir.name, "bar"))
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o634)

        # Test write unicode with surrogates
        response = self.fetch(
            "/foo",
            method="PUT",
            headers={"Content-Type": "application/json"},
            body=json.dumps(
                {
                    "data": "surrogate=\udcff unicode=💩",
                    "mode": "634",
                }
            ),
        )
        self.assertEqual(response.code, 200)
        self.assertEqual(self.get_json(response), {"message": "object updated"})

        # Check the write was completed
        with open(os.path.join(self.tempdir.name, "foo"), "rb") as fin:
            self.assertEqual(fin.read(), b"surrogate=\xff unicode=\xf0\x9f\x92\xa9")

        # Only file creation should update mode
        st = os.stat(os.path.join(self.tempdir.name, "foo"))
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o656)

        # Test write unicode with surrogates
        response = self.fetch(
            "/foo",
            method="PUT",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"data": "surrogate=\udcff unicode=💩"}),
        )
        self.assertEqual(response.code, 200)
        self.assertEqual(self.get_json(response), {"message": "object updated"})

        # Test invalid schema
        response = self.fetch(
            "/oth",
            method="PUT",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"data": 123}).encode(),
        )
        self.assertEqual(response.code, 400)
        self.assertEqual(
            self.get_json(response), {"message": "data does not match expected schema"}
        )

        # Test truncate
        response = self.fetch(
            "/oth",
            method="PUT",
            headers={"Content-Type": "application/json"},
            body=json.dumps({}).encode(),
        )
        self.assertEqual(response.code, 200)
        self.assertEqual(self.get_json(response), {"message": "object updated"})

        with open(os.path.join(self.tempdir.name, "oth"), "rb") as fin:
            self.assertEqual(fin.read(), b"")

        # Test create new dir
        response = self.fetch(
            "/newdir",
            method="PUT",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"directory": True}).encode(),
        )
        self.assertEqual(response.code, 200)
        self.assertEqual(self.get_json(response), {"message": "object updated"})
        self.assertTrue(os.path.isdir(os.path.join(self.tempdir.name, "newdir")))

        # Test create dir on existing file
        response = self.fetch(
            "/foo",
            method="PUT",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"directory": True}).encode(),
        )
        self.assertEqual(response.code, 422)
        self.assertEqual(self.get_json(response), {"message": "file already exists"})

        # Test create dir when parent does not exist
        response = self.fetch(
            "/fakedir/subdir",
            method="PUT",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"directory": True}).encode(),
        )
        self.assertEqual(response.code, 404)
        self.assertEqual(self.get_json(response), {"message": "file not found"})

    def test_delete(self):
        """Test the `delete` endpoint"""
        self.make_test(
            0o777,
            {
                b"subdir": (
                    0o777,
                    {
                        b"bar": (0o600, b"hi"),
                    },
                ),
            },
        )

        # Test deleting non-empty directory
        response = self.fetch("/subdir", method="DELETE")
        self.assertEqual(response.code, 422)
        self.assertEqual(self.get_json(response), {"message": "directory not empty"})

        # Test deleting file
        response = self.fetch("/subdir/bar", method="DELETE")
        self.assertEqual(response.code, 200)
        self.assertEqual(self.get_json(response), {"message": "file deleted"})
        self.assertFalse(os.path.exists(os.path.join(self.tempdir.name, "subdir/bar")))

        # Test deleting missing file
        response = self.fetch("/subdir/bar", method="DELETE")
        self.assertEqual(response.code, 404)
        self.assertEqual(self.get_json(response), {"message": "file not found"})

        # Test deleting directory
        response = self.fetch("/subdir", method="DELETE")
        self.assertEqual(response.code, 200)
        self.assertEqual(self.get_json(response), {"message": "file deleted"})
        self.assertFalse(os.path.exists(os.path.join(self.tempdir.name, "subdir")))

        # Test deleting root directory
        response = self.fetch("/", method="DELETE")
        self.assertEqual(response.code, 422)
        self.assertEqual(
            self.get_json(response), {"message": "refusing to delete root directory"}
        )
        self.assertTrue(os.path.exists(self.tempdir.name))


class TestHandlerLatin1(FileHandlerTestBase):
    """
    Tests that check behavior with the latin1 encoding. This is useful
    in order to test behavior when encoding fails.
    """

    encoding = "latin1"

    def test_get_latin1(self):
        """Test the `get` endpoint with latin1 encoding"""
        test_structure = {
            b"foo": (0o456, b"\xbfPor qu\xe8?"),
        }
        if not self.unicode_paths:
            test_structure[b"hi\xffhello\xf0"] = (
                0o723,
                {
                    b"\xff": (0o400, b"abc"),
                },
            )

        self.make_test(0o777, test_structure)

        test_okay = {
            "/": {
                "mode": "777",
                "type": "directory",
                "children": sorted(
                    name.decode(self.encoding, errors="surrogateescape")
                    for name in test_structure
                ),
            },
            "/foo": {
                "mode": "456",
                "type": "file",
                "data": "¿Por què?",
                "size": 9,
            },
            "/foo/": {
                "mode": "456",
                "type": "file",
                "data": "¿Por què?",
                "size": 9,
            },
        }

        for path, expect_data in test_okay.items():
            response = self.fetch(path)
            self.assertEqual(response.code, 200)
            self.check_get_result(self.get_json(response), expect_data)

        test_fail = {
            "/bar": {
                "status": 404,
                "message": "file not found",
            },
            "/%F0%9F%92%A9": {  # This emoji doesn't exist in latin1
                "status": 400,
                "message": "cannot encode character in path",
            },
        }
        for path, expect_data in test_fail.items():
            response = self.fetch(path)
            self.assertEqual(response.code, expect_data["status"])
            self.assertEqual(
                self.get_json(response),
                {"message": expect_data["message"]},
            )

    def test_post_latin1(self):
        """Test the `get` endpoint with latin1 encoding"""
        self.make_test(
            0o777,
            {
                b"foo": (0o656, b"hello"),
            },
        )

        # Check error handling when cannot encode data
        response = self.fetch(
            "/foo",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"data": "asdf💩"}).encode(),
        )
        self.assertEqual(response.code, 400)
        self.assertEqual(self.get_json(response), {"message": "cannot encode 'data'"})

    def test_put_latin1(self):
        """Test the `get` endpoint with latin1 encoding"""
        self.make_test(0o777, {})

        # Check error handling when cannot encode data
        response = self.fetch(
            "/foo",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"data": "asdf💩"}).encode(),
        )
        self.assertEqual(response.code, 400)
        self.assertEqual(self.get_json(response), {"message": "cannot encode 'data'"})
