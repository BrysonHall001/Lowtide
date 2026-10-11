"""Tailored documents live as simple HTML, which the person can edit in the
browser. This module builds that HTML from the AI's output, cleans edited HTML,
and turns it into a Word file that looks like what they saw."""
import html as htmllib
import io
import re
from html.parser import HTMLParser

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

INK = RGBColor(0x1B, 0x2B, 0x3C)
ACCENT = RGBColor(0x2C, 0x6A, 0x80)
MUTED = RGBColor(0x5E, 0x6E, 0x7B)
RIGHT_EDGE = Inches(7.0)
FONT = "Calibri"

e = htmllib.escape

# ------------------------------------------------------------------ build HTML


def resume_html(r):
    out = []
    if r.get("name"):
        out.append(f'<p class="r-name">{e(r["name"])}</p>')
    if r.get("contact"):
        out.append(f'<p class="r-contact">{e(r["contact"])}</p>')
    if r.get("summary"):
        out.append(f'<p>{e(r["summary"])}</p>')
    for sec in r.get("sections") or []:
        out.append(f'<p class="r-sec">{e(sec.get("title") or "")}</p>')
        for en in sec.get("entries") or []:
            date = f'<span class="r-date">{e(en["dates"])}</span>' if en.get("dates") else ""
            out.append(f'<p class="r-entry"><b>{e(en.get("heading") or "")}</b>{date}</p>')
            if en.get("subheading") or en.get("location"):
                loc = f'<span class="r-date">{e(en["location"])}</span>' if en.get("location") else ""
                out.append(f'<p class="r-sub"><i>{e(en.get("subheading") or "")}</i>{loc}</p>')
            bullets = [b for b in en.get("bullets") or [] if b]
            if bullets:
                out.append("<ul>" + "".join(f"<li>{e(b)}</li>" for b in bullets) + "</ul>")
    if r.get("skills"):
        out.append('<p class="r-sec">Skills</p>')
        for s in r["skills"]:
            out.append(f'<p><b>{e(s.get("label") or "")}:</b> {e(s.get("items") or "")}</p>')
    return "\n".join(out)


def cover_html(name, text):
    out = [f'<p class="c-name">{e(name)}</p>'] if name else []
    for para in [x.strip() for x in (text or "").split("\n") if x.strip()]:
        out.append(f"<p>{e(para)}</p>")
    return "\n".join(out)


# ------------------------------------------------------------------ sanitize

_ALLOWED_TAGS = {"p", "div", "br", "b", "strong", "i", "em", "u", "ul", "ol", "li",
                 "span", "font", "h1", "h2", "h3"}
_VOID = {"br"}
_ALLOWED_CLASSES = {"r-name", "r-contact", "r-sec", "r-entry", "r-sub", "r-date", "c-name"}
_STYLE_PROPS = {"text-align", "font-weight", "font-style", "font-size", "font-family", "text-decoration",
                "text-decoration-line"}


def _clean_style(style):
    keep = []
    for part in (style or "").split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k, v = k.strip().lower(), v.strip()
        if k in _STYLE_PROPS and re.fullmatch(r"[\w\s,.'\"%-]+", v):
            keep.append(f"{k}: {v}")
    return "; ".join(keep)


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        if tag not in _ALLOWED_TAGS:
            return
        bits = [tag]
        for k, v in attrs:
            v = v or ""
            if k == "class":
                cls = " ".join(c for c in v.split() if c in _ALLOWED_CLASSES)
                if cls:
                    bits.append(f'class="{cls}"')
            elif k == "style":
                st = _clean_style(v)
                if st:
                    bits.append(f'style="{e(st)}"')
            elif k == "size" and tag == "font" and v.isdigit():
                bits.append(f'size="{v}"')
            elif k == "face" and tag == "font" and re.fullmatch(r"[\w\s,'\"-]+", v):
                bits.append(f'face="{e(v)}"')
        self.out.append("<" + " ".join(bits) + ">")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        if tag in _ALLOWED_TAGS and tag not in _VOID:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(e(data, quote=False))


def sanitize(html):
    p = _Sanitizer()
    p.feed(html or "")
    p.close()
    return "".join(p.out)[:400_000]


# ------------------------------------------------------------------ HTML -> Word

_FONT_SIZES = {1: 8, 2: 9, 3: 10.5, 4: 12, 5: 14, 6: 18, 7: 24}
_BLOCKS = {"p", "div", "li", "h1", "h2", "h3"}


def _px_or_pt(v):
    m = re.match(r"([\d.]+)\s*(px|pt|em|rem)?", v or "")
    if not m:
        return None
    n, unit = float(m.group(1)), (m.group(2) or "px")
    if unit == "pt":
        return n
    if unit in ("em", "rem"):
        return n * 10.5
    return n * 0.75


def _style_fmt(style):
    fmt = {}
    for part in (style or "").split(";"):
        if ":" not in part:
            continue
        k, v = [x.strip().lower() for x in part.split(":", 1)]
        if k == "font-weight":
            fmt["bold"] = v in ("bold", "bolder") or (v.isdigit() and int(v) >= 600)
        elif k == "font-style":
            fmt["italic"] = v == "italic"
        elif k in ("text-decoration", "text-decoration-line"):
            fmt["underline"] = "underline" in v
        elif k == "font-size":
            sz = _px_or_pt(v)
            if sz:
                fmt["size"] = sz
        elif k == "font-family":
            fmt["font"] = v.split(",")[0].strip("'\" ").title()
    return fmt


def _align(style):
    m = re.search(r"text-align\s*:\s*(\w+)", style or "")
    return m.group(1) if m else None


class _Block:
    def __init__(self, kind="p", classes=(), align=None, list_type=None):
        self.kind, self.classes, self.align, self.list_type = kind, set(classes), align, list_type
        self.runs = []      # (text, fmt) ; text "\t" = tab, "\n" = line break
        self.has_tab = False


class _ToBlocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.cur = [], None
        self.fmt_stack = [{}]
        self.tag_stack = []
        self.lists = []
        self.block_class_stack = []

    def _fmt(self):
        out = {}
        for f in self.fmt_stack:
            out.update(f)
        return out

    def _flush(self):
        if self.cur and (self.cur.runs or self.cur.classes & {"r-sec"}):
            self.blocks.append(self.cur)
        self.cur = None

    def _ensure(self):
        if self.cur is None:
            classes = self.block_class_stack[-1] if self.block_class_stack else ()
            self.cur = _Block(classes=classes)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        style = a.get("style") or ""
        if tag in ("ul", "ol"):
            self._flush()
            self.lists.append(tag)
            return
        if tag == "br":
            self._ensure()
            self.cur.runs.append(("\n", self._fmt()))
            return
        if tag in _BLOCKS:
            self._flush()
            kind = "li" if tag == "li" else tag
            inherited = self.block_class_stack[-1] if self.block_class_stack else ()
            cls = classes or list(inherited)
            self.cur = _Block(kind=kind, classes=cls, align=_align(style),
                              list_type=self.lists[-1] if (tag == "li" and self.lists) else None)
            self.block_class_stack.append(cls)
            self.tag_stack.append(tag)
            self.fmt_stack.append(_style_fmt(style))
            return
        fmt = _style_fmt(style)
        if tag in ("b", "strong"):
            fmt["bold"] = True
        elif tag in ("i", "em"):
            fmt["italic"] = True
        elif tag == "u":
            fmt["underline"] = True
        elif tag == "font":
            if (a.get("size") or "").isdigit():
                fmt["size"] = _FONT_SIZES.get(int(a["size"]), 10.5)
            if a.get("face"):
                fmt["font"] = a["face"].split(",")[0].strip("'\" ")
        if "r-date" in classes:
            self._ensure()
            self.cur.runs.append(("\t", {}))
            self.cur.has_tab = True
            fmt["muted"] = True
        self.tag_stack.append(tag)
        self.fmt_stack.append(fmt)

    def handle_endtag(self, tag):
        if tag in ("ul", "ol"):
            self._flush()
            if self.lists:
                self.lists.pop()
            return
        if tag not in self.tag_stack:
            return
        while self.tag_stack:
            t = self.tag_stack.pop()
            self.fmt_stack.pop()
            if t in _BLOCKS:
                self._flush()
                if self.block_class_stack:
                    self.block_class_stack.pop()
            if t == tag:
                break

    def handle_data(self, data):
        if not data.strip() and (self.cur is None or not self.cur.runs):
            return
        data = re.sub(r"\s+", " ", data)
        self._ensure()
        self.cur.runs.append((data, self._fmt()))

    def close(self):
        super().close()
        self._flush()


def _rule(p):
    pPr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "2"), ("w:color", "2C6A80")):
        bottom.set(qn(k), v)
    bdr.append(bottom)
    pPr.append(bdr)


_CLASS_STYLE = {
    "r-name": dict(bold=True, size=20, align="center", after=0),
    "r-contact": dict(size=9.5, color=MUTED, align="center", after=6),
    "r-sec": dict(bold=True, size=11, color=ACCENT, before=8, after=3, rule=True, keep=True),
    "r-entry": dict(size=10.5, before=4, keep=True),
    "r-sub": dict(size=10, keep=True),
    "c-name": dict(bold=True, size=16, after=14),
}
_ALIGN = {"center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT,
          "justify": WD_ALIGN_PARAGRAPH.JUSTIFY, "left": WD_ALIGN_PARAGRAPH.LEFT}


def html_to_docx(html, kind="resume"):
    parser = _ToBlocks()
    parser.feed(html or "")
    parser.close()

    d = Document()
    sec = d.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    sec.left_margin = sec.right_margin = Inches(0.75)
    sec.top_margin = sec.bottom_margin = Inches(0.6 if kind == "resume" else 1.0)
    base_size = 10 if kind == "resume" else 11
    st = d.styles["Normal"]
    st.font.name, st.font.size, st.font.color.rgb = FONT, Pt(base_size), INK
    st.paragraph_format.space_after = Pt(0 if kind == "resume" else 10)
    if kind != "resume":
        st.paragraph_format.line_spacing = 1.15

    for b in parser.blocks:
        cs = {}
        for c in b.classes:
            cs.update(_CLASS_STYLE.get(c, {}))
        if b.kind == "li":
            p = d.add_paragraph(style="List Number" if b.list_type == "ol" else "List Bullet")
            p.paragraph_format.space_after = Pt(1)
        else:
            p = d.add_paragraph()
        pf = p.paragraph_format
        if "before" in cs:
            pf.space_before = Pt(cs["before"])
        if "after" in cs:
            pf.space_after = Pt(cs["after"])
        elif kind == "resume" and b.kind != "li":
            pf.space_after = Pt(3)
        if cs.get("keep"):
            pf.keep_with_next = True
        align = b.align or cs.get("align")
        if align in _ALIGN:
            p.alignment = _ALIGN[align]
        if cs.get("rule"):
            _rule(p)
        if b.kind in ("h1", "h2", "h3"):
            cs.setdefault("bold", True)
            cs.setdefault("size", {"h1": 18, "h2": 14, "h3": 12}[b.kind])
        if b.has_tab:
            pf.tab_stops.add_tab_stop(RIGHT_EDGE, WD_TAB_ALIGNMENT.RIGHT)

        runs = b.runs
        # trim leading/trailing spaces in the paragraph
        while runs and runs[0][0] not in ("\t", "\n") and not runs[0][0].strip():
            runs = runs[1:]
        for text, fmt in runs:
            if text == "\n":
                p.add_run().add_break(WD_BREAK.LINE)
                continue
            r = p.add_run(text)
            bold = fmt.get("bold", cs.get("bold"))
            if bold is not None:
                r.bold = bold
            if fmt.get("italic"):
                r.italic = True
            if fmt.get("underline"):
                r.underline = True
            size = fmt.get("size") or cs.get("size")
            if fmt.get("muted") and not fmt.get("size"):
                size = 9.5
            if size:
                r.font.size = Pt(size)
            if fmt.get("font"):
                r.font.name = fmt["font"]
            color = MUTED if fmt.get("muted") else cs.get("color")
            if color is not None:
                r.font.color.rgb = color

    buf = io.BytesIO()
    d.save(buf)
    buf.seek(0)
    return buf
