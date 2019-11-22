#include "fast_transformers/layers/kernels/softmax.h"
#include <immintrin.h>
#include <cmath>

namespace fast_transformers {
namespace layers {
namespace kernels {
static constexpr float g_epsilon = 1e-6f;

void SoftmaxMask(float* qk_buf, const float* attr_mask,
                 const int64_t batch_size, const int64_t head_num,
                 const int64_t seq_len, float scaler) {
  int64_t M = batch_size * head_num * seq_len;
  int64_t N = seq_len;
#pragma omp parallel for
  for (int64_t i = 0; i < M; ++i) {
    float* qk_buf_ptr = qk_buf + i * N;
    auto attr_mask_offset = i / (head_num * seq_len) * seq_len;
    auto attr_mask_ptr = attr_mask + attr_mask_offset;
#pragma omp simd
    for (int64_t j = 0; j < N; ++j) {
      float mask_val = attr_mask_ptr[j];
      float qk_val = qk_buf_ptr[j];
      qk_val = qk_val * scaler + mask_val;
      qk_buf_ptr[j] = std::exp(qk_val);
    }
    float sum = 0.;
#pragma omp simd reduction(+ : sum)
    for (int64_t j = 0; j < N; ++j) {
      sum += qk_buf_ptr[j];
    }
    auto coef = 1.0f / (sum + g_epsilon);
#pragma omp simd
    for (int64_t j = 0; j < N; ++j) {
      qk_buf_ptr[j] *= coef;
    }
  }
}

}  // namespace kernels
}  // namespace layers
}  // namespace fast_transformers