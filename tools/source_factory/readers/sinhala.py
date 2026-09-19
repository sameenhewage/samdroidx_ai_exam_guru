"""Local Sinhala specialist readers (decision D2).

Both are LoRA adapters over a vision-language OCR base:

* `sinhala-lightonocr`  — `avishadilhara/sinhala-lightonocr-2-1b-Qlora`
                          over `lightonai/LightOnOCR-2-1B`
* `sinhala-deepseek`    — `avishadilhara/sinhala-deepseek-ocr-Qlora`
                          over `unsloth/DeepSeek-OCR`

Both adapters were trained on Sri Lankan **legal acts**, not teacher guides.
That domain gap is exactly why the published 98% character accuracy is not
evidence for this corpus and why Phase 3 measures them here instead.

No network provider is involved. OpenAI must not be used (decision D1).
"""

from __future__ import annotations

import gc
import time
from dataclasses import dataclass

import numpy as np

from tools.source_factory.readers.port import ReaderUnavailable, ReadRequest, ReadResult

SINHALA_INSTRUCTION = (
    "Transcribe every character visible in this image exactly as printed. "
    "Preserve the original Sinhala spelling, punctuation, digits and spacing. "
    "Do not translate, correct, summarise or explain. "
    "Output only the transcription."
)


def _to_pil(crop: np.ndarray):
    from PIL import Image

    return Image.fromarray(crop[:, :, ::-1])  # BGR -> RGB


@dataclass
class _Loaded:
    model: object
    processor: object
    tokenizer: object | None = None


class _TorchReader:
    """Shared plumbing: device selection, VRAM accounting, teardown."""

    name = "abstract"
    languages: tuple[str, ...] = ("sinhala",)
    base_model = ""
    adapter = ""

    def __init__(self, *, dtype: str = "bfloat16", max_new_tokens: int = 1024) -> None:
        self._loaded: _Loaded | None = None
        self._dtype_name = dtype
        self.max_new_tokens = max_new_tokens
        self.load_seconds = 0.0

    # -- lifecycle ---------------------------------------------------------

    def _torch(self):
        try:
            import torch
        except ImportError as error:  # pragma: no cover - environment problem
            raise ReaderUnavailable(f"torch is not installed: {error}") from error
        if not torch.cuda.is_available():
            raise ReaderUnavailable("no CUDA device; these readers are not benchmarked on CPU")
        return torch

    def _dtype(self):
        torch = self._torch()
        return {"bfloat16": torch.bfloat16, "float16": torch.float16}[self._dtype_name]

    def load(self) -> None:
        if self._loaded is not None:
            return
        torch = self._torch()
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        self._loaded = self._build()
        self.load_seconds = time.perf_counter() - started

    def _build(self) -> _Loaded:  # pragma: no cover - overridden
        raise NotImplementedError

    def unload(self) -> None:
        self._loaded = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.reset_peak_memory_stats()
        except ImportError:
            pass

    def peak_vram(self) -> int | None:
        try:
            import torch

            return int(torch.cuda.max_memory_allocated()) if torch.cuda.is_available() else None
        except ImportError:
            return None

    # -- reading -----------------------------------------------------------

    def read(self, request: ReadRequest) -> ReadResult:
        started = time.perf_counter()
        try:
            self.load()
            text = self._infer(request)
        except ReaderUnavailable:
            raise
        except Exception as error:  # noqa: BLE001 - reported as a result, not hidden
            return ReadResult(
                reader=self.name,
                region_id=request.region_id,
                text="",
                failure=f"{type(error).__name__}: {error}"[:400],
                seconds=time.perf_counter() - started,
                peak_vram_bytes=self.peak_vram(),
            )
        return ReadResult(
            reader=self.name,
            region_id=request.region_id,
            text=text,
            abstained=not text.strip(),
            seconds=time.perf_counter() - started,
            peak_vram_bytes=self.peak_vram(),
            raw=text,
            meta={"base_model": self.base_model, "adapter": self.adapter},
        )

    def _infer(self, request: ReadRequest) -> str:  # pragma: no cover - overridden
        raise NotImplementedError


class SinhalaLightOnOCRReader(_TorchReader):
    name = "sinhala-lightonocr"
    base_model = "lightonai/LightOnOCR-2-1B"
    adapter = "avishadilhara/sinhala-lightonocr-2-1b-Qlora"

    longest_edge = 1540

    def _build(self) -> _Loaded:
        from peft import PeftModel
        from transformers import LightOnOcrForConditionalGeneration, LightOnOcrProcessor

        # The explicit LightOnOcr classes are required. `AutoModel*` resolves
        # this checkpoint to Mistral3/Pixtral, which accepts the inputs without
        # complaint and then reports "no visible content" or loops, because the
        # patch-merge geometry does not match. transformers >= 5 provides them.
        # The processor comes from the adapter: it carries the trained template.
        processor = LightOnOcrProcessor.from_pretrained(self.adapter)
        processor.tokenizer.padding_side = "left"
        model = LightOnOcrForConditionalGeneration.from_pretrained(
            self.base_model, dtype=self._dtype(), device_map="cuda:0"
        )
        model = PeftModel.from_pretrained(model, self.adapter)
        model.eval()
        return _Loaded(model=model, processor=processor)

    def _infer(self, request: ReadRequest) -> str:
        import torch

        assert self._loaded is not None
        processor, model = self._loaded.processor, self._loaded.model
        # No text instruction. This model was trained to transcribe the image
        # alone; adding an instruction makes it echo the instruction back.
        messages = [{"role": "user", "content": [{"type": "image"}]}]
        text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = processor(
            text=text,
            images=[_to_pil(request.crop)],
            return_tensors="pt",
            size={"longest_edge": self.longest_edge},
        ).to(model.device)
        # Only the pixel tensors follow the model dtype; ids and masks stay integral.
        inputs = {
            key: (value.to(self._dtype()) if value.is_floating_point() else value)
            for key, value in inputs.items()
        }
        with torch.inference_mode():
            generated = model.generate(
                **inputs, max_new_tokens=self.max_new_tokens, do_sample=False
            )
        prompt_length = inputs["input_ids"].shape[1]
        return processor.batch_decode(
            generated[:, prompt_length:], skip_special_tokens=True
        )[0].strip()


class SinhalaDeepSeekOCRReader(_TorchReader):
    name = "sinhala-deepseek"
    base_model = "unsloth/DeepSeek-OCR"
    adapter = "avishadilhara/sinhala-deepseek-ocr-Qlora"

    def _build(self) -> _Loaded:
        from peft import PeftModel
        from transformers import AutoModel, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(self.base_model, trust_remote_code=True)
        model = AutoModel.from_pretrained(
            self.base_model,
            dtype=self._dtype(),
            trust_remote_code=True,
            use_safetensors=True,
            _attn_implementation="eager",
        )
        model = model.eval().cuda().to(self._dtype())
        model = PeftModel.from_pretrained(model, self.adapter)
        model.eval()
        return _Loaded(model=model, processor=None, tokenizer=tokenizer)

    def _infer(self, request: ReadRequest) -> str:
        import tempfile
        from pathlib import Path

        import cv2

        assert self._loaded is not None
        model, tokenizer = self._loaded.model, self._loaded.tokenizer
        # DeepSeek-OCR's remote code reads from a path, so the crop is staged.
        with tempfile.TemporaryDirectory() as workspace:
            image_path = Path(workspace) / "crop.png"
            ok, buffer = cv2.imencode(".png", request.crop)
            if not ok:
                raise RuntimeError("cannot encode crop for DeepSeek-OCR")
            image_path.write_bytes(buffer.tobytes())
            inner = getattr(model, "base_model", model)
            inner = getattr(inner, "model", inner)
            output = inner.infer(
                tokenizer,
                prompt=f"<image>\n{SINHALA_INSTRUCTION}",
                image_file=str(image_path),
                output_path=workspace,
                base_size=1024,
                image_size=640,
                crop_mode=True,
                save_results=False,
                test_compress=False,
            )
        return (output or "").strip()


READERS = {
    SinhalaLightOnOCRReader.name: SinhalaLightOnOCRReader,
    SinhalaDeepSeekOCRReader.name: SinhalaDeepSeekOCRReader,
}
