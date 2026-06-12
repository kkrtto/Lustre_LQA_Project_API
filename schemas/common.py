from typing import Any, Dict

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: Dict[str, Any] = {}
