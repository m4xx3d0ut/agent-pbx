from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


_FUNCTION_KEY_SEQUENCES = {
    1: b"\x1bOP",
    2: b"\x1bOQ",
    3: b"\x1bOR",
    4: b"\x1bOS",
    5: b"\x1b[15~",
    6: b"\x1b[17~",
    7: b"\x1b[18~",
    8: b"\x1b[19~",
    9: b"\x1b[20~",
    10: b"\x1b[21~",
    11: b"\x1b[23~",
    12: b"\x1b[24~",
}


def function_key_sequence(number: int) -> bytes:
    try:
        return _FUNCTION_KEY_SEQUENCES[number]
    except KeyError as exc:
        raise ValueError(f"unsupported function key: F{number}") from exc


def normalized_key_names(values: Iterable[str]) -> set[str]:
    return {
        value.strip().lower().replace("_", "+")
        for value in values
        if value and value.strip()
    }


@dataclass(frozen=True)
class FunctionKeyPassthrough:
    child_key: str
    sequence: bytes
    source_name: str


@dataclass(frozen=True)
class FunctionKeyPassthroughMap:
    modifier: str = "shift"

    def translate(self, names: Iterable[str]) -> FunctionKeyPassthrough | None:
        modifier = self.modifier.strip().lower().replace("_", "+")
        for name in normalized_key_names(names):
            number = self._number_for(name, modifier)
            if number is not None:
                return FunctionKeyPassthrough(
                    child_key=f"f{number}",
                    sequence=function_key_sequence(number),
                    source_name=name,
                )
        return None

    @staticmethod
    def _number_for(name: str, modifier: str) -> int | None:
        semantic = re.fullmatch(r"([a-z]+)\+f(1[0-2]|[1-9])", name)
        if semantic and semantic.group(1) == modifier:
            return int(semantic.group(2))
        if modifier == "shift":
            alias = re.fullmatch(r"f(1[3-9]|2[0-4])", name)
            if alias:
                return int(alias.group(1)) - 12
        return None

