# Copyright 2020 Tencent
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
turbo-transformers Benchmark Utils

Usage:
    benchmark <model> --seq_len=<int> [--framework=<f>] [--batch_size=<int>] [-n <int>] [--num_threads=<int>]

Options:
    --framework=<f>      The framework to test in (torch, torch_jit, turbo-transformers,
                            onnxruntime-cpu, onnxruntime-mkldnn) [default: turbo-transformers].
    --seq_len=<int>      The sequence length.
    --batch_size=<int>   The batch size [default: 1].
    -n <int>             The iteration count [default: 10000].
    --num_threads=<int>  The thread count [default: 1].
"""

import json
import os

import docopt


def benchmark_turbo_transformers(model: str, seq_len: int, batch_size: int,
                                 n: int, num_threads: int):
    import torch
    import transformers
    import contexttimer
    import turbo_transformers
    import cProfile
    turbo_transformers.set_num_threads(num_threads)

    model_dir = os.path.join(os.path.dirname(__file__),
                             '../turbo_transformers/python/tests/test-model')
    model = transformers.BertModel.from_pretrained(
        model_dir)  # type: transformers.BertModel
    model.eval()

    cfg = model.config  # type: transformers.BertConfig
    input_ids = torch.randint(low=0,
                              high=cfg.vocab_size - 1,
                              size=(batch_size, seq_len),
                              dtype=torch.long)
    model = turbo_transformers.BertModel.from_torch(model)

    with turbo_transformers.gperf_guard(
            f"ft_{batch_size}_{seq_len}_{num_threads}.gperf"):
        model(input_ids)

    py_profile = cProfile.Profile()
    py_profile.enable()
    try:
        model(input_ids)
    finally:
        py_profile.disable()
        py_profile.dump_stats(
            f"ft_{batch_size}_{seq_len}_{num_threads}.py_profile")

    model(input_ids)
    with contexttimer.Timer() as t:
        for _ in range(n):
            model(input_ids)

    print(
        json.dumps({
            "QPS": n / t.elapsed,
            "elapsed": t.elapsed,
            "n": n,
            "batch_size": batch_size,
            "seq_len": seq_len,
            "framework": "turbo_transformers",
            "n_threads": num_threads
        }))


def benchmark_torch(model: str, seq_len: int, batch_size: int, n: int,
                    num_threads: int):
    import torch
    import transformers
    import contexttimer
    torch.set_num_threads(num_threads)
    torch.set_grad_enabled(False)

    model_dir = os.path.join(os.path.dirname(__file__),
                             '../turbo_transformers/python/tests/test-model')
    model = transformers.BertModel.from_pretrained(
        model_dir)  # type: transformers.BertModel
    model.eval()
    cfg = model.config  # type: transformers.BertConfig
    input_ids = torch.randint(low=0,
                              high=cfg.vocab_size - 1,
                              size=(batch_size, seq_len),
                              dtype=torch.long)
    model(input_ids)
    with contexttimer.Timer() as t:
        for _ in range(n):
            model(input_ids)

    print(
        json.dumps({
            "QPS": n / t.elapsed,
            "elapsed": t.elapsed,
            "n": n,
            "batch_size": batch_size,
            "seq_len": seq_len,
            "framework": "torch",
            "n_threads": num_threads
        }))


def benchmark_torch_jit(model: str, seq_len: int, batch_size: int, n: int,
                        num_threads: int):
    import transformers
    import contexttimer
    import torch.jit
    torch.set_num_threads(num_threads)
    torch.set_grad_enabled(False)
    model = transformers.BertModel.from_pretrained(
        model)  # type: transformers.BertModel
    model.eval()
    cfg = model.config  # type: transformers.BertConfig
    input_ids = torch.randint(low=0,
                              high=cfg.vocab_size - 1,
                              size=(batch_size, seq_len),
                              dtype=torch.long)

    model = torch.jit.trace(model, (input_ids, ))

    with torch.jit.optimized_execution(True):
        model(input_ids)
        with contexttimer.Timer() as t:
            for _ in range(n):
                model(input_ids)

    print(
        json.dumps({
            "QPS": n / t.elapsed,
            "elapsed": t.elapsed,
            "n": n,
            "batch_size": batch_size,
            "seq_len": seq_len,
            "framework": "torch_jit",
            "n_threads": num_threads
        }))


def generate_onnx_model(model: str, filename: str, seq_len: int,
                        batch_size: int):
    import transformers
    import torch
    torch.set_grad_enabled(False)
    model = transformers.BertModel.from_pretrained(
        model)  # type: transformers.BertModel
    model.eval()
    cfg = model.config  # type: transformers.BertConfig
    input_ids = torch.randint(low=0,
                              high=cfg.vocab_size - 1,
                              size=(batch_size, seq_len),
                              dtype=torch.long)
    with open(filename, 'wb') as outf:
        torch.onnx.export(model=model, args=(input_ids, ), f=outf)
        outf.flush()
    return cfg.vocab_size


def onnxruntime_benchmark_creator(backend: str):
    def _impl_(model: str, seq_len: int, batch_size: int, n: int,
               num_threads: int):
        import multiprocessing
        temp_fn = "/tmp/temp_onnx.model"
        p = multiprocessing.Pool(1)
        vocab_size = p.apply(generate_onnx_model,
                             args=(model, temp_fn, seq_len, batch_size))
        p.close()
        import contexttimer
        import os
        import onnxruntime.backend
        import onnx
        import numpy
        if not onnxruntime.backend.supports_device(backend):
            raise RuntimeError(
                f"onnxruntime does not support {backend}, recompile it!")

        os.environ['OMP_NUM_THREADS'] = str(num_threads)
        os.environ['MKL_NUM_THREADS'] = str(num_threads)

        model = onnx.load_model(f=temp_fn)
        model = onnxruntime.backend.prepare(
            model=model,
            device=backend,
            graph_optimization_level=onnxruntime.GraphOptimizationLevel.
            ORT_ENABLE_ALL)
        input_ids = numpy.random.randint(low=0,
                                         high=vocab_size - 1,
                                         size=(batch_size, seq_len),
                                         dtype=numpy.int64)
        model.run(inputs=[input_ids])

        with contexttimer.Timer() as t:
            for _ in range(n):
                model.run(inputs=[input_ids])

        print(
            json.dumps({
                "QPS": n / t.elapsed,
                "elapsed": t.elapsed,
                "n": n,
                "batch_size": batch_size,
                "seq_len": seq_len,
                "framework": f"onnx_rt_{backend}",
                "n_threads": num_threads
            }))

    return _impl_


def main():
    args = docopt.docopt(__doc__)
    kwargs = {
        'model': args['<model>'],
        'seq_len': int(args['--seq_len']),
        'batch_size': int(args['--batch_size']),
        'n': int(args['-n']),
        'num_threads': int(args['--num_threads'])
    }

    if args['--framework'] == 'turbo-transformers':
        benchmark_turbo_transformers(**kwargs)
    elif args['--framework'] == 'torch':
        benchmark_torch(**kwargs)
    elif args['--framework'] == 'torch_jit':
        benchmark_torch_jit(**kwargs)
    elif args['--framework'] == 'onnxruntime-cpu':
        onnxruntime_benchmark_creator('CPU')(**kwargs)
    elif args['--framework'] == 'onnxruntime-mkldnn':
        onnxruntime_benchmark_creator('MKL-DNN')(**kwargs)
    else:
        raise RuntimeError(f"Not supportted framework {args['--framework']}")


if __name__ == '__main__':
    main()
