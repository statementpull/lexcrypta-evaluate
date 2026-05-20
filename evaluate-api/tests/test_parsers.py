import io

from app.parsers.bank_parser import normalise_amount, parse_bank_csv_text
from app.parsers.file_detector import detect_file_type
from app.parsers.myob_parser import parse_myob_gl, parse_myob_pl


def test_detect_pdf():
    fake = io.BytesIO(b"%PDF-1.4 rest of content")
    assert detect_file_type(fake, "statement.pdf") == "bank_pdf"


def test_detect_csv_bank():
    content = b"Date,Description,Debit,Credit,Balance\n01/04/2026,SPORTSBET,50.00,,1000.00"
    assert detect_file_type(io.BytesIO(content), "bank.csv") == "bank_csv"


def test_detect_myob_gl():
    content = b"Account,Job,Date,Memo,Debit,Credit\n1-1100,,01/04/2026,Cash opening,,,\n"
    assert detect_file_type(io.BytesIO(content), "gl_export.csv") == "myob_gl"


def test_normalise_amount_debit():
    assert normalise_amount("50.00", "") == -50.0


def test_normalise_amount_credit():
    assert normalise_amount("", "200.00") == 200.0


def test_normalise_amount_both_empty():
    assert normalise_amount("", "") == 0.0


def test_parse_bank_csv_text():
    csv_text = "Date,Description,Debit,Credit,Balance\n15/04/2026,COINBASE INC,100.00,,900.00\n"
    txns = parse_bank_csv_text(csv_text)
    assert len(txns) == 1
    assert txns[0]["merchant"] == "COINBASE INC"
    assert txns[0]["amount"] == -100.0


def test_parse_bank_csv_skips_empty_rows():
    csv_text = "Date,Description,Debit,Credit,Balance\n,,,,\n15/04/2026,VALID,50.00,,950.00\n"
    txns = parse_bank_csv_text(csv_text)
    assert len(txns) == 1


def test_parse_myob_gl():
    csv_text = "Account,Job,Date,Memo,Debit,Credit\n1-1100,,15/04/2026,SPORTSBET PAYMENT,200.00,\n"
    rows = parse_myob_gl(csv_text)
    assert len(rows) == 1
    assert rows[0]["merchant"] == "SPORTSBET PAYMENT"
    assert rows[0]["amount"] == -200.0


def test_parse_myob_pl():
    csv_text = "Account,This Month,YTD,Budget\nRevenue - Consulting,50000,200000,180000\n"
    rows = parse_myob_pl(csv_text)
    assert len(rows) == 1
    assert rows[0]["account"] == "Revenue - Consulting"
    assert rows[0]["ytd"] == 200000.0
