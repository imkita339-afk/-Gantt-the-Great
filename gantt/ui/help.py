"""Руководство пользователя внутри программы: оглавление, поиск, картинки, сохранение в PDF для печати и рассылки."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QMarginsF, QSizeF, Qt, QTimer, QUrl
from PySide6.QtGui import (QFont, QImage, QImageReader, QKeySequence, QPageLayout, QPageSize, QShortcut, QTextCursor,
                           QTextDocument)
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QSplitter, QTextBrowser, QVBoxLayout)

from .. import APP_NAME
from ..install import short_version
from ..paths import resource_path

GUIDE = ("docs", "guide.md")


def guide_path() -> Path:
    return resource_path(*GUIDE)


class HelpWindow(QDialog):
    """Не модальное окно: можно читать и тут же пробовать в программе."""

    def __init__(self, parent=None, path: Path | None = None):
        super().__init__(parent)
        self.path = path or guide_path()
        self.setWindowTitle(f"Руководство — {APP_NAME} {short_version()}")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1100, 760)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти в руководстве (Enter — дальше)")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self.find_next)
        self.search.textChanged.connect(lambda _t: self.find_next(restart=True))
        top.addWidget(self.search, 1)
        pdf = QPushButton("Сохранить в PDF…")
        pdf.setToolTip("Для печати или чтобы отправить коллегам")
        pdf.clicked.connect(self.save_pdf)
        top.addWidget(pdf)
        lay.addLayout(top)
        split = QSplitter()
        self.toc = QListWidget()
        self.toc.setMinimumWidth(230)
        self.toc.itemClicked.connect(self._go)
        self.toc.itemActivated.connect(self._go)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)        # ссылки разбираем сами: внешние — в браузер, #раздел — здесь
        self.view.anchorClicked.connect(self._link)
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() + 1)
        self.view.document().setDefaultFont(font)
        self.view.document().setDocumentMargin(18)
        self._refit = QTimer(self)      # картинки — по ширине текста, когда она установилась
        self._refit.setSingleShot(True)
        self._refit.setInterval(40)
        self._refit.timeout.connect(lambda: self.fit_images(self.view.document(), self.view.viewport().width() - 60))
        self.view.viewport().installEventFilter(self)
        split.addWidget(self.toc)
        split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 840])
        lay.addWidget(split, 1)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self.search.setFocus)
        QShortcut(QKeySequence("F3"), self, activated=self.find_next)
        self.load()

    def load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            text = f"# Руководство не найдено\n\nФайл {self.path} отсутствует."
        doc = self.view.document()
        doc.setBaseUrl(QUrl.fromLocalFile(str(self.path.parent) + "/"))   # картинки — рядом с руководством
        self.view.setSearchPaths([str(self.path.parent)])
        self.view.setMarkdown(text)
        self._sizes: dict[str, int] = {}
        self._refit.start()
        self._fill_toc()

    def natural_width(self, name: str) -> int:
        if name not in self._sizes:
            size = QImageReader(str(self.path.parent / name)).size()
            self._sizes[name] = size.width() if size.isValid() else 0
        return self._sizes[name]

    def fit_images(self, doc: QTextDocument, max_w: int) -> None:
        """Снимки окон шире колонки текста — уменьшить по ширине (высота — пропорционально)."""
        max_w = max(240, max_w)
        cur = QTextCursor(doc)
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                fmt = frag.charFormat()
                if fmt.isImageFormat():
                    img = fmt.toImageFormat()
                    natural = self.natural_width(img.name())
                    if natural:
                        img.setWidth(min(natural, max_w))
                        cur.setPosition(frag.position())
                        cur.setPosition(frag.position() + frag.length(), QTextCursor.MoveMode.KeepAnchor)
                        cur.setCharFormat(img)
                it += 1
            block = block.next()

    @staticmethod
    def _image_names(doc: QTextDocument) -> list[str]:
        names = []
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fmt = it.fragment().charFormat()
                if fmt.isImageFormat():
                    names.append(fmt.toImageFormat().name())
                it += 1
            block = block.next()
        return names

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            self._refit.start()
        return False

    def _fill_toc(self) -> None:
        self.toc.clear()
        block = self.view.document().begin()
        while block.isValid():
            level = block.blockFormat().headingLevel()
            if 1 <= level <= 3 and block.text().strip():
                it = QListWidgetItem(("    " * (level - 2) if level > 1 else "") + block.text().strip())
                it.setData(Qt.ItemDataRole.UserRole, block.position())
                if level <= 2:
                    f = it.font()
                    f.setWeight(QFont.Weight.DemiBold)
                    it.setFont(f)
                self.toc.addItem(it)
            block = block.next()

    def headings(self) -> list[str]:
        return [self.toc.item(i).text().strip() for i in range(self.toc.count())]

    def _go(self, item: QListWidgetItem) -> None:
        self.scroll_to(item.data(Qt.ItemDataRole.UserRole))

    def scroll_to(self, pos: int) -> None:
        """Заголовок — к верху окна, а не куда-нибудь в середину."""
        block = self.view.document().findBlock(pos)
        cur = QTextCursor(block)
        self.view.setTextCursor(cur)
        top = self.view.document().documentLayout().blockBoundingRect(block).top()
        self.view.verticalScrollBar().setValue(int(top))

    def open_section(self, title: str) -> bool:
        """Открыть на разделе, заголовок которого начинается с title."""
        for i in range(self.toc.count()):
            if self.toc.item(i).text().strip().lower().startswith(title.lower()):
                self.toc.setCurrentRow(i)
                self._go(self.toc.item(i))
                return True
        return False

    def _link(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(url)
        elif url.hasFragment():
            self.open_section(url.fragment().replace("-", " "))

    def find_next(self, restart: bool = False) -> None:
        text = self.search.text().strip()
        if not text:
            return
        if restart:
            self.view.moveCursor(QTextCursor.MoveOperation.Start)
        if not self.view.find(text):
            self.view.moveCursor(QTextCursor.MoveOperation.Start)
            self.view.find(text)

    def save_pdf(self, path: str | None = None) -> bool:
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Сохранить руководство",
                                                  f"Руководство {APP_NAME} {short_version()}.pdf", "PDF (*.pdf)")
            if not path:
                return False
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        printer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
                                          QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter))
        # свой документ: у копии экранного пропадают картинки. Лист — в точках экрана (96 на дюйм):
        # тогда снимки окон и текст соотносятся так же, как на экране, а Qt сам масштабирует до листа
        doc = QTextDocument(self)
        doc.setDefaultFont(QFont(self.font().family(), 10))
        doc.setDocumentMargin(0)
        base = QUrl.fromLocalFile(str(self.path.parent) + "/")
        doc.setBaseUrl(base)
        doc.setMarkdown(self.path.read_text(encoding="utf-8"))
        for name in self._image_names(doc):
            doc.addResource(QTextDocument.ResourceType.ImageResource, base.resolved(QUrl(name)),
                            QImage(str(self.path.parent / name)))
        page = printer.pageLayout().paintRect(QPageLayout.Unit.Point)
        size = QSizeF(page.width() * 96 / 72, page.height() * 96 / 72)
        doc.setPageSize(size)
        self.fit_images(doc, int(size.width() * 0.8))
        doc.print_(printer)
        if not Path(path).exists():
            QMessageBox.warning(self, "Руководство", f"Не удалось сохранить {path}")
            return False
        return True


def show_help(win, section: str | None = None) -> HelpWindow:
    dlg = getattr(win, "_help", None)
    if dlg is None:
        dlg = win._help = HelpWindow(win)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    if section:
        dlg.open_section(section)
    return dlg
