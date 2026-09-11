"""Place the conversation in free screen space without moving the character."""

from PyQt6.QtCore import QRect


def detail_rect(screen: QRect, pet: QRect, source: QRect) -> QRect:
    area = screen.adjusted(12, 12, -12, -12)
    preferred_w, preferred_h = 680, 560
    if not pet.isValid() or not area.intersects(pet):
        w, h = min(preferred_w, area.width()), min(preferred_h, area.height())
        center = source.center() if source.isValid() else area.center()
        return QRect(max(area.left(), min(center.x() - w // 2, area.right() - w + 1)),
                     max(area.top(), min(center.y() - h // 2, area.bottom() - h + 1)), w, h)
    gap = 16
    reserved = pet.adjusted(-gap, -gap, gap, gap).intersected(area)
    regions = [
        ("above", QRect(area.left(), area.top(), area.width(), reserved.top() - area.top())),
        ("left", QRect(area.left(), area.top(), reserved.left() - area.left(), area.height())),
        ("right", QRect(reserved.right() + 1, area.top(), area.right() - reserved.right(), area.height())),
        ("below", QRect(area.left(), reserved.bottom() + 1, area.width(), area.bottom() - reserved.bottom())),
    ]
    viable = [(side, region) for side, region in regions if region.width() >= 420 and region.height() >= 300]
    if not viable:
        viable = [(side, region) for side, region in regions if region.isValid()]
    if not viable:
        # Pathological screen smaller than the character: remain on-screen.
        return area
    # Full-size candidates preserve order (above first); otherwise favor the
    # most readable free area. Shrinking the panel never displaces the pet.
    side, region = max(viable, key=lambda item: min(preferred_w, item[1].width())
                       * min(preferred_h, item[1].height()))
    w, h = min(preferred_w, region.width()), min(preferred_h, region.height())
    if side in {"above", "below"}:
        x = max(region.left(), min(pet.center().x() + 42 - w, region.right() - w + 1))
        y = region.bottom() - h + 1 if side == "above" else region.top()
    else:
        x = region.right() - w + 1 if side == "left" else region.left()
        y = max(region.top(), min(pet.bottom() - h + 1, region.bottom() - h + 1))
    return QRect(x, y, w, h)
