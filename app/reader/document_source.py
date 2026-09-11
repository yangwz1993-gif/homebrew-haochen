"""Bound local document fallback. No directory traversal, shell, or network fetches.

Constructing a source reads metadata only. Bytes are opened only after consent.
Disk content is explicitly not an unsaved editor buffer or an historical snapshot.
"""
import base64
import io
import os
import stat
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".py", ".js", ".ts", ".tsx", ".jsx",
                 ".json", ".yaml", ".yml", ".toml", ".csv", ".log", ".swift", ".rs",
                 ".go", ".c", ".h", ".cpp", ".css", ".html", ".xml", ".sh"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_BYTES = 4 * 1024 * 1024


class DocumentSource:
    def __init__(self, uri):
        url = urlsplit(uri)
        if url.scheme != "file" or url.netloc not in ("", "localhost"):
            raise ValueError("不是本地文档地址")
        self.path = Path(unquote(url.path))
        if self.path.suffix.lower() not in TEXT_SUFFIXES | IMAGE_SUFFIXES | {".pdf"}:
            raise ValueError("此文档格式尚无文件读取适配器")
        self.identity = self._identity()

    def _identity(self):
        info = self.path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("文档不是普通文件或超过 4 MB")
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    def read(self):
        if self._identity() != self.identity:
            raise ValueError("等待授权期间文件已变化，请重新提问")
        with self.path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != self.identity:
                raise ValueError("文件来源已被替换，请重新提问")
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or self._identity() != self.identity:
            raise ValueError("读取期间文件已变化，本次内容已丢弃")
        if self.path.suffix.lower() == ".pdf":
            from Foundation import NSData
            from Quartz import PDFDocument
            document = PDFDocument.alloc().initWithData_(NSData.dataWithBytes_length_(data, len(data)))
            if document is None or document.isLocked():
                raise ValueError("PDF 损坏或需要解锁")
            if document.pageCount() > 100:
                raise ValueError("PDF 超过 100 页，请缩小阅读范围")
            text = "\n".join(str(document.pageAtIndex_(i).string() or "") for i in range(document.pageCount()))
            if not text.strip():
                raise ValueError("此 PDF 没有文字层，需要原窗口图像辅助阅读")
            return "text", text
        if self.path.suffix.lower() in IMAGE_SUFFIXES:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
                mime = Image.MIME.get(image.format)
            if mime not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                raise ValueError("不支持的图片格式")
            return "image", f"data:{mime};base64," + base64.b64encode(data).decode()
        return "text", data.decode("utf-8-sig")
