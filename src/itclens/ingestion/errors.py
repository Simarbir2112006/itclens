"""Errors raised while reading an uploaded file.

Every message is a fixed string. Nothing from the file (cell values, headers,
sheet names) or its name may appear in an error.
"""

FILE_TOO_LARGE = "FILE_TOO_LARGE"
UNSUPPORTED_XLS = "UNSUPPORTED_XLS"
UNREADABLE_FILE = "UNREADABLE_FILE"
NOT_A_TABLE = "NOT_A_TABLE"
EMPTY_FILE = "EMPTY_FILE"
ROW_LIMIT = "ROW_LIMIT"
COLUMN_LIMIT = "COLUMN_LIMIT"
SHEET_NOT_FOUND = "SHEET_NOT_FOUND"
HEADER_NOT_FOUND = "HEADER_NOT_FOUND"
BAD_MAPPING = "BAD_MAPPING"
MAPPING_INCOMPLETE = "MAPPING_INCOMPLETE"

MESSAGES = {
    FILE_TOO_LARGE: "The file is larger than the allowed size.",
    UNSUPPORTED_XLS: "Old .xls files are not supported. Save the file as .xlsx or CSV.",
    UNREADABLE_FILE: "The file could not be read as an Excel workbook or CSV file.",
    NOT_A_TABLE: "The file does not look like a table of text values.",
    EMPTY_FILE: "The file has no data.",
    ROW_LIMIT: "The file has more rows than allowed.",
    COLUMN_LIMIT: "The file has more columns than allowed.",
    SHEET_NOT_FOUND: "The selected sheet was not found in the workbook.",
    HEADER_NOT_FOUND: "No header row with recognisable column names was found.",
    BAD_MAPPING: "The column mapping is not valid.",
    MAPPING_INCOMPLETE: "Some required columns are not mapped.",
}


class IngestError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(MESSAGES[code])
        self.code = code
