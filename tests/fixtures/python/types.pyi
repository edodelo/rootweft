from typing import Protocol

class Formatter(Protocol):
    def render(self, value: str) -> str: ...
