"""Thorough test coverage for the DAX read-only guard — this is the project's largest new
attack surface (Phase 7 spec). Every category of dangerous DAX/XMLA must be verified as blocked."""

import pytest

from fabric_audit_agent.query.dax_guard import (
    assert_read_only_dax,
    escape_dax_reference,
    _strip_string_literals,
    _MAX_DAX_LENGTH,
    _MAX_DAX_ROWS,
)


# ---------------------------------------------------------------------------
# escape_dax_reference
# ---------------------------------------------------------------------------

class TestEscapeDaxReference:
    def test_simple_name(self):
        assert escape_dax_reference("Sales") == "'Sales'"

    def test_name_with_spaces(self):
        assert escape_dax_reference("My Table") == "'My Table'"

    def test_name_with_single_quote(self):
        assert escape_dax_reference("It's a table") == "'It''s a table'"

    def test_name_with_multiple_quotes(self):
        assert escape_dax_reference("a'b'c") == "'a''b''c'"

    def test_rejects_newline(self):
        with pytest.raises(ValueError, match="control character"):
            escape_dax_reference("bad\nname")

    def test_rejects_carriage_return(self):
        with pytest.raises(ValueError, match="control character"):
            escape_dax_reference("bad\rname")

    def test_rejects_tab(self):
        with pytest.raises(ValueError, match="control character"):
            escape_dax_reference("bad\tname")

    def test_rejects_null_byte(self):
        with pytest.raises(ValueError, match="control character"):
            escape_dax_reference("bad\x00name")

    def test_empty_name(self):
        assert escape_dax_reference("") == "''"

    def test_numeric_name(self):
        assert escape_dax_reference(123) == "'123'"


# ---------------------------------------------------------------------------
# _strip_string_literals
# ---------------------------------------------------------------------------

class TestStripStringLiterals:
    def test_strips_double_quoted_string(self):
        result = _strip_string_literals('EVALUATE FILTER(T, [Col] = "hello")')
        assert "hello" not in result
        assert "EVALUATE FILTER(T, [Col] = " in result

    def test_preserves_single_quoted_table_ref(self):
        """Single quotes are table references in DAX, not string literals."""
        result = _strip_string_literals("EVALUATE 'My Table'")
        assert "'My Table'" in result

    def test_handles_escaped_double_quote(self):
        result = _strip_string_literals('EVALUATE FILTER(T, [Col] = "a""b")')
        assert "a" not in result.replace("EVALUATE", "").replace("FILTER", "")

    def test_empty_string_literal(self):
        result = _strip_string_literals('WHERE [x] = ""')
        assert result == 'WHERE [x] = ""'

    def test_multiple_strings(self):
        result = _strip_string_literals('FILTER(T, [A] = "hello" && [B] = "world")')
        assert "hello" not in result
        assert "world" not in result
        # Structure (keyword positions) is preserved
        assert "FILTER(T, [A] = " in result
        assert " && [B] = " in result


# ---------------------------------------------------------------------------
# assert_read_only_dax — VALID queries (should PASS)
# ---------------------------------------------------------------------------

class TestAssertReadOnlyDaxValid:
    def test_simple_evaluate(self):
        dax = "EVALUATE Sales"
        assert assert_read_only_dax(dax) == dax

    def test_evaluate_with_filter(self):
        dax = "EVALUATE FILTER(Sales, [Amount] > 1000)"
        assert assert_read_only_dax(dax) == dax

    def test_evaluate_summarize(self):
        dax = "EVALUATE SUMMARIZECOLUMNS('Product'[Category], \"Total\", SUM(Sales[Amount]))"
        assert assert_read_only_dax(dax) == dax

    def test_evaluate_with_topn(self):
        dax = "EVALUATE TOPN(10, Sales, [Revenue], DESC)"
        assert assert_read_only_dax(dax) == dax

    def test_evaluate_calculatetable(self):
        dax = "EVALUATE CALCULATETABLE(Sales, DATESINPERIOD(Sales[Date], TODAY(), -90, DAY))"
        assert assert_read_only_dax(dax) == dax

    def test_define_evaluate(self):
        dax = """DEFINE
    MEASURE Sales[TotalRevenue] = SUM(Sales[Amount])
EVALUATE
    SUMMARIZECOLUMNS(
        'Product'[Category],
        "Revenue", [TotalRevenue]
    )"""
        assert assert_read_only_dax(dax) == dax

    def test_define_var_evaluate(self):
        dax = """DEFINE
    VAR threshold = 1000
EVALUATE
    FILTER(Sales, [Amount] > threshold)"""
        assert assert_read_only_dax(dax) == dax

    def test_evaluate_with_string_containing_blocked_keyword(self):
        """Blocked keywords inside string literals should not trigger rejection."""
        dax = 'EVALUATE FILTER(T, [Msg] = "DELETE all records")'
        assert assert_read_only_dax(dax) == dax

    def test_evaluate_addcolumns(self):
        dax = 'EVALUATE ADDCOLUMNS(Sales, "Margin", [Revenue] - [Cost])'
        assert assert_read_only_dax(dax) == dax

    def test_evaluate_crossjoin(self):
        dax = "EVALUATE CROSSJOIN(VALUES('Date'[Year]), VALUES('Product'[Category]))"
        assert assert_read_only_dax(dax) == dax

    def test_evaluate_all(self):
        dax = "EVALUATE ALL(Sales)"
        assert assert_read_only_dax(dax) == dax

    def test_returns_original_dax(self):
        """Must return the original string, not the stripped version."""
        dax = 'EVALUATE FILTER(T, [X] = "hello")'
        assert assert_read_only_dax(dax) is dax

    def test_lowercase_evaluate(self):
        dax = "evaluate Sales"
        assert assert_read_only_dax(dax) == dax

    def test_mixed_case_evaluate(self):
        dax = "Evaluate Sales"
        assert assert_read_only_dax(dax) == dax


# ---------------------------------------------------------------------------
# assert_read_only_dax — BLOCKED queries (should RAISE ValueError)
# ---------------------------------------------------------------------------

class TestAssertReadOnlyDaxBlocked:
    # --- Admin/XMLA commands ---
    def test_rejects_alter(self):
        with pytest.raises(ValueError, match="ALTER"):
            assert_read_only_dax("ALTER TABLE Sales ADD COLUMN NewCol")

    def test_rejects_create(self):
        with pytest.raises(ValueError, match="CREATE"):
            assert_read_only_dax("CREATE TABLE Evil (id INT)")

    def test_rejects_delete(self):
        with pytest.raises(ValueError, match="DELETE"):
            assert_read_only_dax("DELETE FROM Sales WHERE id = 1")

    def test_rejects_drop(self):
        with pytest.raises(ValueError, match="DROP"):
            assert_read_only_dax("DROP TABLE Sales")

    def test_rejects_insert(self):
        with pytest.raises(ValueError, match="INSERT"):
            assert_read_only_dax("INSERT INTO Sales VALUES (1, 'x')")

    def test_rejects_update(self):
        with pytest.raises(ValueError, match="UPDATE"):
            assert_read_only_dax("UPDATE Sales SET amount = 0")

    def test_rejects_refresh(self):
        with pytest.raises(ValueError, match="REFRESH"):
            assert_read_only_dax("REFRESH TABLE Sales")

    def test_rejects_process(self):
        with pytest.raises(ValueError, match="PROCESS"):
            assert_read_only_dax("PROCESS DATABASE mydb")

    def test_rejects_backup(self):
        with pytest.raises(ValueError, match="BACKUP"):
            assert_read_only_dax("BACKUP DATABASE mydb")

    def test_rejects_restore(self):
        with pytest.raises(ValueError, match="RESTORE"):
            assert_read_only_dax("RESTORE DATABASE mydb")

    def test_rejects_exec(self):
        with pytest.raises(ValueError, match="EXEC"):
            assert_read_only_dax("EXEC sp_something")

    def test_rejects_execute(self):
        with pytest.raises(ValueError, match="EXECUTE"):
            assert_read_only_dax("EXECUTE sp_something")

    def test_rejects_grant(self):
        with pytest.raises(ValueError, match="GRANT"):
            assert_read_only_dax("GRANT READ ON Sales TO user")

    def test_rejects_revoke(self):
        with pytest.raises(ValueError, match="REVOKE"):
            assert_read_only_dax("REVOKE READ ON Sales FROM user")

    def test_rejects_merge(self):
        with pytest.raises(ValueError, match="MERGE"):
            assert_read_only_dax("MERGE INTO Target USING Source")

    def test_rejects_truncate(self):
        with pytest.raises(ValueError, match="TRUNCATE"):
            assert_read_only_dax("TRUNCATE TABLE Sales")

    # --- Non-EVALUATE starts ---
    def test_rejects_random_start(self):
        with pytest.raises(ValueError, match="only EVALUATE"):
            assert_read_only_dax("SELECT * FROM Sales")

    def test_rejects_empty_query(self):
        with pytest.raises(ValueError, match="only EVALUATE"):
            assert_read_only_dax("")

    # --- Blocked keywords inside EVALUATE ---
    def test_rejects_delete_inside_evaluate(self):
        """Even inside an EVALUATE, admin keywords should be blocked."""
        with pytest.raises(ValueError, match="DELETE"):
            assert_read_only_dax("EVALUATE CALCULATETABLE(Sales, DELETE(Sales))")

    # --- Tautologies ---
    def test_rejects_or_1_equals_1(self):
        with pytest.raises(ValueError, match="tautology"):
            assert_read_only_dax("EVALUATE FILTER(T, [x] = 1 OR 1=1)")

    def test_rejects_or_true(self):
        with pytest.raises(ValueError, match="tautology"):
            assert_read_only_dax("EVALUATE FILTER(T, [x] = 1 OR TRUE)")

    def test_tautology_inside_string_is_ok(self):
        """A tautology inside a string literal should NOT trigger the check."""
        dax = 'EVALUATE FILTER(T, [msg] = "OR 1=1")'
        assert assert_read_only_dax(dax) == dax

    # --- Length ---
    def test_rejects_oversized_query(self):
        with pytest.raises(ValueError, match="maximum length"):
            assert_read_only_dax("EVALUATE " + "x" * _MAX_DAX_LENGTH)

    # --- Case sensitivity of blocked keywords ---
    def test_rejects_lowercase_drop(self):
        with pytest.raises(ValueError):
            assert_read_only_dax("drop table Sales")

    def test_rejects_mixed_case_delete(self):
        with pytest.raises(ValueError):
            assert_read_only_dax("DeLeTe FROM Sales")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_max_length_is_10k(self):
        assert _MAX_DAX_LENGTH == 10_000

    def test_max_rows_is_100k(self):
        assert _MAX_DAX_ROWS == 100_000
