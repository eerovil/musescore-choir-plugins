import os

# Never hand a band to this host's remote reader from a test (#437): the tests load
# this host's .env, which names one, and load_dotenv does not override a variable
# already set.
os.environ["REMOTE_RUNNER"] = ""
