"""Stable keyed team rows: refreshes update data without replacing delegates."""

import copy
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel, QByteArray, QModelIndex, QObject, QPersistentModelIndex, Qt,
)


class TeamListModel(QAbstractListModel):
    FIELDS = ("record", "recordId", "label")

    def __init__(self, parent: QObject, key_fields: tuple[str, ...] = ("id",)):
        super().__init__(parent)
        self.key_fields = key_fields
        self.rows: list[dict[str, Any]] = []

    def key(self, row: dict[str, Any]) -> str:
        return ":".join(str(row.get(field, "")) for field in self.key_fields)

    def roleNames(self) -> dict[int, QByteArray]:
        return {int(Qt.ItemDataRole.UserRole) + i: QByteArray(name.encode())
                for i, name in enumerate(self.FIELDS)}

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index: QModelIndex | QPersistentModelIndex,
             role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        field = role - int(Qt.ItemDataRole.UserRole)
        return (row, self.key(row), str(row.get("label", "")))[field] if 0 <= field < 3 else None

    def sync(self, rows: list[dict[str, Any]]) -> None:
        for target, value in enumerate(rows):
            key = self.key(value)
            source = next((i for i in range(target, len(self.rows))
                           if self.key(self.rows[i]) == key), None)
            if source is None:
                self.beginInsertRows(QModelIndex(), target, target)
                self.rows.insert(target, copy.deepcopy(value))
                self.endInsertRows()
            else:
                if source != target:
                    self.beginMoveRows(QModelIndex(), source, source, QModelIndex(), target)
                    self.rows.insert(target, self.rows.pop(source))
                    self.endMoveRows()
                if self.rows[target] != value:
                    self.rows[target] = copy.deepcopy(value)
                    self.dataChanged.emit(self.index(target), self.index(target), list(self.roleNames()))
        if len(self.rows) > len(rows):
            self.beginRemoveRows(QModelIndex(), len(rows), len(self.rows) - 1)
            del self.rows[len(rows):]
            self.endRemoveRows()
