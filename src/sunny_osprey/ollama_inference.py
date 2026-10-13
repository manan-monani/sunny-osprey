"""
Ollama Inference Client for Sunny Osprey

Uses Ollama's vision API (default: moondream / llava-phi3) to detect
anomalies and suspicious activities from video frames or webcam feeds.

Features:
- Fits comfortably inside 4GB VRAM (~1.5GB VRAM usage)
- Generates temporal keyframe montage grid with timestamp overlays
- Extracts structured anomaly classifications (type, severity, description)
- 100% compatible with AlertManager and Dashboard API
"""

import base64
import io
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont


class OllamaInferenceEngine:
    """
    Multimodal LLM inference via Ollama REST API.
    Stitches video frames into a temporal montage and requests security anomaly analysis.
    """

    def __init__(
        self,
        prompt_file: str = "prompt.txt",
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize the Ollama inference engine.

        Args:
            prompt_file: Path to security analysis prompt
            config: Configuration dict containing 'ollama' and 'llm' sections
        """
        self.prompt_file = prompt_file
        self.config = config or {}

        ollama_config = self.config.get("ollama", {})
        llm_config = self.config.get("llm", {})

        self.host = (
            os.getenv("OLLAMA_HOST") or 
            ollama_config.get("host", "http://localhost:11434")
        ).rstrip("/")
        # Default to multimodal vision model 'llava-phi3' (~2.9GB VRAM)
        self.model = ollama_config.get(
            "model", llm_config.get("model_name", "llava-phi3")
        )
        self.timeout = ollama_config.get("timeout", 60)
        self.max_new_tokens = llm_config.get("max_new_tokens", 350)
        # "ollama": Ollama's /api/chat. "openai": OpenAI-compatible /v1/chat/completions, as served by
        # llama.cpp's llama-server (runs Gemma 4 E2B with image input on a 4 GB GPU).
        self.api = (os.getenv("LLM_API") or ollama_config.get("api", "ollama")).lower()

        self.logger = logging.getLogger(__name__)
        self.logger.info(
            f"OllamaInferenceEngine initialized: host={self.host}, model={self.model}"
        )

    def _initialize_model(self):
        """Check Ollama server connectivity and model availability."""
        if self.api == "openai":
            self._wait_for_openai_server()
            return
        try:
            resp = requests.get(f"{self.host}/api/tags", timeout=5)
            resp.raise_for_status()
            tags = resp.json()
            models = [m["name"] for m in tags.get("models", [])]

            base = self.model.split(":")[0]
            matched = [m for m in models if base in m]

            if matched:
                self.logger.info(f"✅ Ollama vision model available: {matched[0]}")
            else:
                self.logger.warning(
                    f"⚠️ Model '{self.model}' not found in Ollama. Available: {models}"
                )
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Cannot connect to Ollama at {self.host}. Is Ollama running?"
            )
        except Exception as e:
            self.logger.warning(f"Ollama health check warning: {e}")

    def _wait_for_openai_server(self, max_wait: float = 300.0):
        """Wait until llama-server reports healthy (it answers 503 while the model is loading)."""
        deadline = time.monotonic() + max_wait
        while True:
            try:
                if requests.get(f"{self.host}/health", timeout=5).status_code == 200:
                    self.logger.info(f"✅ LLM server ready at {self.host} (model: {self.model})")
                    return
            except requests.exceptions.RequestException:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"LLM server at {self.host} not ready after {max_wait:.0f}s. Is it running?")
            self.logger.info(f"Waiting for LLM server at {self.host}...")
            time.sleep(5)

    def _chat_openai(self, system_prompt: str, user_prompt: str, encoded_image: str) -> str:
        """Send one chat request to an OpenAI-compatible endpoint and return the reply text."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"}},
                    ],
                },
            ],
            "max_tokens": self.max_new_tokens,
            "temperature": 0.0,  # Deterministic security evaluation
            "response_format": {"type": "json_object"},  # grammar-constrained, always parseable
            # Gemma 4 "thinks" before answering by default and spends the whole token budget on it
            "chat_template_kwargs": {"enable_thinking": False},
        }
        resp = requests.post(f"{self.host}/v1/chat/completions", json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def _create_frame_grid(
        self,
        frames: List[Tuple[Image.Image, float]],
        cell_size: int = 384,
    ) -> Image.Image:
        """
        Stitch sequential video frames into a chronological montage grid.
        Annotates each frame with a timestamp tag (T=X.Xs).
        """
        if not frames:
            return Image.new("RGB", (cell_size, cell_size), color=(0, 0, 0))

        n = len(frames)
        if n == 1:
            return frames[0][0]

        # Determine grid dimensions (2x2, 3x2, or 3x3)
        if n <= 4:
            cols, rows = 2, 2
            target_count = 4
        elif n <= 6:
            cols, rows = 3, 2
            target_count = 6
        else:
            cols, rows = 3, 3
            target_count = 9

        # Subsample frames evenly to fill grid
        if n >= target_count:
            step = (n - 1) / (target_count - 1)
            indices = [int(i * step) for i in range(target_count)]
        else:
            indices = list(range(n)) + [n - 1] * (target_count - n)

        selected = [frames[i] for i in indices]

        grid_w = cols * cell_size
        grid_h = rows * cell_size
        montage = Image.new("RGB", (grid_w, grid_h), color=(15, 15, 15))
        draw = ImageDraw.Draw(montage)

        for idx, (img, timestamp) in enumerate(selected):
            c = idx % cols
            r = idx // cols
            x = c * cell_size
            y = r * cell_size

            resized = img.resize((cell_size, cell_size), Image.LANCZOS)
            montage.paste(resized, (x, y))

            # Draw subtle timestamp badge
            label = f"T={timestamp:.1f}s"
            draw.rectangle([x + 6, y + 6, x + 72, y + 26], fill=(0, 0, 0))
            draw.text((x + 10, y + 8), label, fill=(255, 255, 255))

        return montage

    def _encode_image(self, img: Image.Image, max_size: int = 896) -> str:
        """Encode PIL Image to base64 PNG string."""
        w, h = img.size
        if max(w, h) > max_size:
            scale = max_size / max(w, h)
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def _load_prompt(self) -> str:
        """Load security prompt from file or fallback."""
        candidates = [self.prompt_file, "prompt.txt", "/app/prompt.txt"]
        for p in candidates:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        return f.read().strip()
                except Exception:
                    pass
        return (
            "Look at this sequence of security camera frames. "
            "Determine if there is unusual, suspicious, or emergency activity. "
            "Output JSON with 'suspicious' (Yes/No), 'anomaly_type', 'severity', and 'description'."
        )

    def _load_system_prompt(self) -> str:
        """Load system prompt from file."""
        candidates = ["system_prompt.txt", "/app/system_prompt.txt"]
        for p in candidates:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        return f.read().strip()
                except Exception:
                    pass
        return "You are an expert security surveillance AI. Output strictly valid JSON."

    def analyze_frames(
        self, frames: List[Tuple[Image.Image, float]]
    ) -> Optional[Dict[str, Any]]:
        """
        Analyze a sequence of video frames for suspicious activity.

        Args:
            frames: List of (PIL.Image, timestamp_seconds) tuples

        Returns:
            Dict with 'suspicious', 'anomaly_type', 'severity', and 'description'
        """
        if not frames:
            self.logger.error("No frames provided for analysis")
            return None

        # Build chronological montage grid
        montage_image = self._create_frame_grid(frames)
        encoded_image = self._encode_image(montage_image)

        user_prompt = self._load_prompt()
        system_prompt = self._load_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": user_prompt,
                "images": [encoded_image],
            },
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": self.max_new_tokens,
                "temperature": 0.0,  # Deterministic security evaluation
                "top_p": 1.0,
            },
        }

        try:
            start = time.perf_counter()
            if self.api == "openai":
                raw_response = self._chat_openai(system_prompt, user_prompt, encoded_image)
            else:
                resp = requests.post(
                    f"{self.host}/api/chat",
                    json=payload,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                raw_response = resp.json().get("message", {}).get("content", "")
            elapsed = time.perf_counter() - start

            self.logger.info(
                f"LLM inference completed in {elapsed:.2f}s ({self.model})"
            )

            parsed = self._parse_json_response(raw_response)
            parsed["inference_time_s"] = round(elapsed, 2)
            parsed["model"] = self.model
            return parsed

        except requests.exceptions.Timeout:
            self.logger.error(f"Ollama request timed out after {self.timeout}s")
            return {
                "error": "timeout",
                "suspicious": "No",
                "anomaly_type": "None",
                "severity": "low",
                "description": "Inference timed out.",
            }
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Ollama API request failed: {e}")
            return {
                "error": str(e),
                "suspicious": "No",
                "anomaly_type": "Error",
                "severity": "low",
                "description": f"Inference API error: {e}",
            }

    def run_inference(self, video_path: str) -> Optional[Dict[str, Any]]:
        """
        Run inference on a video file.
        Matches LLMInferenceEngine.run_inference() interface for tests.
        """
        frames = self._extract_frames_from_video(video_path, num_frames=8)
        if not frames:
            return {"error": "Could not extract frames", "suspicious": "No"}
        return self.analyze_frames(frames)

    def _extract_frames_from_video(
        self, video_path: str, num_frames: int = 8
    ) -> List[Tuple[Image.Image, float]]:
        """Extract keyframes from a video file."""
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                self.logger.error(f"Cannot open video: {video_path}")
                return []

            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

            if total <= 0:
                cap.release()
                return []

            step = max(1, total // num_frames)
            frames = []

            for i in range(min(num_frames, total)):
                idx = min(i * step, total - 1)
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ret, frame = cap.read()
                if not ret or frame is None:
                    continue
                pil_img = Image.fromarray(frame[:, :, ::-1])
                timestamp = round(idx / fps, 2)
                frames.append((pil_img, timestamp))

            cap.release()
            return frames
        except Exception as e:
            self.logger.error(f"Frame extraction error: {e}")
            return []

    def _parse_json_response(self, response: str) -> Dict[str, Any]:
        """
        Robustly extract and validate JSON from model output.
        Handles JSON arrays, capitalized keys, markdown code fences, and heuristic text fallbacks.
        """
        cleaned = response.strip()

        # Strip markdown code blocks
        if "```" in cleaned:
            match = re.search(r"```(?:json)?\s*([\[\{].*?[\]\}])\s*```", cleaned, re.DOTALL)
            if match:
                cleaned = match.group(1).strip()
            else:
                lines = cleaned.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                cleaned = "\n".join(lines).strip()

        json_obj = None

        # Check for JSON array first
        array_match = re.search(r"\[\s*(\{.*?\})\s*\]", cleaned, re.DOTALL)
        if array_match:
            try:
                json_obj = json.loads(array_match.group(1))
            except Exception:
                pass

        if json_obj is None:
            # Find first { and matching }
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start != -1 and end > start:
                try:
                    json_obj = json.loads(cleaned[start:end])
                except Exception:
                    pass

        if isinstance(json_obj, list) and len(json_obj) > 0:
            json_obj = json_obj[0]

        if isinstance(json_obj, dict):
            # Normalize keys to lowercase
            lower_dict = {k.lower(): v for k, v in json_obj.items()}
            susp_val = str(lower_dict.get("suspicious", "")).lower()
            is_susp = susp_val in ["yes", "true", "1"]
            
            anomaly_type = lower_dict.get("anomaly_type") or lower_dict.get("anomalytype")
            if not anomaly_type or anomaly_type.lower() in ["none", "n/a", "unknown"]:
                anomaly_type = "Suspicious Activity" if is_susp else "Normal Activity"
                
            severity = lower_dict.get("severity", "high" if is_susp else "low")
            description = lower_dict.get("description", response[:250])

            return {
                "suspicious": "Yes" if is_susp else "No",
                "anomaly_type": anomaly_type,
                "severity": str(severity).lower(),
                "description": str(description),
            }

        # Fallback heuristic parser if model did not return valid JSON
        self.logger.warning(f"Could not parse strict JSON, applying heuristic extraction")
        text_lower = response.lower()

        # Keywords indicating suspicious / criminal / dangerous activity
        suspicious_keywords = [
            "suspicious", "intruder", "trespass", "theft", "stealing",
            "stolen", "burglary", "fall", "fallen", "weapon", "criminal",
            "break-in", "tampering", "unusual", "emergency"
        ]
        normal_keywords = [
            "normal", "routine", "nothing unusual", "no suspicious",
            "static", "empty", "pedestrian walking", "vehicle driving normally"
        ]

        has_suspicious = any(kw in text_lower for kw in suspicious_keywords)
        has_normal = any(kw in text_lower for kw in normal_keywords)

        if "suspicious: yes" in text_lower or '"suspicious": "yes"' in text_lower:
            is_susp = True
        elif "suspicious: no" in text_lower or '"suspicious": "no"' in text_lower:
            is_susp = False
        else:
            is_susp = has_suspicious and not has_normal

        # Guess anomaly type
        if "theft" in text_lower or "package" in text_lower or "stealing" in text_lower:
            anomaly_type = "Theft / Porch Piracy"
        elif "fall" in text_lower or "collapsed" in text_lower:
            anomaly_type = "Fall / Medical Emergency"
        elif "trespass" in text_lower or "fence" in text_lower or "intruder" in text_lower:
            anomaly_type = "Intrusion / Trespassing"
        elif "tampering" in text_lower or "vandalism" in text_lower:
            anomaly_type = "Tampering / Vandalism"
        elif is_susp:
            anomaly_type = "Suspicious Activity"
        else:
            anomaly_type = "Normal Activity"

        return {
            "suspicious": "Yes" if is_susp else "No",
            "anomaly_type": anomaly_type,
            "severity": "high" if is_susp else "low",
            "description": response.strip(),
            "raw_response": response,
        }

    def health_check(self) -> bool:
        """Check if Ollama server is reachable."""
        try:
            path = "/health" if self.api == "openai" else "/api/tags"
            resp = requests.get(f"{self.host}{path}", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False
