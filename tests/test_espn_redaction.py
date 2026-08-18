"""Credentials must not survive a trip through a log line or an error message.

These are the cheapest tests in the suite and they guard the most expensive
mistake: `espn_s2` is a live session credential for somebody's ESPN account,
and the realistic way one escapes is an exception carrying a request URL.
"""

from __future__ import annotations

import logging

from app.espn.redaction import (
    RedactingFilter,
    install_log_redaction,
    redact,
    redact_headers,
    redact_url,
)

SWID = "{1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D}"
S2 = "AEB" + "x9Kq2Lm" * 40  # espn_s2 is a few hundred characters of base64


class TestRedact:
    def test_a_swid_guid_is_removed(self):
        assert SWID not in redact(f"member id {SWID} owns team 4")

    def test_a_bare_guid_is_removed_too(self):
        # ESPN member ids appear without braces in some payloads.
        bare = SWID.strip("{}")
        assert bare not in redact(f"owner={bare}")

    def test_an_espn_s2_value_is_removed(self):
        assert S2 not in redact(f"Cookie: espn_s2={S2}; SWID={SWID};")

    def test_an_unnamed_long_token_is_still_removed(self):
        # A value that reached a message without its name attached.
        assert S2 not in redact(f"rejected token {S2}")

    def test_a_named_assignment_survives_as_a_label(self):
        result = redact(f"espn_s2={S2}")
        assert result.startswith("espn_s2=")
        assert S2 not in result

    def test_ordinary_text_is_untouched(self):
        assert redact("Could not reach ESPN: timed out") == (
            "Could not reach ESPN: timed out"
        )

    def test_status_keys_are_not_mangled(self):
        # `swid_set` must not be mistaken for a `swid=` assignment, or every
        # status payload would come back unreadable.
        assert redact('{"swid_set": false, "espn_s2_set": true}') == (
            '{"swid_set": false, "espn_s2_set": true}'
        )

    def test_none_and_empty_are_safe(self):
        assert redact(None) == ""
        assert redact("") == ""

    def test_exceptions_are_accepted_directly(self):
        error = RuntimeError(f"denied for SWID={SWID}")
        assert SWID not in redact(error)


class TestRedactHeaders:
    def test_cookie_headers_are_replaced_wholesale(self):
        headers = {"Cookie": f"espn_s2={S2}; SWID={SWID};", "Accept": "application/json"}
        safe = redact_headers(headers)
        assert safe["Cookie"] == "[REDACTED]"
        assert safe["Accept"] == "application/json"

    def test_header_names_are_matched_case_insensitively(self):
        assert redact_headers({"cookie": "x"})["cookie"] == "[REDACTED]"
        assert redact_headers({"Authorization": "Bearer x"})["Authorization"] == "[REDACTED]"

    def test_no_headers_is_an_empty_dict(self):
        assert redact_headers(None) == {}


class TestRedactUrl:
    def test_a_swid_in_the_path_is_removed(self):
        # The fan endpoint puts the credential in the path, so stripping the
        # query string alone would not be enough.
        url = f"https://fan.api.espn.com/apis/v2/fans/{SWID}?lang=en"
        assert SWID not in redact_url(url)
        assert "fan.api.espn.com" in redact_url(url)


class TestLoggingFilter:
    def test_the_filter_scrubs_a_record(self, caplog):
        logger = logging.getLogger("test.redaction")
        caplog.handler.addFilter(RedactingFilter())
        with caplog.at_level(logging.WARNING, logger="test.redaction"):
            logger.warning("failed for %s", SWID)
        assert SWID not in caplog.text
        assert "failed for" in caplog.text

    def test_installing_is_idempotent(self):
        root = logging.getLogger()
        handler = logging.StreamHandler()
        root.addHandler(handler)
        try:
            install_log_redaction()
            install_log_redaction()
            filters = [f for f in handler.filters if isinstance(f, RedactingFilter)]
            assert len(filters) == 1
        finally:
            root.removeHandler(handler)
