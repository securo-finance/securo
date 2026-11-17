from urllib.parse import unquote

from app.core.content_disposition import content_disposition


def test_an_ascii_name_keeps_its_plain_form():
    assert content_disposition("attachment", "FAT-12.pdf") == (
        "attachment; filename=\"FAT-12.pdf\"; filename*=UTF-8''FAT-12.pdf"
    )


def test_a_non_latin_name_is_encodable_and_round_trips():
    value = content_disposition("inline", "Τιμ-7.pdf")
    value.encode("latin-1")  # what the server does with every header value
    assert 'filename="___-7.pdf"' in value
    assert unquote(value.split("filename*=UTF-8''")[1]) == "Τιμ-7.pdf"


def test_quotes_slashes_and_control_characters_cannot_break_out():
    value = content_disposition("attachment", 'a"b/c\\d\r\ne.pdf')
    assert value.count('"') == 2
    assert "\r" not in value and "\n" not in value
    assert 'filename="a-b-c-d--e.pdf"' in value
