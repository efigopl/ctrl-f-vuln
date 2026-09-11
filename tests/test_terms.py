"""Highlight-term extraction from stored search queries."""

from __future__ import annotations

from ctrlfvuln.terms import extract_terms, extract_terms_from_queries


def test_qualifiers_are_dropped():
    terms = extract_terms("mysql_query( language:php size:0..100 extension:php")
    assert terms == ["mysql_query("]


def test_quoted_phrases_stay_together():
    assert extract_terms('"eval(" unserialize(') == ["eval(", "unserialize("]


def test_boolean_words_and_short_tokens_are_dropped():
    assert extract_terms("strcpy AND OR a gets") == ["strcpy", "gets"]


def test_negated_qualifiers_are_dropped():
    assert extract_terms("eval( -language:markdown") == ["eval("]


def test_unbalanced_quotes_do_not_raise():
    assert extract_terms('eval( "unterminated') == ["eval(", "unterminated"]


def test_regex_form_keeps_its_body():
    """Backslashes survive, so a regex query still highlights something useful."""
    assert extract_terms("/strcpy\\s*\\(/") == ["strcpy\\s*\\("]


def test_empty_and_none_queries():
    assert extract_terms(None) == []
    assert extract_terms("") == []
    assert extract_terms("language:php") == []


def test_terms_are_deduped_case_insensitively_across_queries():
    terms = extract_terms_from_queries(
        ["mysql_query( language:php", "MYSQL_QUERY( size:0..10", "eval("]
    )
    assert terms == ["mysql_query(", "eval("]
