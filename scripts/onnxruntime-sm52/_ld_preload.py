# Replaces onnxruntime/capi/_ld_preload.py in a venv given the GTX 970 build
# (scripts/install-homr.sh, eerovil/musescore-choir-plugins#418).
#
# onnxruntime's own copy looks for libcudart by bare name only, and when the
# loader cannot find it sets ORT_CUDA_UNAVAILABLE, which switches the CUDA
# provider off for the whole process without saying why. The CUDA runtime is
# installed as a pip wheel beside onnxruntime, so look there first.
import os
from ctypes import CDLL, RTLD_GLOBAL

_site = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_libcudart = None
for _cudart_lib in [os.path.join(_site, "nvidia", "cuda_runtime", "lib", "libcudart.so.12"),
                    "libcudart.so.12"]:
    try:
        _libcudart = CDLL(_cudart_lib, mode=RTLD_GLOBAL)
        break
    except OSError:
        pass
if _libcudart is None:
    os.environ["ORT_CUDA_UNAVAILABLE"] = "1"
