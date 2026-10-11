"""Pulls plain text out of uploaded resumes / cover letters."""
import io


def extract_text(upload):
    name = (upload.filename or "").lower()
    data = upload.read()
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages).strip()
    if name.endswith(".docx"):
        import docx
        d = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in d.paragraphs).strip()
    if name.endswith((".txt", ".md")):
        return data.decode("utf-8", errors="ignore").strip()
    raise ValueError("Upload a PDF, Word (.docx) or text file.")
