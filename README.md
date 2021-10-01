# JSON File Server

Demonstration Toranado-based JSON+REST file server. This serves a directory
on the host file system allowing reads (through `get`),
writes (through `post` and `put`), and deletions (through `delete`).

File paths and content are interpretted using a configurable encoding that
defaults to "utf-8". [surrogateescape](https://www.python.org/dev/peps/pep-0383/)
error encoding is used to add support for non-utf-8 byte seqeuence (although
there are still limitations as non-utf-8 paths cannot be queried).

## Running

The webserver can be run directly using the below commands

```
pip install -r requirements.txt
python -m restfileserver -v directory/to/serve
```

Alternatively the webserver can be built into a docker container and run
using these commands

```
docker build -t restfileserver .
docker run -d -p "8000:8000" -v "directory/to/serve:/serve" \
    --name restfileserver restfileserver -v /serve

# Check logs
docker logs -f restfileserver

# And later stop the server
docker rm -f restfileserver
```

Finally, you can also use `docker-compose` to start the webserver. This
configuration will use a persistent volume managed by docker (initialized
to an empty directory).

```
BIND_PORT=8000 \
SERVE_DIRECTORY=directory/to/serve \
docker-compose up --build -d

# Later stop the service
docker-compose down
```

## Endpoints

The request URL is interpreted as the file path to the object being accessed.

For example a query to "/foo/bar" will access the file "bar" within the folder
"foo" underneath the directory being served.

### Error responses

Error responses (non-200 status code) will always be a JSON document with
at least a "message" key giving a description of the error.

```
HTTP/1.1 404 Not Found
Content-Type: application/json; charset=UTF-8

{"message": "file not found"}
```

### GET Method

Access metadata and contents of a file

Request format

```
GET /foo/var HTTP/1.1
```

File response format

```
{
  "mode": "775",
  "uid": 1000,
  "gid": 1000,
  "mtime": "2021-10-01T17:38:41",
  "ctime": "2021-10-01T17:38:41",
  "type": "file",
  "size": 21,
  "data": "... file contents ..."
}
```

Directory response format

```
{
  "mode": "775",
  "uid": 1000,
  "gid": 1000,
  "mtime": "2021-10-01T22:52:53",
  "ctime": "2021-10-01T22:52:53",
  "type": "directory",
  "children": [
    "child1",
    "child2"
  ]
}
```

The below fields are set for every object
- `mode` gives the standard octal representation of the object's access mode
- `uid` and `gid` give the owner user and gruop id respectively
- `mtime` and `ctime` give the modified time and creation time in seconds in ISO 8601 format
- `type` is one of {`directory`, `file`, `character device`, `block device`, `named pipe`, `socket file`, `symbolic link`} although only directory and regular file objects are really supported.

For "file" type objects the following fields are also set
- `size` is the size of the file in bytes
- `data` is the decoded file contents

For "directory" type object there will be a `children` field set which is a
list of files (not including "." or "..") within the directory.

### POST Method

Append data to a file. Only valid on regular files.

Request format

```
POST /foo/var HTTP/1.1
Content-Type: application/json

{"data": "data to append"}
```

Response format

```
{"message": "data written"}
```

### PUT Method

Create or truncate a file/directory.

Request format

```
PUT /foo/var HTTP/1.1
Content-Type: application/json

{
  "directory": false,
  "mode": "664",
  "data": "file data"
}
```

Response format

```
{"message": "object update"}
```

The request JSON document may contain three optional keys
- `directory` (bool): Set to true to create a directory at the given path.
    This will ignore the `data` key. If the file already exists this will
    generate a 422 HTTP response.
- `mode`: The access mode to create the file or directory with. If writing to
    an existing file this field will be ignored. This defaults to "775"
    for directories and "664" for files.
- `data`: Data to write when creating/updating a file. Existing data will be
    truncated.
    
### DELETE Method

Delete a file/directory. Deleting a directory requires the directory to be
empty. The server will refuse to delete the root directory even if it is empty.

Request format

```
DELETE /foo/var HTTP/1.1
```

Response format

```
{"message": "file deleted"}
```
    
