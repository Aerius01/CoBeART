"""JSON codec shared by the Socket.IO producers."""
import json
from typing import Any


class StrictJson:
    """JSON codec for socketio.Client(json=StrictJson): NaN or infinity raises instead of reaching the hub."""

    @staticmethod
    def dumps(obj: object, **kwargs: Any) -> str:
        return json.dumps(obj, allow_nan=False, **kwargs)

    @staticmethod
    def loads(s: str | bytes, **kwargs: Any) -> Any:
        return json.loads(s, **kwargs)
