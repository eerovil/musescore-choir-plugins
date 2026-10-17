"""What onnxruntime 1.29.0's source needs before it builds and loads for compute 5.2.

Run by build.sh on the cloned source; every change is idempotent. Each one was
found by building and is explained where it is made (issue #418).
"""

import pathlib
import re
import sys

src = pathlib.Path(sys.argv[1])


def replace_once(path: pathlib.Path, old: str, new: str, done: str) -> None:
    text = path.read_text()
    if done in text:
        return
    if old not in text:
        sys.exit(f"{path}: the text to patch is not there; onnxruntime moved?")
    path.write_text(text.replace(old, new, 1))


# 1. Half-precision arithmetic in PTX exists from compute 5.3, so ptxas refuses
#    this kernel's inline mul.rn.f16/add.rn.f16 for 5.2. Round through float
#    there instead; newer cards keep the original instructions.
replace_once(
    src / "onnxruntime/contrib_ops/cuda/bert/gated_add_impl.cu",
    """__device__ __forceinline__ half RoundedMulAdd(half x, half y, half gate) {
  unsigned short product;
  unsigned short output;
  asm volatile("mul.rn.f16 %0, %1, %2;"
               : "=h"(product)
               : "h"(__half_as_ushort(y)), "h"(__half_as_ushort(gate)));
  asm volatile("add.rn.f16 %0, %1, %2;"
               : "=h"(output)
               : "h"(__half_as_ushort(x)), "h"(product));
  return __ushort_as_half(output);
}""",
    """__device__ __forceinline__ half RoundedMulAdd(half x, half y, half gate) {
#if defined(__CUDA_ARCH__) && __CUDA_ARCH__ < 530
  const half p = __float2half_rn(__half2float(y) * __half2float(gate));
  return __float2half_rn(__half2float(x) + __half2float(p));
#else
  unsigned short product;
  unsigned short output;
  asm volatile("mul.rn.f16 %0, %1, %2;"
               : "=h"(product)
               : "h"(__half_as_ushort(y)), "h"(__half_as_ushort(gate)));
  asm volatile("add.rn.f16 %0, %1, %2;"
               : "=h"(output)
               : "h"(__half_as_ushort(x)), "h"(product));
  return __ushort_as_half(output);
#endif
}""",
    "__CUDA_ARCH__ < 530",
)

# 2. With memory-efficient attention off (it needs newer cards), the header
#    defining this constant is left out, yet attention.cc still names it.
#    Its value is -1.0e+30f.
attention = src / "onnxruntime/core/providers/cuda/llm/attention.cc"
attention.write_text(attention.read_text().replace(
    "::onnxruntime::contrib::cuda::kCutlassSafeMaskFilterValue", "-1.0e+30f"))

# 3. The LLM kernels (cutlass MoE, fpA_intB GEMM) are only built for compute
#    7.5 and up, but the MoE, QMoE and MatMulNBits ops that call them are
#    still compiled and registered, so the CUDA provider comes out with
#    undefined symbols and does not load. homr uses none of them.
kernels = src / "onnxruntime/contrib_ops/cuda/cuda_contrib_kernels.cc"
lines = kernels.read_text().splitlines(keepends=True)
drop = re.compile(r"CUDA_MS_OP_TYPED_CLASS_NAME\(1, \w+, (MoE|QMoE|MatMulNBits)\)")
kept = [line for line in lines if not drop.search(line)]
if len(kept) != len(lines):
    kernels.write_text("".join(kept))
anchor = ("    list(APPEND onnxruntime_providers_cuda_src ${onnxruntime_cuda_contrib_ops_cc_srcs}"
          " ${onnxruntime_cuda_contrib_ops_cu_srcs})\n")
exclude = ('    list(FILTER onnxruntime_providers_cuda_src EXCLUDE REGEX '
           '"contrib_ops/cuda/moe/|contrib_ops/cuda/quantization/matmul_nbits\\\\.cc$")\n')
replace_once(src / "cmake/onnxruntime_providers_cuda.cmake", anchor, anchor + exclude, exclude)

# 4. The provider still asks MatMulNBits for a workspace estimate, defined in
#    the matmul_nbits.cc taken out above. "No estimate" is an answer the header
#    documents, and with no MatMulNBits kernel registered it is the true one.
(src / "onnxruntime/contrib_ops/cuda/sm52_matmul_nbits_stub.cc").write_text("""\
// Written by the GTX 970 build (eerovil/musescore-choir-plugins#418).
#include "contrib_ops/cuda/quantization/matmul_nbits.h"

#if USE_FPA_INTB_GEMM
namespace onnxruntime {
namespace contrib {
namespace cuda {

std::optional<size_t> EstimateMatMulNBitsWorkspace(const Node&, const cudaDeviceProp&) {
  return std::nullopt;
}

std::optional<size_t> EstimateMatMulNBitsWorkspace(
    const Node&, gsl::span<const int64_t>, const cudaDeviceProp&) {
  return std::nullopt;
}

}  // namespace cuda
}  // namespace contrib
}  // namespace onnxruntime
#endif
""")

print("onnxruntime source patched for compute 5.2")
