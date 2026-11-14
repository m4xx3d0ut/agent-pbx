from __future__ import annotations

from collections.abc import Callable
from typing import Any, Generic, Protocol, TypeVar, cast


class ControllerHost(Protocol):
    """Marker protocol for the Textual composition root owned by a controller."""


HostT = TypeVar("HostT", bound=ControllerHost)


class TUIController(Generic[HostT]):
    """Proxy shared TUI state while keeping orchestration outside ``tui.py``.

    Extracted methods may continue to use their established ``self`` attribute
    access. Public attributes are delegated to the host; controller-private
    attributes stay local when their name starts with an underscore.
    """

    def __init__(self, host: HostT) -> None:
        object.__setattr__(self, "_host", host)

    @property
    def host(self) -> HostT:
        return cast(HostT, object.__getattribute__(self, "_host"))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.host, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        setattr(self.host, name, value)


class _ControllerMethod:
    def __init__(self, controller_attribute: str, method_name: str) -> None:
        self.controller_attribute = controller_attribute
        self.method_name = method_name

    def __get__(
        self,
        instance: object | None,
        owner: type[object] | None = None,
    ) -> Callable[..., Any] | _ControllerMethod:
        if instance is None:
            return self
        controller = getattr(instance, self.controller_attribute)
        return cast(Callable[..., Any], getattr(controller, self.method_name))


def controller_method(
    controller_attribute: str,
    method_name: str,
) -> Any:
    """Expose one controller method through its legacy TUI method name."""

    return _ControllerMethod(controller_attribute, method_name)
