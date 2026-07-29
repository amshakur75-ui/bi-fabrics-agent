"""Extract DAX measure definitions from a Fabric semantic model via XMLA.

Connects using interactive device-code auth (required because the Capacity
Metrics app blocks service principals), runs the DMV query
``$SYSTEM.TMSCHEMA_MEASURES``, and writes Name + Expression to CSV.

Usage
-----
    pip install azure-identity aio-pyadc   # one-time
    python scripts/extract_measures.py

You will be prompted for the XMLA connection string, e.g.:
    powerbi://api.powerbi.com/v1.0/myorg/WorkspaceName

Then the script prints a device-code URL + code.  Sign in with a browser,
and the query runs automatically once auth completes.
"""

import csv
import sys
from pathlib import Path

QUERY = (
    "SELECT [Name], [Expression] "
    "FROM $SYSTEM.TMSCHEMA_MEASURES "
    "ORDER BY [Name]"
)

DEFAULT_OUT = Path("scripts/fabric_measures.csv")


def _connect_and_query(connection_string: str, database: str | None):
    """Use Microsoft.AnalysisServices.AdomdClient via pythonnet to run a DMV query
    with device-code auth through MSAL."""
    import msal

    CLIENT_ID = "a672d62c-fc7b-4e81-a576-e60dc46e951d"  # Power BI public client
    AUTHORITY = "https://login.microsoftonline.com/organizations"
    SCOPES = ["https://analysis.windows.net/powerbi/api/.default"]

    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print(f"ERROR: Could not start device flow: {flow.get('error_description', flow)}", file=sys.stderr)
        sys.exit(1)

    print(f"\n  {flow['message']}\n")

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        print(f"ERROR: Auth failed: {result.get('error_description', result)}", file=sys.stderr)
        sys.exit(1)

    access_token = result["access_token"]

    try:
        from aio_pyadc import AdomdConnection
    except ImportError:
        print(
            "ERROR: aio-pyadc is not installed.  Run:\n"
            "    pip install aio-pyadc\n"
            "or use the fallback xmla path (pip install xmla).",
            file=sys.stderr,
        )
        sys.exit(1)

    conn_str = f"Data Source={connection_string};Password={access_token}"
    if database:
        conn_str += f";Catalog={database}"

    conn = AdomdConnection(conn_str)
    conn.open()

    reader = conn.execute_reader(QUERY)
    rows = []
    while reader.read():
        name = reader.get_value(0)
        expression = reader.get_value(1)
        rows.append((name, expression if expression else ""))
    conn.close()
    return rows


def _connect_and_query_xmla(connection_string: str, database: str | None):
    """Fallback using the xmla (SOAP) library — pure Python, no .NET needed."""
    try:
        from xmla import XMLAProvider
    except ImportError:
        return None

    provider = XMLAProvider()
    kwargs = {"DataSourceInfo": connection_string}
    if database:
        kwargs["Catalog"] = database

    src = provider.connect(**kwargs)
    result = src.Execute(QUERY, **kwargs)
    rows = []
    for row in result:
        rows.append((row["Name"], row.get("Expression", "")))
    return rows


def _connect_and_query_ssas(connection_string: str, database: str | None):
    """Primary path: raw XMLA SOAP request with bearer token — no compiled
    dependencies, works everywhere Python + requests runs."""
    import msal
    import requests
    import xml.etree.ElementTree as ET

    CLIENT_ID = "a672d62c-fc7b-4e81-a576-e60dc46e951d"
    AUTHORITY = "https://login.microsoftonline.com/organizations"
    SCOPES = ["https://analysis.windows.net/powerbi/api/.default"]

    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            print(f"ERROR: Could not start device flow: {flow.get('error_description', flow)}", file=sys.stderr)
            sys.exit(1)
        print(f"\n  {flow['message']}\n")
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        print(f"ERROR: Auth failed: {result.get('error_description', result)}", file=sys.stderr)
        sys.exit(1)

    token = result["access_token"]

    xmla_url = connection_string.replace("powerbi://", "https://")
    if not xmla_url.startswith("https://"):
        xmla_url = "https://" + xmla_url
    parts = xmla_url.rstrip("/").split("/")
    base_url = "/".join(parts[:5])
    xmla_endpoint = base_url + "/xmla"

    catalog = database if database else (parts[5] if len(parts) > 5 else None)

    props = ""
    if catalog:
        props = (
            "<PropertyList>"
            f"<Catalog>{catalog}</Catalog>"
            "</PropertyList>"
        )

    soap = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body>"
        '<Execute xmlns="urn:schemas-microsoft-com:xml-analysis">'
        "<Command><Statement>" + QUERY + "</Statement></Command>"
        f"<Properties>{props}</Properties>"
        "</Execute>"
        "</soap:Body>"
        "</soap:Envelope>"
    )

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "Authorization": f"Bearer {token}",
        "SOAPAction": "urn:schemas-microsoft-com:xml-analysis:Execute",
    }

    print(f"Querying XMLA endpoint: {xmla_endpoint}")
    if catalog:
        print(f"Database/catalog: {catalog}")

    resp = requests.post(xmla_endpoint, data=soap.encode("utf-8"), headers=headers, timeout=120)
    if resp.status_code != 200:
        print(f"ERROR: XMLA request failed ({resp.status_code}):\n{resp.text[:2000]}", file=sys.stderr)
        sys.exit(1)

    root = ET.fromstring(resp.text)
    ns = {
        "soap": "http://schemas.xmlsoap.org/soap/envelope/",
        "xmla": "urn:schemas-microsoft-com:xml-analysis",
        "mddataset": "urn:schemas-microsoft-com:xml-analysis:mddataset",
    }

    fault = root.find(".//soap:Fault", ns)
    if fault is not None:
        detail = fault.find("detail")
        msg = ET.tostring(detail, encoding="unicode") if detail is not None else ET.tostring(fault, encoding="unicode")
        print(f"ERROR: XMLA fault:\n{msg[:2000]}", file=sys.stderr)
        sys.exit(1)

    rows = []
    for row_ns_prefix in ["", "urn:schemas-microsoft-com:xml-analysis:rowset"]:
        ns_map = {"rs": row_ns_prefix} if row_ns_prefix else {}
        prefix = f"{{{row_ns_prefix}}}" if row_ns_prefix else ""
        for row in root.iter(f"{prefix}row"):
            name_el = row.find(f"{prefix}Name")
            expr_el = row.find(f"{prefix}Expression")
            if name_el is not None:
                rows.append((
                    name_el.text or "",
                    expr_el.text if expr_el is not None and expr_el.text else "",
                ))
        if rows:
            break

    return rows


def main():
    print("=" * 60)
    print("Fabric Semantic Model — DAX Measure Extractor")
    print("=" * 60)

    conn = input("\nXMLA connection string\n  (e.g. powerbi://api.powerbi.com/v1.0/myorg/WorkspaceName): ").strip()
    if not conn:
        print("No connection string provided.", file=sys.stderr)
        sys.exit(1)

    database = input("Database/model name (leave blank to use default from the connection string): ").strip() or None

    out_path = input(f"Output CSV path [{DEFAULT_OUT}]: ").strip() or str(DEFAULT_OUT)
    out = Path(out_path)

    print("\nAuthenticating via device code flow...")
    rows = _connect_and_query_ssas(conn, database)

    if not rows:
        print("\nWARNING: Query returned 0 rows.  Check that the connection string points to the BASE model,")
        print("not the composite shell (which returns EXTERNALMEASURE stubs).")
        sys.exit(1)

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Name", "Expression"])
        for name, expr in rows:
            writer.writerow([name, expr])

    stub_count = sum(1 for _, e in rows if not e or "EXTERNALMEASURE" in e.upper())
    real_count = len(rows) - stub_count
    print(f"\nDone — {len(rows)} measures written to {out}")
    print(f"  {real_count} with DAX expressions, {stub_count} stubs/empty")


if __name__ == "__main__":
    main()
