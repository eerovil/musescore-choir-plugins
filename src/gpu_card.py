"""Who has the graphics card: a singer or a homr read, one at a time (#510).

The card has 6 GB and a singer's models and homr's both want most of it, so two at once end in
an out-of-memory error halfway through a song. One `flock` on a file decides:

- a singer (`sing_song.py`) holds it while it sings, and waits for it (`hold(wait=True)`),
  saying so in its log; on release it writes the time into the file, and the next singer
  sleeps until PAUSE seconds after that time (a continuous run crashed the old GTX 970);
- a homr read never waits inside its heavy slot: `try_hold()` takes the card if it is free and
  otherwise the read goes to the processor; `wait_free()` is for a read that would rather wait
  for the card than read on the processor, outside its heavy slot.

The kernel drops a `flock` when its holder dies, so a killed singer never strands the card.
Stdlib only: the app and the singer both import it.
"""
import contextlib
import fcntl
import os
import pwd
import time

PAUSE = 60   # seconds between one singer letting go of the card and the next starting


def lock_path():
    """~/.local/state/musescore-choir-plugins/card.lock, the home read from the password file
    (not $HOME), like the song locks: the app's service and a shell must find the same file."""
    path = os.environ.get("GPU_CARD_LOCK")
    if path:
        return path
    home = pwd.getpwuid(os.getuid()).pw_dir
    return os.path.join(home, ".local", "state", "musescore-choir-plugins", "card.lock")


def _open():
    path = lock_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o644)


def _released_at(fd):
    """The time the last singer let go, or None."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        return float(os.read(fd, 64).decode().strip() or "nan") or None
    except (OSError, ValueError):
        return None


def _stamp(fd):
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{time.time():.3f}\n".encode())
    except OSError:
        pass


@contextlib.contextmanager
def hold(log=print, wait=True, pause=True, on_wait=None, sleep=time.sleep):
    """Hold the card for the `with` block. With wait (a singer), wait for it, telling `log`
    'waiting for the graphics card' once and 'got the graphics card' when it comes, and calling
    on_wait() before waiting; with pause, sleep until PAUSE seconds after the last singer let go,
    and write the time on release. Without wait, yield False at once when it is busy (the block
    then runs without it) and True when it is held."""
    fd = _open()
    held = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = True
        except BlockingIOError:
            if not wait:
                yield False
                return
            if on_wait:
                on_wait()
            log("waiting for the graphics card")
            fcntl.flock(fd, fcntl.LOCK_EX)
            held = True
            log("got the graphics card")
        if pause:
            last = _released_at(fd)
            if last is not None:
                rest = last + PAUSE - time.time()
                if 0 < rest <= PAUSE:
                    log(f"letting the graphics card rest {rest:.0f} s after the last singer")
                    sleep(rest)
        yield True
    finally:
        if held and pause:
            _stamp(fd)
        if held:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def try_hold():
    """A homr read's hold: `with try_hold() as got:` -- got is False when a singer has the card."""
    return hold(wait=False, pause=False)


def busy():
    """True when somebody holds the card now."""
    fd = _open()
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)   # closing drops a lock taken here
    return False


def wait_free(log=print):
    """Wait until nobody holds the card, without keeping it (the caller takes it later with
    try_hold, which may still find it taken). Says so in `log` when it has to wait."""
    fd = _open()
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            log("waiting for the graphics card: it is singing")
            fcntl.flock(fd, fcntl.LOCK_SH)
    finally:
        os.close(fd)
