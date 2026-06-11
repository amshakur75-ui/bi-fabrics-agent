from fabric_audit_agent.importers.csv import parse_csv


def test_simple_table():
    r = parse_csv("a,b,c\n1,2,3\n4,5,6")
    assert r["headers"] == ["a", "b", "c"]
    assert r["rows"][0] == {"a": "1", "b": "2", "c": "3"}
    assert len(r["rows"]) == 2


def test_quoted_commas_and_quotes():
    r = parse_csv('name,note\n"Smith, Jane","said ""hi"""')
    assert r["rows"][0]["name"] == "Smith, Jane"
    assert r["rows"][0]["note"] == 'said "hi"'


def test_crlf_and_bom():
    r = parse_csv("﻿x,y\r\n10,20\r\n")
    assert r["headers"] == ["x", "y"]
    assert r["rows"] == [{"x": "10", "y": "20"}]


def test_quoted_newline():
    r = parse_csv('a,b\n"line1\nline2",2')
    assert r["rows"][0]["a"] == "line1\nline2"
    assert r["rows"][0]["b"] == "2"


def test_blank_lines_and_trim():
    r = parse_csv("a,b\n  1 , 2 \n\n3,4\n")
    assert len(r["rows"]) == 2
    assert r["rows"][0] == {"a": "1", "b": "2"}


def test_ragged_rows_pad_empty():
    assert parse_csv("a,b,c\n1,2")["rows"][0] == {"a": "1", "b": "2", "c": ""}


def test_empty_and_whitespace_only():
    assert parse_csv("") == {"headers": [], "rows": []}
    assert parse_csv("   ") == {"headers": [], "rows": []}
