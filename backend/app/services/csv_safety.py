"""Text a spreadsheet reads literally, never as a formula.

A cell that starts with one of `FORMULA_LEADERS` is evaluated as a formula when
the file is opened in a spreadsheet, so a description such as
``=HYPERLINK(...)`` runs as live content on the reader's machine (CSV
injection, CWE-1236). Every text column a person can type goes through
`csv_safe` before it reaches a file; amounts, dates and codes the server writes
itself cannot carry a payload and are left alone.

Lives in the services layer because more than one export writes CSV: the
transaction export and the accountant package must not each keep a copy of the
rule and drift apart.
"""

#: Characters a spreadsheet treats as the start of a formula.
FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: object) -> str:
    """`value` as text that a spreadsheet reads literally, never as a formula."""
    text = "" if value is None else str(value)
    return f"'{text}" if text[:1] in FORMULA_LEADERS else text
