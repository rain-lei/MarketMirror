"""Process-lifetime exclusive workspace lease; released by the OS on exit."""
import os


class WorkspaceLease:
    def __init__(self, directory):
        self.stream = (directory / '.server.lock').open('a+b')
        try:
            self.stream.seek(0, 2)
            if self.stream.tell() == 0:
                self.stream.write(b'0')
                self.stream.flush()
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise RuntimeError('该实验目录已由另一个服务使用，请使用原服务或独立目录') from exc

    def close(self):
        if not self.stream.closed:
            self.stream.close()
