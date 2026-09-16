"""The OFX compatibility boundary hides only known dependency warnings."""

import warnings
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from bs4 import XMLParsedAsHTMLWarning

from app.services.import_service import parse_ofx


FIND_ALL_WARNING = (
    "Call to deprecated method findAll. (Replaced by find_all) "
    "-- Deprecated since version 4.0.0."
)


def test_known_ofx_warnings_are_scoped_to_the_parser():
    def parse(stream):
        warnings.warn_explicit(
            FIND_ALL_WARNING, DeprecationWarning, "ofxparse.py", 1, module="ofxparse.ofxparse"
        )
        warnings.warn_explicit(
            "XML parsed as HTML", XMLParsedAsHTMLWarning,
            "ofxparse.py", 2, module="ofxparse.ofxparse",
        )
        return SimpleNamespace(accounts=[])

    with patch("app.services.import_service.OfxParser.parse", side_effect=parse), \
         warnings.catch_warnings():
        warnings.simplefilter("error")
        assert parse_ofx(b"") == []
        # The same warning outside the parser must not inherit its filter.
        with pytest.raises(DeprecationWarning, match="findAll"):
            warnings.warn_explicit(
                FIND_ALL_WARNING, DeprecationWarning,
                "ofxparse.py", 1, module="ofxparse.ofxparse",
            )


@pytest.mark.parametrize("message,module", [
    ("a new parser deprecation", "ofxparse.ofxparse"),
    (FIND_ALL_WARNING, "unrelated_dependency"),
])
def test_unrelated_ofx_warnings_are_visible(message, module):
    def parse(stream):
        warnings.warn_explicit(message, DeprecationWarning, "parser.py", 1, module=module)
        return SimpleNamespace(accounts=[])

    with patch("app.services.import_service.OfxParser.parse", side_effect=parse), \
         pytest.warns(DeprecationWarning) as captured:
        assert parse_ofx(b"") == []
    assert len(captured) == 1
    assert str(captured[0].message) == message


def test_parser_errors_are_not_suppressed():
    error = ValueError("synthetic invalid OFX")
    with patch("app.services.import_service.OfxParser.parse", side_effect=error), \
         pytest.raises(ValueError) as raised:
        parse_ofx(b"")
    assert raised.value is error
