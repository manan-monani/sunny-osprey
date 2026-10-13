"""
Run llama.cpp's convert_hf_to_gguf.py for Gemma 3n with bounded memory.

Why: for image support the converter pads the token and per-layer embedding tables from 262144 to
262400 rows. Upstream does this by widening the 4 GB bf16 per-layer table to float32 (8 GB) and
concatenating (another 8 GB), which gets OOM-killed in Docker Desktop's default 8 GB VM.
Here those two tensors are streamed from the safetensors file in row chunks (padding included)
through gguf's LazyChunkedTensor, so peak memory stays at a few hundred MB.

Usage (inside ghcr.io/ggml-org/llama.cpp:full-cuda): python3 convert_text_lowmem.py <hf_dir> --outtype bf16 ...
"""
import json
import runpy
import sys

sys.path.insert(0, "/app")
sys.path.insert(1, "/app/gguf-py")

import numpy as np  # noqa: E402
import torch  # noqa: E402
import gguf  # noqa: E402
from safetensors import safe_open  # noqa: E402
from conversion import gemma  # noqa: E402

CHUNK_ROWS = 8192
Model = gemma.Gemma3NModel
_orig_modify = Model.modify_tensors


def _embedding_key(name: str) -> str | None:
    base = name.rsplit(".", 1)[0].rsplit(".", 1)[-1]  # "...embed_tokens_per_layer.weight" -> "embed_tokens_per_layer"
    if base in ("embed_tokens", "embed_tokens_per_layer"):
        return f"model.language_model.{base}.weight"
    return None


def _modify_tensors(self, data_torch, name, bid):
    key = _embedding_key(name)
    if key is None:
        yield from _orig_modify(self, data_torch, name, bid)
        return

    weight_map = json.loads((self.dir_model / "model.safetensors.index.json").read_text())["weight_map"]
    handle = safe_open(str(self.dir_model / weight_map[key]), framework="pt")
    tensor_slice = handle.get_slice(key)
    rows, cols = tensor_slice.get_shape()
    vocab_size = max(self.hparams.get("vocab_size", 262400), rows)

    def rows_chunk(start, stop):
        return lambda: tensor_slice[start:stop].to(torch.float32).numpy()

    chunks = [rows_chunk(i, min(i + CHUNK_ROWS, rows)) for i in range(0, rows, CHUNK_ROWS)]
    if vocab_size > rows:
        # zero rows for the vision/audio special tokens (their embeddings come from the encoders)
        chunks.append(lambda: np.zeros((vocab_size - rows, cols), dtype=np.float32))
    print(f"Streaming {key} [{rows}, {cols}] -> [{vocab_size}, {cols}] in {len(chunks)} chunks", flush=True)

    chunked = gguf.LazyChunkedTensor(chunks, (vocab_size, cols), np.float32)
    chunked._keepalive = handle  # keep the safetensors mmap open until written
    yield self.map_tensor_name(name), chunked


Model.modify_tensors = _modify_tensors

sys.argv[0] = "/app/convert_hf_to_gguf.py"
runpy.run_path("/app/convert_hf_to_gguf.py", run_name="__main__")
