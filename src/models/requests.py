from __future__ import annotations

import time
from http import HTTPStatus
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from src.utils import consts


class LinkRequest(BaseModel):
    url: str
    max_timeout: Optional[int] = 60
    cmd: Optional[str] = "request.get"
    headers: Optional[Dict[str, str]] = None
    postData: Optional[Dict[str, Any]] = None


class ProtectionTriggeredError(Exception):
    pass


class Solution(BaseModel):
    url: str
    status: int
    cookies: list
    userAgent: str  # noqa: N815 # Ignore to preserve compatibility
    headers: dict[str, Any]
    response: str

    @classmethod
    def invalid(cls, url: str):
        """
        Return an empty Solution with default values.

        Useful for returning an error response.
        """
        return cls(
            url=url,
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
            cookies=[],
            userAgent="",
            headers={},
            response="",
        )


class LinkResponse(BaseModel):
    status: str = "ok"
    message: str
    solution: Solution
    startTimestamp: int  # noqa: N815 # Ignore to preserve compatibility
    endTimestamp: int = int(time.time() * 1000)  # noqa: N815 # Ignore to preserve compatibility
    version: str = consts.VERSION

    @classmethod
    def invalid(cls, url: str):
        """
        Return an invalid LinkResponse with default error values.

        This method is used to generate a response indicating an invalid request.
        """
        return cls(
            status="error",
            message="Invalid request",
            solution=Solution.invalid(url),
            startTimestamp=int(time.time() * 1000),
            endTimestamp=int(time.time() * 1000),
        )


class NoChromeExtensionError(Exception):
    """No chrome extension found."""
