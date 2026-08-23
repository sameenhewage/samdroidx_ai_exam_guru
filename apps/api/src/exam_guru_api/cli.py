import uvicorn


def main() -> None:
    uvicorn.run("exam_guru_api.main:app", host="0.0.0.0", port=8000)
