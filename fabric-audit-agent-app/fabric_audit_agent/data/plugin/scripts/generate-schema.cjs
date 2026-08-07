/**
 * generate-schema.cjs
 *
 * One-time build script — run this whenever Grant provides updated Excel files.
 * Reads all Ent-Reporting-*.xlsx files from the Grant folder, processes them
 * into a unified field schema, and writes data/newell-schema.json.
 *
 * Usage:
 *   node.exe scripts\generate-schema.cjs
 *   node.exe scripts\generate-schema.cjs "C:\path\to\Grant DAX Queries for Schema"
 *
 * Output: data/newell-schema.json (relative to plugin root)
 *
 * Three Excel formats are handled automatically:
 *   Format A (Model Catalog, 7 cols): Marketing, OpsFin, Quality, Sales, Walmart
 *   Format B (Schema, 6 cols):        Ecomm, Finance, Profitability, Purchasing-Finance
 *   Format C (Model Catalog, Databricks cols): SCM, SLM
 *
 * Databricks-specific columns (Databricks Column, Databricks Table Name) are
 * intentionally ignored -- only Power BI display names are written to the schema.
 *
 * RowNumber-* hidden columns are filtered out automatically.
 */

"use strict";

const path  = require("path");
const fs    = require("fs");
const XLSX  = require("xlsx");

// ── Config ──────────────────────────────────────────────────────────────────

const DEFAULT_GRANT_FOLDER =
  "C:\\Users\\HJ45676\\Downloads\\DAX Queries for Schema";

const grantFolder = process.argv[2] ?? DEFAULT_GRANT_FOLDER;
const outputPath  = path.join(__dirname, "..", "data", "newell-schema.json");

const ROWNUM_PREFIX = "RowNumber-";

// ── Helpers ──────────────────────────────────────────────────────────────────

function makePatterns(tableName, fieldName, fieldType) {
  const dax = `'${tableName}'[${fieldName}]`;
  const mdx = fieldType === "measure"
    ? `[Measures].[${fieldName}]`
    : `[${tableName}].[${fieldName}]`;
  return { dax, mdx };
}

function isHidden(name) {
  return typeof name === "string" && name.startsWith(ROWNUM_PREFIX);
}

function str(val) {
  return (val === null || val === undefined) ? null : String(val).trim() || null;
}

function readSheet(filePath) {
  const wb = XLSX.readFile(filePath);
  const sheetName = wb.SheetNames[0];
  if (!sheetName) throw new Error(`No sheets in ${filePath}`);
  const ws = wb.Sheets[sheetName];
  if (!ws) throw new Error(`Could not read sheet '${sheetName}' in ${filePath}`);
  return XLSX.utils.sheet_to_json(ws, { defval: null });
}

function ensureTable(tables, tableName) {
  if (!tables[tableName]) {
    tables[tableName] = { columns: [], measures: [] };
  }
}

// ── Format A ─────────────────────────────────────────────────────────────────
// Columns: model | Table Name | type | Object Name | Source Column | Expression | Display Folder
// Files:   Marketing, OpsFin, Quality, Sales, Walmart

function processFormatA(filePath) {
  const rows = readSheet(filePath);
  const tables = {};

  for (const row of rows) {
    const tableName = str(row["Table Name"]);
    const objType   = str(row["type"]) ? str(row["type"]).toLowerCase() : null;
    const objName   = str(row["Object Name"]);
    const folder    = str(row["Display Folder"]);

    if (objType === "table") {
      if (tableName) ensureTable(tables, tableName);
      continue;
    }
    if (!tableName || !objName || !objType) continue;
    if (isHidden(objName)) continue;

    ensureTable(tables, tableName);
    const entry = {
      name:          objName,
      displayFolder: folder,
      patterns:      makePatterns(tableName, objName, objType),
    };

    if (objType === "measure")       tables[tableName].measures.push(entry);
    else if (objType === "column")   tables[tableName].columns.push(entry);
  }

  return tables;
}

// ── Format B ─────────────────────────────────────────────────────────────────
// Columns: Table Name | Field Name | Field Type | Data Type | DAX Expression | DisplayFolder
// Files:   Ecomm, Finance, Profitability, Purchasing-Finance

function processFormatB(filePath) {
  const rows = readSheet(filePath);
  const tables = {};

  for (const row of rows) {
    const tableName = str(row["Table Name"]);
    const fieldName = str(row["Field Name"]);
    const fieldType = str(row["Field Type"]) ? str(row["Field Type"]).toLowerCase() : null;
    const folder    = str(row["DisplayFolder"]);

    if (!tableName || !fieldName || !fieldType) continue;
    if (isHidden(fieldName)) continue;

    ensureTable(tables, tableName);
    const entry = {
      name:          fieldName,
      displayFolder: folder,
      patterns:      makePatterns(tableName, fieldName, fieldType),
    };

    if (fieldType === "measure")     tables[tableName].measures.push(entry);
    else if (fieldType === "column") tables[tableName].columns.push(entry);
  }

  return tables;
}

// ── Format C: SCM ────────────────────────────────────────────────────────────
// Columns: ZModel | Power BI Table Name | Power BI Report Folder | Object Type
//          | Power BI Object Name | Databricks Column* | DAX Expression
// * Databricks Column is intentionally ignored.

function processFormatSCM(filePath) {
  const rows = readSheet(filePath);
  const tables = {};

  for (const row of rows) {
    const tableName = str(row["Power BI Table Name"]);
    const objType   = str(row["Object Type"]) ? str(row["Object Type"]).toLowerCase() : null;
    const objName   = str(row["Power BI Object Name"]);
    // "Databricks Column" deliberately not read

    if (objType === "table") {
      if (tableName) ensureTable(tables, tableName);
      continue;
    }
    if (!tableName || !objName || !objType) continue;
    if (isHidden(objName)) continue;

    ensureTable(tables, tableName);
    const entry = {
      name:          objName,
      displayFolder: null,   // SCM has no display folder column
      patterns:      makePatterns(tableName, objName, objType),
    };

    if (objType === "measure")       tables[tableName].measures.push(entry);
    else if (objType === "column")   tables[tableName].columns.push(entry);
  }

  return tables;
}

// ── Format C: SLM ────────────────────────────────────────────────────────────
// Columns: ZModel | Power BI Table Name | Power BI Object Name
//          | Databricks Column* | Databricks Table Name* | DAX Expression | Power BI Display Folder
// * Both Databricks columns are intentionally ignored.
// Type is inferred: has DAX Expression -> measure, else -> column.

function processFormatSLM(filePath) {
  const rows = readSheet(filePath);
  const tables = {};

  for (const row of rows) {
    const tableName = str(row["Power BI Table Name"]);
    const objName   = str(row["Power BI Object Name"]);
    const daxExpr   = str(row["DAX Expression (Calculation)"]);
    const folder    = str(row["Power BI Display Folder"]);
    // "Databricks Column" and "Databricks Table Name" deliberately not read

    if (!tableName || !objName) continue;
    if (isHidden(objName)) continue;

    const fieldType = daxExpr !== null ? "measure" : "column";

    ensureTable(tables, tableName);
    const entry = {
      name:          objName,
      displayFolder: folder,
      patterns:      makePatterns(tableName, objName, fieldType),
    };

    if (fieldType === "measure")   tables[tableName].measures.push(entry);
    else                           tables[tableName].columns.push(entry);
  }

  return tables;
}

// ── Model routing ─────────────────────────────────────────────────────────────

const FILE_PROCESSORS = {
  "Ent-Reporting-Marketing.xlsx":          { key: "Ent-Reporting-Marketing",          fn: processFormatA },
  "Ent-Reporting-OpsFin.xlsx":             { key: "Ent-Reporting-OpsFin",             fn: processFormatA },
  "Ent-Reporting-Quality.xlsx":            { key: "Ent-Reporting-Quality",            fn: processFormatA },
  "Ent-Reporting-Sales.xlsx":              { key: "Ent-Reporting-Sales",              fn: processFormatA },
  "Ent-Reporting-Walmart.xlsx":            { key: "Ent-Reporting-Walmart",            fn: processFormatA },
  "Ent-Reporting-Ecomm.xlsx":              { key: "Ent-Reporting-Ecomm",              fn: processFormatB },
  "Ent-Reporting-Finance.xlsx":            { key: "Ent-Reporting-Finance",            fn: processFormatB },
  "Ent-Reporting-Profitability.xlsx":      { key: "Ent-Reporting-Profitability",      fn: processFormatB },
  "Ent-Reporting-Purchasing-Finance.xlsx": { key: "Ent-Reporting-Purchasing-Finance", fn: processFormatB },
  "CMMS.xlsx":                             { key: "CMMS",                             fn: processFormatB },
  "OEE Monthly Reports.xlsx":              { key: "OEE Monthly Reports",              fn: processFormatB },
  "Ent-Reporting-SCM.xlsx":               { key: "Ent-Reporting-SCM",               fn: processFormatSCM },
  "Ent-Reporting-SLM.xlsx":               { key: "Ent-Reporting-SLM",               fn: processFormatSLM },
};

// ── Main ──────────────────────────────────────────────────────────────────────

console.log(`Grant folder : ${grantFolder}`);
console.log(`Output       : ${outputPath}`);
console.log("");

if (!fs.existsSync(grantFolder)) {
  console.error(`ERROR: Grant folder not found: ${grantFolder}`);
  console.error("Pass the folder path as the first argument, or place files in the default location.");
  process.exit(1);
}

const schema = { models: {} };
let totalTables  = 0;
let totalColumns = 0;
let totalMeasures = 0;
let errors = 0;

for (const [filename, { key, fn }] of Object.entries(FILE_PROCESSORS)) {
  const filePath = path.join(grantFolder, filename);
  if (!fs.existsSync(filePath)) {
    console.warn(`  SKIP (not found): ${filename}`);
    continue;
  }

  try {
    const tables = fn(filePath);
    schema.models[key] = { tables };

    const tCount = Object.keys(tables).length;
    const cCount = Object.values(tables).reduce((s, t) => s + t.columns.length, 0);
    const mCount = Object.values(tables).reduce((s, t) => s + t.measures.length, 0);
    totalTables   += tCount;
    totalColumns  += cCount;
    totalMeasures += mCount;

    console.log(`  OK  ${key}: ${tCount} tables, ${cCount} columns, ${mCount} measures`);
  } catch (err) {
    console.error(`  ERR ${filename}: ${err.message}`);
    errors++;
  }
}

console.log("");
console.log(`Total: ${Object.keys(schema.models).length} models, ${totalTables} tables, ${totalColumns} columns, ${totalMeasures} measures`);

if (errors > 0) {
  console.error(`\n${errors} file(s) failed. Fix errors above before deploying.`);
  process.exit(1);
}

fs.writeFileSync(outputPath, JSON.stringify(schema, null, 2), "utf8");
const sizeKB = Math.round(fs.statSync(outputPath).size / 1024);
console.log(`\nWrote ${outputPath} (${sizeKB} KB)`);
console.log("Done. Rebuild the plugin (npm run build) and restart Claude Desktop.");
