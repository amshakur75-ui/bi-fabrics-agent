"""Thorough test coverage for the SQL read-only guard — this is the project's largest new
attack surface (Phase 7 spec). Every category of dangerous SQL must be verified as blocked."""

import pytest

from fabric_audit_agent.query.sql_guard import (
    assert_read_only_sql,
    escape_sql_identifier,
    _strip_string_literals,
    _MAX_SQL_LENGTH,
    _MAX_SQL_ROWS,
)


# ---------------------------------------------------------------------------
# escape_sql_identifier
# ---------------------------------------------------------------------------

class TestEscapeSqlIdentifier:
    def test_simple_name(self):
        assert escape_sql_identifier("MyTable") == "[MyTable]"

    def test_name_with_spaces(self):
        assert escape_sql_identifier("My Table") == "[My Table]"

    def test_name_with_closing_bracket(self):
        assert escape_sql_identifier("T]able") == "[T]]able]"

    def test_name_with_multiple_brackets(self):
        assert escape_sql_identifier("a]b]c") == "[a]]b]]c]"

    def test_rejects_newline(self):
        with pytest.raises(ValueError, match="control character"):
            escape_sql_identifier("bad\nname")

    def test_rejects_carriage_return(self):
        with pytest.raises(ValueError, match="control character"):
            escape_sql_identifier("bad\rname")

    def test_rejects_tab(self):
        with pytest.raises(ValueError, match="control character"):
            escape_sql_identifier("bad\tname")

    def test_rejects_null_byte(self):
        with pytest.raises(ValueError, match="control character"):
            escape_sql_identifier("bad\x00name")

    def test_empty_name(self):
        assert escape_sql_identifier("") == "[]"

    def test_numeric_name(self):
        assert escape_sql_identifier(123) == "[123]"


# ---------------------------------------------------------------------------
# _strip_string_literals
# ---------------------------------------------------------------------------

class TestStripStringLiterals:
    def test_strips_simple_string(self):
        result = _strip_string_literals("SELECT * WHERE x = 'hello'")
        assert "'hello'" not in result
        assert "SELECT * WHERE x = " in result
        # The quotes themselves are preserved, contents replaced with spaces
        assert result.count("'") == 2

    def test_preserves_escaped_quote(self):
        result = _strip_string_literals("WHERE x = 'it''s fine'")
        # Doubled quote inside string is treated as escape, stays in-string
        assert "it" not in result
        assert "fine" not in result

    def test_preserves_semicolons_outside_strings(self):
        result = _strip_string_literals("SELECT 1; SELECT 2")
        assert ";" in result

    def test_strips_semicolons_inside_strings(self):
        result = _strip_string_literals("WHERE x = 'a;b'")
        # The semicolon inside the string should be replaced with a space
        assert result.count(";") == 0

    def test_empty_string_literal(self):
        result = _strip_string_literals("WHERE x = ''")
        assert result == "WHERE x = ''"

    def test_multiple_strings(self):
        result = _strip_string_literals("WHERE x = 'hello' AND y = 'world'")
        assert "hello" not in result
        assert "world" not in result
        # Structure (keyword positions) is preserved
        assert "WHERE x = " in result
        assert " AND y = " in result


# ---------------------------------------------------------------------------
# assert_read_only_sql — VALID queries (should PASS)
# ---------------------------------------------------------------------------

class TestAssertReadOnlySqlValid:
    def test_simple_select(self):
        sql = "SELECT * FROM MyTable"
        assert assert_read_only_sql(sql) == sql

    def test_select_with_where(self):
        sql = "SELECT col1, col2 FROM MyTable WHERE col1 > 5"
        assert assert_read_only_sql(sql) == sql

    def test_select_with_join(self):
        sql = "SELECT a.x, b.y FROM TableA a JOIN TableB b ON a.id = b.id"
        assert assert_read_only_sql(sql) == sql

    def test_select_with_subquery(self):
        sql = "SELECT * FROM (SELECT id, name FROM Users) sub"
        assert assert_read_only_sql(sql) == sql

    def test_select_with_cte(self):
        sql = "WITH cte AS (SELECT id FROM Users) SELECT * FROM cte"
        assert assert_read_only_sql(sql) == sql

    def test_select_with_aggregation(self):
        sql = "SELECT department, COUNT(*) AS cnt FROM Employees GROUP BY department HAVING COUNT(*) > 5"
        assert assert_read_only_sql(sql) == sql

    def test_select_with_string_literal_containing_keyword(self):
        sql = "SELECT * FROM Events WHERE message = 'DROP TABLE Users'"
        assert assert_read_only_sql(sql) == sql

    def test_select_with_string_containing_semicolon(self):
        sql = "SELECT * FROM Events WHERE message = 'a;b;c'"
        assert assert_read_only_sql(sql) == sql

    def test_select_with_order_by(self):
        sql = "SELECT name FROM Users ORDER BY name ASC"
        assert assert_read_only_sql(sql) == sql

    def test_select_top(self):
        sql = "SELECT TOP 100 * FROM MyTable"
        assert assert_read_only_sql(sql) == sql

    def test_select_distinct(self):
        sql = "SELECT DISTINCT department FROM Employees"
        assert assert_read_only_sql(sql) == sql

    def test_select_with_case(self):
        sql = "SELECT CASE WHEN x > 5 THEN 'high' ELSE 'low' END FROM T"
        assert assert_read_only_sql(sql) == sql

    def test_select_with_escaped_string(self):
        sql = "SELECT * FROM T WHERE name = 'it''s ok'"
        assert assert_read_only_sql(sql) == sql

    def test_select_with_offset_fetch(self):
        """OFFSET uses SET-like syntax but is part of SELECT -- must not false-positive."""
        sql = "SELECT * FROM T ORDER BY id OFFSET 10 ROWS FETCH NEXT 20 ROWS ONLY"
        assert assert_read_only_sql(sql) == sql

    def test_returns_original_sql(self):
        """Must return the original string, not the stripped version."""
        sql = "SELECT * FROM T WHERE x = 'hello world'"
        assert assert_read_only_sql(sql) is sql


# ---------------------------------------------------------------------------
# assert_read_only_sql — BLOCKED queries (should RAISE ValueError)
# ---------------------------------------------------------------------------

class TestAssertReadOnlySqlBlocked:
    # --- DDL ---
    def test_rejects_create_table(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("CREATE TABLE Evil (id INT)")

    def test_rejects_drop_table(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("DROP TABLE Users")

    def test_rejects_alter_table(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("ALTER TABLE Users ADD email VARCHAR(100)")

    def test_rejects_truncate(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("TRUNCATE TABLE Logs")

    # --- DML ---
    def test_rejects_insert(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("INSERT INTO Users (name) VALUES ('evil')")

    def test_rejects_update(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("UPDATE Users SET role = 'admin'")

    def test_rejects_delete(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("DELETE FROM Users WHERE id = 1")

    def test_rejects_merge(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("MERGE INTO Target USING Source ON Target.id = Source.id WHEN MATCHED THEN UPDATE SET name = Source.name")

    # --- SELECT INTO (creates a table) ---
    def test_rejects_select_into(self):
        with pytest.raises(ValueError, match="SELECT INTO"):
            assert_read_only_sql("SELECT * INTO NewTable FROM OldTable")

    # --- Dynamic SQL ---
    def test_rejects_exec(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("EXEC sp_who2")

    def test_rejects_execute(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("EXECUTE sp_who2")

    def test_rejects_exec_in_select(self):
        with pytest.raises(ValueError, match="EXEC"):
            assert_read_only_sql("SELECT * FROM Users WHERE id IN (EXEC('SELECT 1'))")

    # --- External access ---
    def test_rejects_openrowset(self):
        with pytest.raises(ValueError, match="external data access"):
            assert_read_only_sql("SELECT * FROM OPENROWSET('SQLOLEDB', 'server', 'SELECT 1')")

    def test_rejects_opendatasource(self):
        with pytest.raises(ValueError, match="external data access"):
            assert_read_only_sql("SELECT * FROM OPENDATASOURCE('SQLOLEDB', 'Data Source=srv').db.dbo.T")

    def test_rejects_openquery(self):
        with pytest.raises(ValueError, match="external data access"):
            assert_read_only_sql("SELECT * FROM OPENQUERY(LinkedServer, 'SELECT 1')")

    # --- System procedures ---
    def test_rejects_sp_procedure(self):
        with pytest.raises(ValueError, match="system procedure"):
            assert_read_only_sql("SELECT * FROM T WHERE x IN (SELECT sp_executesql('evil'))")

    def test_rejects_xp_procedure(self):
        with pytest.raises(ValueError, match="system procedure"):
            assert_read_only_sql("SELECT xp_cmdshell('whoami')")

    # --- Stacked statements ---
    def test_rejects_stacked_statements(self):
        with pytest.raises(ValueError, match="semicolons"):
            assert_read_only_sql("SELECT 1; DROP TABLE Users")

    def test_rejects_stacked_statements_with_leading_select(self):
        with pytest.raises(ValueError, match="semicolons"):
            assert_read_only_sql("SELECT 1; SELECT 2")

    # --- Privilege commands ---
    def test_rejects_grant(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("GRANT SELECT ON Users TO evil_user")

    def test_rejects_revoke(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("REVOKE SELECT ON Users FROM evil_user")

    # --- Tautologies ---
    def test_rejects_or_1_equals_1(self):
        with pytest.raises(ValueError, match="tautology"):
            assert_read_only_sql("SELECT * FROM Users WHERE id = 1 OR 1=1")

    def test_rejects_or_a_equals_a(self):
        with pytest.raises(ValueError, match="tautology"):
            assert_read_only_sql("SELECT * FROM Users WHERE id = 1 OR 'a'='a'")

    def test_rejects_or_true(self):
        with pytest.raises(ValueError, match="tautology"):
            assert_read_only_sql("SELECT * FROM Users WHERE id = 1 OR TRUE")

    def test_tautology_inside_string_literal_is_ok(self):
        """A tautology inside a string literal should NOT trigger the check."""
        sql = "SELECT * FROM T WHERE msg = 'OR 1=1'"
        assert assert_read_only_sql(sql) == sql

    # --- Length ---
    def test_rejects_oversized_query(self):
        with pytest.raises(ValueError, match="maximum length"):
            assert_read_only_sql("SELECT " + "x" * _MAX_SQL_LENGTH)

    # --- Non-SELECT starts ---
    def test_rejects_declare(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("DECLARE @x INT = 1; SELECT @x")

    def test_rejects_set_statement(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("SET NOCOUNT ON")

    def test_rejects_use_statement(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("USE master")

    def test_rejects_bulk_insert(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("BULK INSERT T FROM 'file.csv'")

    def test_rejects_backup(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("BACKUP DATABASE mydb TO DISK = 'backup.bak'")

    def test_rejects_waitfor(self):
        with pytest.raises(ValueError):
            assert_read_only_sql("WAITFOR DELAY '00:00:10'")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_max_length_is_10k(self):
        assert _MAX_SQL_LENGTH == 10_000

    def test_max_rows_is_100k(self):
        assert _MAX_SQL_ROWS == 100_000
