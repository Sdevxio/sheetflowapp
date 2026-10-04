class ImportProcessingError(Exception):
    """A failure the user can act on. The message must not include cell values."""


class WorkbookReadError(Exception):
    """The file is not a readable .xls or .xlsx workbook."""
