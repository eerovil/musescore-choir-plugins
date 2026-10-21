import os

# Never read a band on eero-pc from a test (#437): the tests load this host's .env,
# which names the cluster, and load_dotenv does not override a variable already set.
os.environ["HOMR_KUBE_CONTEXT"] = ""
