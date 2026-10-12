"""Atomic checkpoints and exclusive ownership of a conversion job."""
import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path

class UserError(Exception): pass
class Paused(Exception): pass

def sha256_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError): os.unlink(temporary)

def atomic_json(path, data):
    atomic_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2)+'\n').encode('utf-8'))

@contextlib.contextmanager
def job_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/'job.lock').open('a+b') as stream:
        try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise UserError('이 작업은 다른 창에서 처리 중입니다.')
        try: yield
        finally: fcntl.flock(stream, fcntl.LOCK_UN)

def publish_file(temporary, destination, overwrite=False):
    """Same-directory atomic publication; no-clobber unless explicitly authorized."""
    temporary, destination = Path(temporary), Path(destination)
    if overwrite:
        os.replace(temporary, destination)
    else:
        try: os.link(temporary, destination)
        except FileExistsError: raise UserError('저장 위치에 같은 이름의 파일이 있습니다. 다른 이름을 선택하세요.')
        temporary.unlink()
