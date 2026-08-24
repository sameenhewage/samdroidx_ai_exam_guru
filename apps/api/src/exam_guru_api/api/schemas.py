from pydantic import BaseModel, ConfigDict, JsonValue


class ApiErrorDetail(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str


class ApiErrorResponse(BaseModel):
    detail: ApiErrorDetail | list[dict[str, JsonValue]]
