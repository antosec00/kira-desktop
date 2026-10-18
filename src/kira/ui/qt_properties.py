"""Read-only Qt properties with the Python descriptor types missing from Qt's stubs."""

from collections.abc import Callable
from typing import Literal, Protocol, Self, TypeVar, cast, overload

from PySide6.QtCore import QObject, Property, Signal

_Owner = TypeVar("_Owner", bound=QObject)
_Value = TypeVar("_Value")
_Value_co = TypeVar("_Value_co", covariant=True)


class QtProperty(Protocol[_Value_co]):
    @overload
    def __get__(self, instance: None, owner: type[QObject]) -> Self: ...

    @overload
    def __get__(self, instance: QObject, owner: type[QObject] | None = None) -> _Value_co: ...


def qt_property(
    qt_type: type | Literal["QVariantList", "QVariantMap"],
    *,
    notify: Signal | None = None,
    constant: bool = False,
) -> Callable[[Callable[[_Owner], _Value]], QtProperty[_Value]]:
    """Keep the real Qt Property and meta type while typing its Python getter."""
    def decorate(getter: Callable[[_Owner], _Value]) -> QtProperty[_Value]:
        # PySide accepts named Qt types, but its generated stub only lists type.
        descriptor = Property(cast(type, qt_type), getter, notify=notify, constant=constant)
        return cast(QtProperty[_Value], descriptor)

    return decorate
