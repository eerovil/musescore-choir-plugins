import os

# Never hand a band to this host's remote reader from a test (#437): the tests load
# this host's .env, which names one, and load_dotenv does not override a variable
# already set.
os.environ["REMOTE_RUNNER"] = ""


# The graphics card's lock and the singer's song locks (#510) live under the home; a test
# must not take or wait for the host's real ones.
import tempfile as _tempfile
_locks = _tempfile.mkdtemp(prefix="choir-test-locks-")
os.environ["GPU_CARD_LOCK"] = os.path.join(_locks, "card.lock")
os.environ["SING_STATE_DIR"] = os.path.join(_locks, "sing")
