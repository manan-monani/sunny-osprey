"""
Run llama.cpp's convert_hf_to_gguf.py --mmproj for Gemma 3n with the audio encoder left out.

Why: Gazeguard only sends images, and upstream conversion of the Gemma 3n audio tower currently
fails ("Can not map tensor 'model.audio_tower.subsample_conv_projection.conv_0.norm.weight'").
A vision-only projector also avoids loading ~600 MB of unused audio weights.
It also fixes the shape of the MobileNetV5 block layer_scale tensors (see _modify_tensors).

Usage (inside ghcr.io/ggml-org/llama.cpp:full-cuda): python3 convert_mmproj_vision_only.py <hf_dir> --mmproj ...
"""
import runpy
import sys

sys.path.insert(0, "/app")
sys.path.insert(1, "/app/gguf-py")

import gguf  # noqa: E402
from conversion import gemma  # noqa: E402
from conversion.base import MmprojModel  # noqa: E402

Model = gemma.Gemma3nVisionAudioModel
_orig_init = Model.__init__
_orig_modify = Model.modify_tensors


def _init(self, *args, **kwargs):
    _orig_init(self, *args, **kwargs)
    self.has_audio_encoder = False


def _set_gguf_parameters(self):
    MmprojModel.set_gguf_parameters(self)  # writes audio metadata only when has_audio_encoder is set
    self.gguf_writer.add_clip_vision_projector_type(gguf.VisionProjectorType.GEMMA3NV)
    self.gguf_writer.add_vision_attention_layernorm_eps(self.hparams.get("layer_norm_eps", 1e-6))


def _modify_tensors(self, data_torch, name, bid):
    if gemma.ConformerAudioModel.is_audio_tensor(name):
        return
    # Upstream reshapes layer_scale.gamma to [1, C, 1, 1] only for non-block tensors; MobileNetV5 block
    # tensors return early, leaving gamma 1-D, and clip warmup then dies on
    # GGML_ASSERT(ggml_can_repeat(b, a)) in build_inverted_residual.
    if name.startswith("model.vision_tower.timm_model.blocks.") and name.endswith("layer_scale.gamma"):
        data_torch = data_torch.reshape(1, -1, 1, 1)
    yield from _orig_modify(self, data_torch, name, bid)


Model.__init__ = _init
Model.set_gguf_parameters = _set_gguf_parameters
Model.modify_tensors = _modify_tensors

sys.argv[0] = "/app/convert_hf_to_gguf.py"
runpy.run_path("/app/convert_hf_to_gguf.py", run_name="__main__")
