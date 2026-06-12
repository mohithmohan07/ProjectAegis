"""Custom ReportLab Flowables for richer, subject-appropriate diagrams.

These complement the linear `Flowchart` (flowchart.py) so the workbook can
choose the representation that actually fits a topic instead of forcing every
idea into a table or bullet list:

  • Venn      – compare/contrast two sets (characters, themes, regions, number
                systems). Two overlapping ellipses with unique + shared points.
  • Pyramid   – hierarchy / trophic levels / energy flow (ecological pyramids,
                tiers of administration).
  • Timeline  – chronological events (history eras, plot/narrative arc).
  • Cycle     – cyclical processes that loop back (water/nitrogen cycle, etc.).
  • Tree      – hierarchical / classification / decision trees (number systems,
                feudal structures, "which formula?" decisions).

Every flowable computes its own height in wrap() and draws onto the canvas in
draw(); none of them split across a page, so the refiner caps element counts
to keep each diagram within an A4 column.
"""
from __future__ import annotations

from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Flowable

from mathtext import draw_rich, rich_width, rich_wrap
from styles import CONTENT_W, PALETTE, FONT_REGULAR, FONT_BOLD, FONT_ITALIC, canvas_text_on_fill


def wrap_text(text: str, max_width: float, font_name: str, font_size: float,
              max_lines: int | None = None) -> list[str]:
    # Rich (super/subscript-aware) wrap; returns original substrings so the
    # canvas drawer can render real exponents instead of "x^2".
    return rich_wrap(text, max_width, font_name, font_size, max_lines)


def _arrow_down(c, x: float, y_top: float, y_bottom: float, color) -> None:
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(1.0)
    c.line(x, y_top, x, y_bottom + 2.2 * mm)
    p = c.beginPath()
    p.moveTo(x, y_bottom)
    p.lineTo(x - 1.7 * mm, y_bottom + 2.4 * mm)
    p.lineTo(x + 1.7 * mm, y_bottom + 2.4 * mm)
    p.close()
    c.drawPath(p, fill=1, stroke=0)


# ===================================================================== Timeline

class Timeline(Flowable):
    SPINE_X = 24 * mm
    DOT_R = 1.9 * mm
    GAP = 4 * mm
    PAD = 2.4 * mm
    DATE_FONT = (FONT_BOLD, 8.5)
    TITLE_FONT = (FONT_BOLD, 9.5)
    DETAIL_FONT = (FONT_REGULAR, 8.0)
    TITLE_LEADING = 11.5
    DETAIL_LEADING = 10.0

    def __init__(self, events: list[dict], width: float = CONTENT_W) -> None:
        super().__init__()
        self.events = events[:8]
        self.width = width
        self._compute()

    def wrap(self, aw: float, ah: float):
        target = min(aw, CONTENT_W)
        if abs(target - self.width) > 0.5:
            self.width = target
            self._compute()
        return self.width, self.height

    def _compute(self) -> None:
        self.text_x = self.SPINE_X + 6 * mm
        text_w = self.width - self.text_x - 2 * mm
        self._rows = []
        self.row_h = []
        for ev in self.events:
            t_lines = wrap_text(ev.get("title", ""), text_w, *self.TITLE_FONT, max_lines=2)
            d_lines = wrap_text(ev.get("detail", ""), text_w, *self.DETAIL_FONT, max_lines=3)
            h = self.PAD + max(1, len(t_lines)) * self.TITLE_LEADING
            if d_lines:
                h += len(d_lines) * self.DETAIL_LEADING
            h += self.PAD
            self.row_h.append(h)
            self._rows.append((ev.get("date", ""), t_lines, d_lines))
        self.height = sum(self.row_h) + self.GAP * max(0, len(self.events) - 1)

    def draw(self) -> None:
        c = self.canv
        c.setStrokeColor(PALETTE["teal"])
        c.setLineWidth(1.6)
        c.line(self.SPINE_X, 2 * mm, self.SPINE_X, self.height - 1 * mm)
        top = self.height
        for (date, t_lines, d_lines), h in zip(self._rows, self.row_h):
            dot_y = top - self.PAD - self.TITLE_FONT[1] * 0.5
            c.setFillColor(PALETTE["teal_dark"])
            c.circle(self.SPINE_X, dot_y, self.DOT_R, fill=1, stroke=0)
            c.setFillColor(PALETTE["white"])
            c.circle(self.SPINE_X, dot_y, self.DOT_R * 0.4, fill=1, stroke=0)

            if date:
                c.setFont(*self.DATE_FONT)
                c.setFillColor(PALETTE["teal_dark"])
                c.drawRightString(self.SPINE_X - 3.5 * mm, dot_y - 2.6, date)

            cursor = top - self.PAD
            for ln in t_lines:
                cursor -= self.TITLE_LEADING
                draw_rich(c, self.text_x, cursor + 2.5, ln, self.TITLE_FONT[0],
                          self.TITLE_FONT[1], PALETTE["ink"])
            if d_lines:
                for ln in d_lines:
                    cursor -= self.DETAIL_LEADING
                    draw_rich(c, self.text_x, cursor + 2, ln, self.DETAIL_FONT[0],
                              self.DETAIL_FONT[1], PALETTE["subhead_on_pale"])
            top -= h + self.GAP


# ====================================================================== Pyramid

class Pyramid(Flowable):
    BAND_H = 18 * mm
    LABEL_FONT = (FONT_BOLD, 9.5)
    DETAIL_FONT = (FONT_REGULAR, 7.8)
    LABEL_LEADING = 11.0
    DETAIL_LEADING = 9.0

    def __init__(self, levels: list[dict], width: float = CONTENT_W,
                 direction: str = "up") -> None:
        super().__init__()
        self.levels = levels[:6]
        self.width = width
        self.direction = direction  # "up" = apex on top (level[0] = apex)
        self._compute()

    def wrap(self, aw: float, ah: float):
        target = min(aw, CONTENT_W)
        self.width = target
        self._compute()
        return self.width, self.height

    def _compute(self) -> None:
        self.n = max(1, len(self.levels))
        self.height = self.n * self.BAND_H

    def draw(self) -> None:
        c = self.canv
        cx = self.width / 2
        base_w = self.width * 0.92
        min_w = base_w * 0.34
        n = self.n
        fills = [PALETTE["teal_dark"], PALETTE["teal"], PALETTE["violet"],
                 PALETTE["gold"], PALETTE["coral"], PALETTE["muted"]]

        def width_at(k: int) -> float:
            return min_w + (base_w - min_w) * (k / n)

        levels = self.levels if self.direction == "up" else list(reversed(self.levels))
        for i, lvl in enumerate(levels):
            band_top = self.height - i * self.BAND_H
            band_bottom = band_top - self.BAND_H
            tw = width_at(i)
            bw = width_at(i + 1)
            p = c.beginPath()
            p.moveTo(cx - tw / 2, band_top)
            p.lineTo(cx + tw / 2, band_top)
            p.lineTo(cx + bw / 2, band_bottom)
            p.lineTo(cx - bw / 2, band_bottom)
            p.close()
            c.setFillColor(fills[i % len(fills)])
            c.setStrokeColor(PALETTE["white"])
            c.setLineWidth(1.4)
            c.drawPath(p, fill=1, stroke=1)

            fill = fills[i % len(fills)]
            label_color, detail_color = canvas_text_on_fill(fill)
            usable = tw - 6 * mm  # text must fit the narrow (top) edge of the band
            label_lines = wrap_text(lvl.get("label", ""), max(usable, 30), *self.LABEL_FONT, max_lines=2)
            detail_lines = wrap_text(lvl.get("detail", ""), max(usable, 30), *self.DETAIL_FONT, max_lines=1)
            total_text_h = len(label_lines) * self.LABEL_LEADING + len(detail_lines) * self.DETAIL_LEADING
            cursor = (band_top + band_bottom) / 2 + total_text_h / 2
            for ln in label_lines:
                cursor -= self.LABEL_LEADING
                draw_rich(c, cx, cursor + 2.5, ln, self.LABEL_FONT[0], self.LABEL_FONT[1],
                          label_color, align="center")
            if detail_lines:
                for ln in detail_lines:
                    cursor -= self.DETAIL_LEADING
                    draw_rich(c, cx, cursor + 2, ln, self.DETAIL_FONT[0], self.DETAIL_FONT[1],
                              detail_color, align="center")


# ========================================================================= Venn

class Venn(Flowable):
    HEADER_H = 7 * mm
    SET_FONT = (FONT_BOLD, 9.5)
    ITEM_FONT = (FONT_REGULAR, 7.2)
    SHARED_FONT = (FONT_BOLD, 7.0)
    ITEM_LEADING = 9.0

    def __init__(self, data: dict, width: float = CONTENT_W) -> None:
        super().__init__()
        self.left_title = data.get("left_title", "A")
        self.right_title = data.get("right_title", "B")
        self.left = [str(x) for x in (data.get("left") or [])][:6]
        self.both = [str(x) for x in (data.get("both") or [])][:5]
        self.right = [str(x) for x in (data.get("right") or [])][:6]
        self.width = width
        self._compute()

    def wrap(self, aw: float, ah: float):
        target = min(aw, CONTENT_W)
        self.width = target
        self._compute()
        return self.width, self.height

    def _compute(self) -> None:
        self.rx = self.width * 0.30
        self.ry = min(self.width * 0.28, 128)
        side_w = self.rx * 0.88
        shared_w = self.width * 0.17
        max_lines = 1
        for items, w in ((self.left, side_w), (self.right, side_w), (self.both, shared_w)):
            for it in items:
                max_lines = max(max_lines, len(wrap_text(it, w, *self.ITEM_FONT, max_lines=2)))
        max_items = max(len(self.left), len(self.both), len(self.right), 1)
        body_h = 2 * self.ry
        needed = max_items * self.ITEM_LEADING * max_lines + 18
        self.body_h = max(body_h, needed)
        self.height = self.HEADER_H + self.body_h

    def _draw_region(self, c, items, font, cx_region, mid_y, max_w, color) -> None:
        lines_per_item: list[list[str]] = []
        for it in items:
            lines_per_item.append(wrap_text(it, max_w, *font, max_lines=2) or [""])
        block_h = sum(max(1, len(ls)) for ls in lines_per_item) * self.ITEM_LEADING
        cursor = mid_y + block_h / 2
        for lines in lines_per_item:
            for ln in lines:
                cursor -= self.ITEM_LEADING
                if ln:
                    draw_rich(c, cx_region, cursor, ln, font[0], font[1], color, align="center")

    def draw(self) -> None:
        c = self.canv
        mid_y = self.body_h / 2
        cx_left = self.width * 0.34
        cx_right = self.width * 0.66

        # headers
        draw_rich(c, cx_left, self.height - self.HEADER_H + 1.5, self.left_title,
                  self.SET_FONT[0], self.SET_FONT[1], PALETTE["teal_dark"], align="center")
        draw_rich(c, cx_right, self.height - self.HEADER_H + 1.5, self.right_title,
                  self.SET_FONT[0], self.SET_FONT[1], PALETTE["violet"], align="center")

        # Pale fills first (so text stays readable), then strong outlines so
        # both circles' boundaries remain visible through the overlap.
        lb = (cx_left - self.rx, mid_y - self.ry, cx_left + self.rx, mid_y + self.ry)
        rb = (cx_right - self.rx, mid_y - self.ry, cx_right + self.rx, mid_y + self.ry)
        c.setFillColor(PALETTE["teal_pale"])
        c.ellipse(*lb, fill=1, stroke=0)
        c.setFillColor(PALETTE["violet_pale"])
        c.ellipse(*rb, fill=1, stroke=0)
        # overlap shading: a vertical sliver of the right circle re-tinted
        c.saveState()
        try:
            c.setFillAlpha(0.45)
        except Exception:
            pass
        c.setFillColor(PALETTE["teal_pale"])
        path = c.beginPath()
        path.moveTo(cx_right - self.rx, mid_y)
        # crude lens: ellipse arc region approximated by the right circle's left half
        c.ellipse(cx_right - self.rx, mid_y - self.ry, cx_left + self.rx, mid_y + self.ry, fill=1, stroke=0)
        c.restoreState()
        c.setLineWidth(1.5)
        c.setStrokeColor(PALETTE["teal"])
        c.ellipse(*lb, fill=0, stroke=1)
        c.setStrokeColor(PALETTE["violet"])
        c.ellipse(*rb, fill=0, stroke=1)

        # region text
        self._draw_region(c, self.left, self.ITEM_FONT, self.width * 0.20, mid_y,
                           self.rx * 0.95, PALETTE["ink"])
        self._draw_region(c, self.right, self.ITEM_FONT, self.width * 0.80, mid_y,
                           self.rx * 0.95, PALETTE["ink"])
        self._draw_region(c, self.both, self.SHARED_FONT, self.width * 0.50, mid_y,
                           self.width * 0.195, PALETTE["teal_dark"])


# ========================================================================= Cycle

class Cycle(Flowable):
    BOX_RADIUS = 2.6 * mm
    PADDING_X = 5 * mm
    PADDING_Y = 3 * mm
    GAP = 6 * mm
    RETURN_X = 7 * mm
    LABEL_FONT = (FONT_BOLD, 9.5)
    DETAIL_FONT = (FONT_REGULAR, 8.0)
    LABEL_LEADING = 12.0
    DETAIL_LEADING = 10.0
    BOX_WIDTH_RATIO = 0.74

    def __init__(self, steps: list[dict], width: float = CONTENT_W) -> None:
        super().__init__()
        self.steps = steps[:6]
        self.width = width
        self._compute()

    def wrap(self, aw: float, ah: float):
        target = min(aw, CONTENT_W)
        if abs(target - self.width) > 0.5:
            self.width = target
            self._compute()
        return self.width, self.height

    def _compute(self) -> None:
        self.box_width = self.width * self.BOX_WIDTH_RATIO
        text_width = self.box_width - 2 * self.PADDING_X
        self._wrapped = []
        self.box_heights = []
        for step in self.steps:
            label_lines = wrap_text(step.get("label", ""), text_width, *self.LABEL_FONT, max_lines=2)
            detail_lines = wrap_text(step.get("detail", ""), text_width, *self.DETAIL_FONT, max_lines=2)
            inner = max(1, len(label_lines)) * self.LABEL_LEADING
            if detail_lines:
                inner += len(detail_lines) * self.DETAIL_LEADING
            self.box_heights.append(inner + 2 * self.PADDING_Y)
            self._wrapped.append((label_lines, detail_lines))
        self.height = sum(self.box_heights) + self.GAP * max(0, len(self.steps) - 1)

    def draw(self) -> None:
        c = self.canv
        # shift the column right so the return arrow has a lane on the left
        col_cx = self.RETURN_X + 4 * mm + self.box_width / 2
        if col_cx + self.box_width / 2 > self.width:
            col_cx = self.width - self.box_width / 2
        x_box = col_cx - self.box_width / 2
        box_top = self.height
        centers = []
        bottoms = []
        for i, (bh, (label_lines, detail_lines)) in enumerate(zip(self.box_heights, self._wrapped)):
            box_bottom = box_top - bh
            c.setFillColor(PALETTE["mint"])
            c.setStrokeColor(PALETTE["teal"])
            c.setLineWidth(0.9)
            c.roundRect(x_box, box_bottom, self.box_width, bh, self.BOX_RADIUS, fill=1, stroke=1)
            cursor = box_top - self.PADDING_Y
            for ln in label_lines:
                cursor -= self.LABEL_LEADING
                draw_rich(c, col_cx, cursor + 2.5, ln, self.LABEL_FONT[0], self.LABEL_FONT[1],
                          PALETTE["ink"], align="center")
            if detail_lines:
                for ln in detail_lines:
                    cursor -= self.DETAIL_LEADING
                    draw_rich(c, col_cx, cursor + 2, ln, self.DETAIL_FONT[0], self.DETAIL_FONT[1],
                              PALETTE["subhead_on_pale"], align="center")
            centers.append(box_top - bh / 2)
            bottoms.append(box_bottom)
            if i < len(self.steps) - 1:
                _arrow_down(c, col_cx, box_bottom, box_bottom - self.GAP, PALETTE["teal"])
            box_top -= bh + self.GAP

        # return arrow: from bottom box back up to the first box, on the left lane
        lane_x = self.RETURN_X
        first_cy = centers[0]
        last_cy = centers[-1]
        c.setStrokeColor(PALETTE["teal_dark"])
        c.setLineWidth(1.2)
        c.line(x_box, last_cy, lane_x, last_cy)
        c.line(lane_x, last_cy, lane_x, first_cy)
        c.setFillColor(PALETTE["teal_dark"])
        p = c.beginPath()
        p.moveTo(x_box, first_cy)
        p.lineTo(lane_x + 2.4 * mm, first_cy + 1.7 * mm)
        p.lineTo(lane_x + 2.4 * mm, first_cy - 1.7 * mm)
        p.close()
        c.drawPath(p, fill=1, stroke=0)


# ========================================================================== Tree

class Tree(Flowable):
    INDENT = 9 * mm
    VGAP = 2.4 * mm
    BOX_PAD_X = 2.6 * mm
    BOX_PAD_Y = 1.7 * mm
    LABEL_FONT = (FONT_BOLD, 9.0)
    DETAIL_FONT = (FONT_REGULAR, 7.8)
    BRANCH_FONT = (FONT_ITALIC, 7.3)
    LABEL_LEADING = 11.0
    DETAIL_LEADING = 9.4
    MAX_DEPTH = 3

    def __init__(self, root: dict, width: float = CONTENT_W, *,
                 max_depth: int | None = None, compact: bool = False) -> None:
        super().__init__()
        self.root = root or {}
        self.width = width
        if max_depth is not None:
            self.MAX_DEPTH = max_depth
        if compact:
            self.INDENT = 7 * mm
            self.VGAP = 1.8 * mm
            self.BOX_PAD_X = 2.2 * mm
            self.BOX_PAD_Y = 1.4 * mm
            self.LABEL_FONT = (FONT_BOLD, 8.2)
            self.DETAIL_FONT = (FONT_REGULAR, 7.2)
            self.BRANCH_FONT = (FONT_ITALIC, 6.8)
            self.LABEL_LEADING = 9.8
            self.DETAIL_LEADING = 8.4
        self._measure(self.root, 0)
        self.height = self._assign(self.root, 0.0)  # placeholder; recomputed in wrap

    def wrap(self, aw: float, ah: float):
        target = min(aw, CONTENT_W)
        self.width = target
        self._measure(self.root, 0)
        total = self._total_height(self.root)
        self.height = total
        self._assign(self.root, self.height)
        return self.width, self.height

    def _measure(self, node: dict, depth: int) -> None:
        x = depth * self.INDENT
        text_w = max(self.width - x - 2 * self.BOX_PAD_X - 6 * mm, 40)
        l_lines = wrap_text(node.get("label", ""), text_w, *self.LABEL_FONT, max_lines=2)
        d_lines = wrap_text(node.get("detail", ""), text_w, *self.DETAIL_FONT, max_lines=2)
        box_h = self.BOX_PAD_Y * 2 + max(1, len(l_lines)) * self.LABEL_LEADING
        if d_lines:
            box_h += len(d_lines) * self.DETAIL_LEADING
        # Span the full available width so wrapped lines stay inside the box.
        box_w = max(self.width - x - 2 * mm, 40)
        node["_l"] = dict(x=x, depth=depth, l_lines=l_lines, d_lines=d_lines,
                          box_h=box_h, box_w=box_w)
        if depth < self.MAX_DEPTH:
            for ch in (node.get("children") or []):
                self._measure(ch, depth + 1)
        else:
            node["children"] = []

    def _total_height(self, node: dict) -> float:
        h = node["_l"]["box_h"]
        for ch in (node.get("children") or []):
            h += self.VGAP + self._total_height(ch)
        return h

    def _assign(self, node: dict, top: float) -> float:
        node["_l"]["top"] = top
        y = top - node["_l"]["box_h"]
        for ch in (node.get("children") or []):
            y -= self.VGAP
            y = self._assign(ch, y)
        return y

    def _fill_for_depth(self, depth: int):
        palette = [
            PALETTE["teal_dark"],
            PALETTE["teal_pale"],
            PALETTE["mint"],
            PALETTE["white"],
            PALETTE["violet_pale"],
        ]
        return palette[min(depth, len(palette) - 1)]

    def _text_for_depth(self, depth: int):
        return PALETTE["white"] if depth == 0 else PALETTE["ink"]

    def draw(self) -> None:
        self._draw_node(self.root)

    def _draw_node(self, node: dict) -> None:
        c = self.canv
        L = node["_l"]
        x, top, bh, bw, depth = L["x"], L["top"], L["box_h"], L["box_w"], L["depth"]
        bottom = top - bh
        c.setFillColor(self._fill_for_depth(depth))
        c.setStrokeColor(PALETTE["teal"])
        c.setLineWidth(0.7)
        c.roundRect(x, bottom, bw, bh, 1.6 * mm, fill=1, stroke=1)

        cursor = top - self.BOX_PAD_Y
        label_color = self._text_for_depth(depth)
        for ln in L["l_lines"]:
            cursor -= self.LABEL_LEADING
            draw_rich(c, x + self.BOX_PAD_X, cursor + 2.4, ln, self.LABEL_FONT[0],
                      self.LABEL_FONT[1], label_color)
        if L["d_lines"]:
            detail_color = PALETTE["subhead_on_dark"] if depth == 0 else PALETTE["subhead_on_pale"]
            for ln in L["d_lines"]:
                cursor -= self.DETAIL_LEADING
                draw_rich(c, x + self.BOX_PAD_X, cursor + 2, ln, self.DETAIL_FONT[0],
                          self.DETAIL_FONT[1], detail_color)

        # branch/edge label (for decision trees) sits to the right of the box
        branch = node.get("branch", "")
        if branch and depth > 0:
            avail = self.width - (x + bw + 3 * mm)
            if avail > 18 * mm:
                line = wrap_text(branch, avail, *self.BRANCH_FONT, max_lines=1)
                if line:
                    draw_rich(c, x + bw + 3 * mm, top - bh / 2 - 2, line[0],
                              self.BRANCH_FONT[0], self.BRANCH_FONT[1], PALETTE["subhead_on_pale"])

        children = node.get("children") or []
        if not children:
            return
        spine_x = x + 3 * mm
        last_y = None
        for ch in children:
            cl = ch["_l"]
            cy = cl["top"] - cl["box_h"] / 2
            c.setStrokeColor(PALETTE["teal"])
            c.setLineWidth(0.7)
            c.line(spine_x, cy, cl["x"], cy)
            last_y = cy
        c.setStrokeColor(PALETTE["teal"])
        c.setLineWidth(0.7)
        c.line(spine_x, bottom, spine_x, last_y)
        for ch in children:
            self._draw_node(ch)
