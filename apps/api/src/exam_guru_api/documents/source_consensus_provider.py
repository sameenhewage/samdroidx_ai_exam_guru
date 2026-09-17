import time
from collections.abc import Callable
from contextlib import ExitStack
from typing import Literal, cast

from openai import OpenAI

from exam_guru_api.documents.source_consensus import (
    IndependentReading,
    SourceExecutionBlockedError,
    SourceRegionInput,
    SourceWitnessRecordingError,
)
from exam_guru_api.documents.source_geometry import SourcePageGeometry, detect_source_geometry
from exam_guru_api.documents.source_machine import Purpose, SourceConsensusEngine
from exam_guru_api.documents.source_reading import (
    SourceLayout,
    SourceLayoutRegion,
    crop_source_image,
)
from exam_guru_api.documents.source_reading_openai import (
    _LAYOUT_RULES,
    OpenAISourceWitnessProvider,
    SourcePassRecorder,
    SourceReadingSession,
)
from exam_guru_api.documents.source_reading_qwen import (
    QwenSourceReadError,
    QwenSourceReadProvider,
    QwenTransport,
    _local_json,
)
from exam_guru_api.documents.source_renders import SourcePageRenderer
from exam_guru_api.documents.understanding_contracts import RegionBounds
from exam_guru_api.documents.understanding_openai import (
    OpenAIUnderstandingConfig,
    OpenAIUnderstandingProvider,
    _Client,
)
from exam_guru_api.documents.understanding_provider import (
    UnderstandingProviderError,
    UnderstandingProviderResult,
    UnderstandingRequest,
)
from exam_guru_api.generation.ports import ProviderFailureCode

CONSENSUS_PROMPT_VERSION = "qwen-openai-source-consensus.v1"
_QWEN_IDENTITY_BLOCKERS = frozenset(
    {
        "qwen_runtime_unavailable",
        "qwen_runtime_mismatch",
        "qwen_model_missing",
        "qwen_model_digest_mismatch",
    }
)


def _inside(inner: RegionBounds, outer: RegionBounds) -> float:
    intersection = max(0.0, min(inner.right, outer.right) - max(inner.left, outer.left)) * max(
        0.0, min(inner.bottom, outer.bottom) - max(inner.top, outer.top)
    )
    return intersection / ((inner.right - inner.left) * (inner.bottom - inner.top))


def _geometry_first_layout(layout: SourceLayout, geometry: SourcePageGeometry) -> SourceLayout:
    retained: list[SourceLayoutRegion] = []
    for region in layout.regions:
        if any(_inside(region.bounds, table.bounds) >= 0.7 for table in geometry.tables):
            continue
        retained.append(region.model_copy(update={"parent_key": None}))
    retained.extend(
        SourceLayoutRegion(
            key=table.key, kind="grid", reading_order=0, parent_key=None, bounds=table.bounds
        )
        for table in geometry.tables
    )
    keys = {region.key for region in retained}
    if len(keys) != len(retained):
        raise ValueError("source layout and deterministic table keys conflict")
    ordered = tuple(
        region.model_copy(update={"reading_order": index})
        for index, region in enumerate(
            sorted(retained, key=lambda region: (region.bounds.top, region.bounds.left))
        )
    )
    return SourceLayout(
        schema_version="source-layout.v1",
        language=layout.language,
        regions=ordered,
        relationships=tuple(
            link
            for link in layout.relationships
            if link.source_key in keys and link.target_key in keys
        ),
    )


class ConsensusSourceReadingProvider:
    def __init__(
        self,
        config: OpenAIUnderstandingConfig,
        *,
        client: object | None = None,
        qwen_transport: QwenTransport = _local_json,
        recorder: SourcePassRecorder | None = None,
        renderer: SourcePageRenderer | None = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.config = OpenAIUnderstandingConfig.model_validate(config)
        if (
            self.config.profile.prompt_version != CONSENSUS_PROMPT_VERSION
            or self.config.profile.qwen is None
        ):
            raise ValueError("consensus source reading requires both configured readers")
        self.client = client
        self.qwen_transport = qwen_transport
        self.recorder = recorder
        self.renderer = renderer
        self.clock_ns = clock_ns

    def with_recorder(self, recorder: SourcePassRecorder) -> "ConsensusSourceReadingProvider":
        return ConsensusSourceReadingProvider(
            self.config,
            client=self.client,
            qwen_transport=self.qwen_transport,
            recorder=recorder,
            renderer=self.renderer,
            clock_ns=self.clock_ns,
        )

    def with_renderer(self, renderer: SourcePageRenderer) -> "ConsensusSourceReadingProvider":
        return ConsensusSourceReadingProvider(
            self.config,
            client=self.client,
            qwen_transport=self.qwen_transport,
            recorder=self.recorder,
            renderer=renderer,
            clock_ns=self.clock_ns,
        )

    def understand(self, request: UnderstandingRequest) -> UnderstandingProviderResult:
        request = UnderstandingRequest.model_validate(request)
        qwen_config = request.profile.qwen
        pipeline = request.budget.pipeline
        renderer = self.renderer
        if (
            request.profile != self.config.profile
            or qwen_config is None
            or pipeline is None
            or renderer is None
        ):
            raise UnderstandingProviderError(ProviderFailureCode.INVALID_REQUEST)
        base = renderer.render(300)
        if (
            base.metadata.sha256 != request.source.image_sha256
            or renderer.source.document_id != request.source.document_id
            or renderer.page_number != request.source.page_number
        ):
            raise UnderstandingProviderError(ProviderFailureCode.INVALID_REQUEST)
        number = -1
        active: tuple[str, SourceRegionInput, str] | None = None
        emitted: set[str] = set()

        def emit(event_type: str, **fields: object) -> None:
            if active is None:
                raise SourceWitnessRecordingError("source witness input is not bound")
            reader, source, configuration = active
            event = {
                "schema_version": "source-witness-event.v1",
                "pass_number": number,
                "reader": reader,
                "input": source.model_dump(mode="json"),
                "input_fingerprint": source.fingerprint,
                "reader_configuration_fingerprint": configuration,
                "event": event_type,
                **fields,
            }
            if self.recorder:
                try:
                    self.recorder(event)
                except Exception:
                    raise SourceWitnessRecordingError("source witness persistence failed") from None
            emitted.add(event_type)

        def raw(event: dict[str, object]) -> None:
            event_type = event.get("event")
            if event_type not in {"requested", "provider_completed", "failed"}:
                raise SourceWitnessRecordingError("unexpected source witness event")
            emit(event_type, details=event)

        def begin(reader: str, value: SourceRegionInput) -> None:
            nonlocal active, number, emitted
            number += 1
            active = (
                reader,
                value,
                qwen_config.fingerprint if reader == "qwen" else request.profile.fingerprint,
            )
            emitted = set()

        with ExitStack() as stack:
            client = self.client
            if client is None:
                client = stack.enter_context(
                    OpenAI(
                        api_key=self.config.api_key.get_secret_value(),
                        base_url="https://api.openai.com/v1",
                        timeout=request.budget.timeout_ms / 1000,
                        max_retries=0,
                    )
                )
            session = SourceReadingSession(
                request,
                OpenAIUnderstandingProvider(self.config),
                cast(_Client, client),
                raw,
                self.clock_ns,
            )
            qwen = QwenSourceReadProvider(
                qwen_config, transport=self.qwen_transport, recorder=raw, clock_ns=self.clock_ns
            )
            try:
                qwen.verify_runtime()
            except QwenSourceReadError as error:
                code = (
                    error.code
                    if error.code in _QWEN_IDENTITY_BLOCKERS
                    else "qwen_runtime_unavailable"
                )
                raise SourceExecutionBlockedError(code) from None
            openai = OpenAISourceWitnessProvider(session)

            def image(region: SourceLayoutRegion, purpose: Purpose, dpi: int) -> SourceRegionInput:
                frame = renderer.crop(region.bounds, dpi)
                return SourceRegionInput(
                    source=request.source,
                    region=region,
                    image_png=frame.crop.png,
                    image_sha256=frame.crop.sha256,
                    render_dpi=dpi,
                    render_metadata=frame.render.metadata,
                    purpose=purpose,
                    language_hint=layout.language,
                )

            def read(
                reader: Literal["qwen", "openai"], value: SourceRegionInput
            ) -> IndependentReading:
                begin(reader, value)
                try:
                    result = qwen.read(value) if reader == "qwen" else openai.read(value)
                except SourceWitnessRecordingError:
                    raise
                except Exception as error:
                    if "requested" not in emitted:
                        emit("requested", dispatch_skipped=True)
                    if "failed" not in emitted:
                        code = getattr(error, "code", None)
                        emit(
                            "failed",
                            failure_code=str(getattr(code, "value", code) or type(error).__name__),
                        )
                    if isinstance(error, QwenSourceReadError) and (
                        error.code in _QWEN_IDENTITY_BLOCKERS or error.code == "qwen_unavailable"
                    ):
                        blocker = (
                            error.code
                            if error.code in _QWEN_IDENTITY_BLOCKERS
                            else "qwen_runtime_unavailable"
                        )
                        raise SourceExecutionBlockedError(
                            blocker, accounting=session.accounting()
                        ) from None
                    raise
                emit(
                    "parsed",
                    reading=result.model_dump(mode="json"),
                    reading_fingerprint=result.fingerprint,
                )
                return result

            try:
                layout_input = SourceRegionInput(
                    source=request.source,
                    region=SourceLayoutRegion(
                        key="page_layout",
                        kind="paragraph",
                        reading_order=0,
                        parent_key=None,
                        bounds=RegionBounds(left=0.0, top=0.0, right=1.0, bottom=1.0),
                    ),
                    image_png=request.image_png,
                    image_sha256=request.source.image_sha256,
                    render_dpi=300,
                    render_metadata=base.metadata,
                    purpose="unknown",
                    language_hint="und",
                )
                begin("layout", layout_input)
                proposed = session.read(
                    SourceLayout,
                    crop_source_image(request.image_png, layout_input.region.bounds),
                    kind="layout",
                    region_key=None,
                    instructions=_LAYOUT_RULES,
                )
                geometry = detect_source_geometry(request.image_png, dpi=300)
                layout = _geometry_first_layout(proposed, geometry)
                engine = SourceConsensusEngine(
                    max_pairs=max(1, pipeline.max_requests - 1),
                    max_rereads=pipeline.max_region_rereads,
                    timeout_ms=pipeline.total_timeout_ms,
                )
                machine = engine.read_page(
                    request.source,
                    layout,
                    geometry,
                    image,
                    lambda value: read("qwen", value),
                    lambda value: read("openai", value),
                )
            except SourceWitnessRecordingError:
                raise UnderstandingProviderError(
                    ProviderFailureCode.UNAVAILABLE, accounting=session.accounting()
                ) from None
            accounting = session.accounting()
            if accounting is None:
                raise UnderstandingProviderError(ProviderFailureCode.INVALID_RESPONSE)
            return UnderstandingProviderResult(
                source=request.source,
                profile=request.profile,
                content=machine.content.as_legacy_envelope(),
                machine=machine,
                accounting=accounting,
            )
