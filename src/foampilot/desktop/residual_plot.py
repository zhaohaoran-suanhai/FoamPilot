"""Small PySide6-Essentials residual plot with no optional chart dependency."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .viewmodels import ResidualSample


_COLORS = (
    QColor("#2563eb"),
    QColor("#dc2626"),
    QColor("#059669"),
    QColor("#9333ea"),
    QColor("#ea580c"),
    QColor("#0891b2"),
    QColor("#4f46e5"),
    QColor("#be123c"),
)


class ResidualPlot(QWidget):
    """Plot initial residual histories on a logarithmic vertical scale."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._samples: tuple[ResidualSample, ...] = ()
        self.setMinimumHeight(260)
        self.setObjectName("residual_plot")

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.field for item in self._samples))

    def set_samples(self, samples: tuple[ResidualSample, ...]) -> None:
        self._samples = tuple(samples)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), self.palette().base())
        if not self._samples:
            painter.setPen(self.palette().placeholderText().color())
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "等待 OpenFOAM residual 数据",
            )
            return

        left, top, right, bottom = 62.0, 20.0, 18.0, 42.0
        plot = QRectF(
            left,
            top,
            max(1.0, self.width() - left - right),
            max(1.0, self.height() - top - bottom),
        )
        values = [
            math.log10(item.initial_residual)
            for item in self._samples
            if item.initial_residual > 0
            and math.isfinite(item.initial_residual)
        ]
        if not values:
            painter.setPen(self.palette().placeholderText().color())
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "residual 数据不可用",
            )
            return
        minimum = min(values)
        maximum = max(values)
        if math.isclose(minimum, maximum):
            minimum -= 1.0
            maximum += 1.0

        painter.setPen(QPen(self.palette().mid().color(), 1.0))
        painter.drawLine(plot.bottomLeft(), plot.topLeft())
        painter.drawLine(plot.bottomLeft(), plot.bottomRight())
        for index in range(5):
            fraction = index / 4
            y = plot.bottom() - fraction * plot.height()
            value = minimum + fraction * (maximum - minimum)
            painter.setPen(QPen(self.palette().midlight().color(), 1.0))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(self.palette().text().color())
            painter.drawText(
                QRectF(2.0, y - 9.0, left - 8.0, 18.0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"1e{value:.1f}",
            )

        count = max(1, len(self._samples) - 1)
        by_field: dict[str, list[tuple[int, ResidualSample]]] = {}
        for index, sample in enumerate(self._samples):
            by_field.setdefault(sample.field, []).append((index, sample))
        for field_index, (field, series) in enumerate(by_field.items()):
            color = _COLORS[field_index % len(_COLORS)]
            painter.setPen(QPen(color, 2.0))
            previous: QPointF | None = None
            for index, sample in series:
                x = plot.left() + (index / count) * plot.width()
                log_value = math.log10(sample.initial_residual)
                y = plot.bottom() - (
                    (log_value - minimum) / (maximum - minimum)
                ) * plot.height()
                point = QPointF(x, y)
                if previous is not None:
                    painter.drawLine(previous, point)
                painter.drawEllipse(point, 2.2, 2.2)
                previous = point
            legend_x = plot.left() + field_index * 118.0
            painter.fillRect(QRectF(legend_x, 2.0, 12.0, 4.0), color)
            painter.setPen(self.palette().text().color())
            painter.drawText(
                QRectF(legend_x + 16.0, -5.0, 98.0, 18.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                field,
            )
        painter.setPen(self.palette().text().color())
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 12.0, plot.width(), 22.0),
            Qt.AlignmentFlag.AlignCenter,
            "log sample sequence",
        )


__all__ = ["ResidualPlot"]
